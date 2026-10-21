"""Locally verifiable feedback. Never infers a splice, endpoint or target count."""
from collections import Counter
from decimal import Decimal
import re
from .profiles import section
from .member_recognition import normalize_view_tag


LEVEL_REFERENCE = re.compile(r'(?<![\w])EL\.?\s*([+-]?\d+(?:\.\d+)?)(?:\s*\(\s*([A-Za-z][\w -]*)\s*\))?', re.I)
ALIGNMENT_REFERENCE = re.compile(r'(?<![\w/.-])([\w.-]+/(?:[A-Za-z]{1,4}|\d{1,4}))(?![\w/-])')


def reported_references(text):
    """Names already asserted by the model, never geometry inferred from pixels."""
    result={}
    for match in LEVEL_REFERENCE.finditer(text):
        name=normalize_view_tag(match.group(0))
        # Missing datum suffix can still identify an omitted level tag; explicit
        # TOS vs BOB are different references and must not be equated.
        key=('LEVEL',Decimal(match[1]),(match[2] or '').upper())
        result[key]=name
    for match in ALIGNMENT_REFERENCE.finditer(text):
        name=normalize_view_tag(match[1])
        result[('ALIGNMENT',name)]=name
    return result


def endpoint_tag_difference(row,source_plane):
    """Report an output contradiction, not proof of the member's physical plane."""
    endpoints=row.get('endpoints')
    if not isinstance(endpoints,list) or len(endpoints)!=2 or not all(isinstance(e,str) for e in endpoints):
        return None
    first,second=(reported_references(e) for e in endpoints)
    shared=set(first)&set(second)
    source=set(reported_references(source_plane))
    shared-=source
    if len(shared)!=1:return None  # Ambiguous description: do not guess a tag.
    reference=next(iter(shared))
    tags={key for tag in row.get('view_tags',[]) for key in reported_references(tag)}
    def same_name(a,b):
        return a==b or (a[0]==b[0]=='LEVEL' and a[1]==b[1] and (not a[2] or not b[2]))
    if any(same_name(reference,tag) for tag in tags):return None
    return {'shared_endpoint_reference':first[reference], 'view_tags':row.get('view_tags',[]),
            'endpoints':endpoints}


def assess_master(payload,children,tiles,*,legacy=False):
    candidates={c['candidate_id']:c for child in children for c in child['observations']}
    regions={tile['id']:tile for tile in tiles}
    feedback=[]

    def add(check,message,*,row_index=None,ids=(),observed=None):
        ids=list(dict.fromkeys(ids))
        feedback.append({'check':check,'row_index':row_index,'candidate_ids':ids,
            'tile_ids':sorted({candidates[i]['source_tile'] for i in ids if i in candidates}),
            'observed':observed,'message':message})

    if not isinstance(payload,dict) or not isinstance(payload.get('members'),list) or not isinstance(payload.get('decisions'),list):
        add('invalid_structure','回复必须包含 members 和 decisions 数组。')
        return {'valid':False,'feedback':feedback}
    members=payload['members'];decisions=payload['decisions']
    if any(not isinstance(d,dict) for d in decisions) or any(not isinstance(m,dict) for m in members):
        add('invalid_structure','members 和 decisions 的每个元素必须是对象。')
        return {'valid':False,'feedback':feedback}
    seen=Counter(d.get('candidate_id') for d in decisions)
    missing=set(candidates)-set(seen);unknown=set(seen)-set(candidates)
    if missing or unknown or any(n!=1 for n in seen.values()):
        add('observation_coverage','每条子观察必须出现一次；不能遗漏、编造或重复处理 ID。',ids=sorted(missing),
            observed={'missing':sorted(missing),'unknown':[str(i) for i in unknown],'repeated':[i for i,n in seen.items() if n>1]})
    links={i:[] for i in range(len(members))};independent=Counter();lookup={d.get('candidate_id'):d for d in decisions}
    for d in decisions:
        identity=d.get('candidate_id');index=d.get('member_index');action=d.get('action')
        if not legacy and action!='DUPLICATE' and d.get('duplicate_of') is not None:
            add('unexpected_duplicate_reference','非 DUPLICATE 的 duplicate_of 必须为 null。',ids=[identity])
        if action=='NOT_MEMBER':
            if index is not None:add('non_member_target','NOT_MEMBER 不应关联台账行。',ids=[identity])
            continue
        if type(index) is not int or index not in links:
            add('invalid_member_index','观察必须指向有效的台账行。',ids=[identity]);continue
        links[index].append(identity)
        if action in ('INCLUDED','UNRESOLVED'):independent[index]+=1
        if action=='UNRESOLVED' and not members[index].get('issue'):
            add('missing_issue','未解决观察必须保留 issue。',row_index=index,ids=[identity])
        if action=='DUPLICATE' and not legacy:
            root=d.get('duplicate_of');primary=lookup.get(root,{})
            if root==identity or root not in candidates or primary.get('action') not in ('INCLUDED','UNRESOLVED') or primary.get('member_index')!=index:
                add('duplicate_reference','DUPLICATE 必须指向同一行中有效的 INCLUDED/UNRESOLVED 根观察，不可指向自身或形成重复链。',row_index=index,ids=[identity,root]);continue
            if identity not in candidates:continue
            a=candidates[identity];b=candidates[root]
            if a['source_tile']==b['source_tile'] or section(a['spec'])!=section(b['spec']):
                add('duplicate_identity','同一裁片的不同标签或不同规格不能作为裁片重叠重复删除。',row_index=index,ids=[identity,root]);continue
            ac=regions[a['source_tile']]['crop_bbox_px'];bc=regions[b['source_tile']]['crop_bbox_px']
            overlap=[max(ac[0],bc[0]),max(ac[1],bc[1]),min(ac[2],bc[2]),min(ac[3],bc[3])]
            def inside(c):
                x,y=c['label_point_original_px']
                return overlap[0]<=x<=overlap[2] and overlap[1]<=y<=overlap[3]
            if overlap[0]>=overlap[2] or overlap[1]>=overlap[3] or not inside(a) or not inside(b):
                add('duplicate_outside_overlap','两个标签中心不同时位于两裁片的实际重叠区域，不能认定是裁片重复。重叠范围仅说明可能重复，仍须看图核对同一标签。',row_index=index,ids=[identity,root])
    for index,row in enumerate(members):
        ids=links[index];refs=row.get('candidate_ids',[]);count=row.get('member_cnt',1)
        if not isinstance(refs,list) or len(refs)!=len(set(refs)) or set(refs)!=set(ids):
            add('source_references','candidate_ids 与处理记录不一致。',row_index=index,ids=ids)
        if legacy:
            if type(count) is not int or independent[index]>count:
                add('label_member_count','独立标签观察数超过输出根数；请结合相关裁片逐标签确认独立构件及各自长度，不能只增 member_cnt 或复制原总长。',row_index=index,ids=ids,
                    observed={'independent_label_count':independent[index],'member_cnt':count,'length':row.get('length')})
            continue
        if count!=1 or isinstance(count,bool):
            add('one_instance_per_row','汇总回复每行只能是一根独立构件，member_cnt 由程序固定为 1；同 key 的数量由本地随后汇总。',row_index=index,ids=ids)
        if refs and independent[index]!=1:
            add('one_label_per_instance','一行必须且只能关联一个 INCLUDED/UNRESOLVED 根标签；其余只能是有明确根引用的重叠重复。不能合并不同位置标签，也不能仅把 member_cnt 改大。',row_index=index,ids=ids,
                observed={'independent_label_count':independent[index],'member_cnt':count})
        if not refs and row.get('original_label_point') is None:
            add('missing_original_label','主代理补入的构件须给出原图标签中心位置，不允许无标签依据地增根。',row_index=index)
        difference=endpoint_tag_difference(row,(payload.get('view') or {}).get('plane_name',''))
        if difference:
            add('endpoint_view_tag_consistency','你返回的两个端点均引用同一轴面，但 view_tags 未包含它。请结合图片核对是否遗漏 EL 标高面或 Alignment 轴面，并修正标签或不准确的端点描述。此反馈只检查回复字段之间的矛盾，不证明图中实际归属，不能为通过检查盲目补标签。',
                row_index=index,ids=ids,observed=difference)
        length=row.get('length') or {};method=length.get('method');status=row.get('segmentation_status')
        if status=='UNRESOLVED':
            if method!='UNKNOWN' or any(length.get(k) is not None for k in ('direct_mm','horizontal_mm','vertical_mm')) or not row.get('issue'):
                add('unresolved_segmentation_length','首尾或拼接仍未解决时，长度必须 UNKNOWN、三个数值均 null，并保留 issue；不得填整跨或拼接前总长。',row_index=index,ids=ids)
        elif status!='CONFIRMED':
            add('missing_segmentation_status','必须明确返回 CONFIRMED 或 UNRESOLVED 的首尾/分段状态。',row_index=index,ids=ids)
        if method in ('DIRECT','DIAGONAL'):
            if not isinstance(length.get('evidence'),str) or not length['evidence'].strip():
                add('missing_length_evidence','确定长度必须说明图中标尺或尺寸链依据。',row_index=index,ids=ids)
            endpoints=row.get('endpoints')
            if not isinstance(endpoints,list) or len(endpoints)!=2 or any(not isinstance(e,str) or not e.strip() for e in endpoints) or endpoints[0].strip()==endpoints[1].strip():
                add('missing_endpoint_evidence','确定长度必须给出两个不同真实端点的图纸描述；裁片边界不算端点。',row_index=index,ids=ids)
        def positive(key):return type(length.get(key)) in (int,float) and length[key]>0
        consistent=(method=='DIRECT' and positive('direct_mm') and length.get('horizontal_mm') is None and length.get('vertical_mm') is None
            or method=='DIAGONAL' and positive('horizontal_mm') and positive('vertical_mm') and length.get('direct_mm') is None
            or method=='UNKNOWN' and all(length.get(k) is None for k in ('direct_mm','horizontal_mm','vertical_mm')))
        if not consistent:add('length_fields','长度 method 与数值字段不一致。',row_index=index,ids=ids)
    return {'valid':not feedback,'feedback':feedback,'independent_observations':sum(independent.values())}

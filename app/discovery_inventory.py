"""Stage-one catalog, derived from native labels and retained view identities."""
import re
from collections import defaultdict
from .native_pdf import inside

PROFILE = re.compile(
    r'(?<![\w])(?:(?P<assembly>\d+(?:LF|F|L)\s*[- ]\s*)?'
    r'(?P<angle>L\d+(?:\.\d+)?\s*[X*×]\s*\d+(?:\.\d+)?(?:\s*[X*×]\s*\d+(?:\.\d+)?)?)'
    r'|(?P<rolled>HE[ABM]\d+|IPET?\d+|IPN\d+|UP[NE]\d+)'
    r'|(?P<hollow>(?:RHS|SHS|CHS)\s*\d+(?:\.\d+)?(?:\s*[X*×]\s*\d+(?:\.\d+)?){1,2}))'
    r'(?![\w])',re.I)


def labels(text):
    for match in PROFILE.finditer(text):
        profile=match['angle'] or match['rolled'] or match['hollow']
        profile=re.sub(r'\s+','',profile.upper()).replace('X','*').replace('×','*')
        count=re.match(r'\d+',match['assembly'] or '')
        yield profile,int(count[0]) if count else 1


def build_inventory(data,pages):
    views=data.get('views',[])
    entries={}
    page_by_id={p['id']:p for p in pages}
    for page in pages:
        for item in page['texts']+[m for m in data.get('manual_evidence',[]) if m['page_id']==page['id']]:
            # Separate a catalog from physical piece identification: labels may be
            # found even if the model missed a view/member. Scope is always visible.
            for profile,components in labels(item['text']):
                entry=entries.setdefault(profile,{'profile':profile,'evidence':[],'view_ids':[],'component_counts':[]})
                associated=[v['id'] for v in views if v['page_id']==page['id'] and v['kind']!='ISOMETRIC'
                            and inside(item,v['bbox'])]
                entry['evidence'].append({'id':item['id'],'page_id':page['id'],'file':page['file'],
                    'page':page['page'],'text':item['text'],'bbox':item['bbox'],'view_ids':associated,
                    'kind':item.get('kind','TEXT')})
                entry['view_ids']=sorted(set(entry['view_ids']+associated))
                entry['component_counts']=sorted(set(entry['component_counts']+[components]))
    candidates=set()
    for observation in data.get('observations',[]):
        candidates.update(p for p,_ in labels(observation['profile']))
    groups=defaultdict(list)
    covered=set()
    for page in pages:
        for title in page.get('titles',[]):
            matched=next((v for v in views if v['page_id']==page['id'] and v['kind']==title['kind']
                          and (v['title']==title['title'] or title['evidence_id'] in v['evidence_ids']
                               or title['kind']=='ELEVATION' and v.get('fixed_label')==title['alignment']
                               or title['kind']=='PLAN' and v.get('elevation_m')==title['elevation_m']
                                  and v.get('elevation_basis')==title['elevation_basis'])),None)
            if matched:covered.add(matched['id'])
            groups[title['kind']].append({'id':matched['id'] if matched else None,'page_id':page['id'],
                'title':title['title'],'kind':title['kind'],'elevation_m':title['elevation_m'],
                'elevation_basis':title['elevation_basis'],'fixed_label':title['alignment'],
                'evidence_ids':[title['evidence_id']],'file':page['file'],'page':page['page'],
                'region_identified':matched is not None and matched.get('region_confirmed',True)})
    for view in views:
        if view['id'] in covered:continue
        page=page_by_id.get(view['page_id'],{})
        groups[view['kind']].append({key:view.get(key) for key in
            ('id','page_id','title','kind','fixed_axis','fixed_label','elevation_m','elevation_basis','evidence_ids')}
            | {'file':page.get('file'),'page':page.get('page'),'region_identified':True})
    return {'profile_count':len(entries),'profiles':[entries[p] for p in sorted(entries)],
            'unverified_profiles':sorted(candidates-set(entries)),
            'view_counts':{kind:len(groups[kind]) for kind in ('PLAN','ELEVATION','ISOMETRIC')},
            'views':dict(groups),'count_basis':'原生文字中的截面规格去重；组合前缀、连接后缀和长度不另算规格。',
            'scope':'图纸标注目录，不代表构件数量；未关联视图的标签仍保留原页位置供核对。'}

"""Audited allocation of view observations to physical member groups.

Each confirmed group consumes a stated quantity from every supporting view.
No XYZ coordinates or instance correspondence are invented from group counts.
"""
from collections import Counter
import hashlib
import json

from pydantic import Field, StrictInt, field_validator

from .domain import Contract
from .geometry import section
from .group_dedup import build_group_review, expand_confirmed_groups
from .member_recognition import normalize_view_tags, view_tag


def source_hash(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,
                                    separators=(',',':')).encode()).hexdigest()


class Allocation(Contract):
    row_id: str = Field(min_length=1)
    member_cnt: StrictInt = Field(gt=0, le=10000)


class Resolution(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')
    spec: str = Field(min_length=1,max_length=120)
    view_tags: list[str] = Field(min_length=1)
    length_mm: float = Field(gt=0)
    member_cnt: StrictInt = Field(gt=0,le=10000)
    allocations: list[Allocation] = Field(min_length=1)
    reason: str = Field(min_length=1,max_length=8000)

    @field_validator('view_tags')
    @classmethod
    def normalize_tags(cls,value):
        result=normalize_view_tags(value)
        if not result:raise ValueError('结果组必须填写所属面名称')
        return result


class Exclusion(Allocation):
    reason: str = Field(min_length=1,max_length=8000)


class GroupStage(Contract):
    reconciliation_mode: str = 'group_ledger'
    source_hash: str
    resolutions: list[Resolution] = Field(default_factory=list)
    exclusions: list[Exclusion] = Field(default_factory=list)
    review: dict = Field(default_factory=dict)
    confirmed_groups: list[dict] = Field(default_factory=list)
    members: list[dict] = Field(default_factory=list)
    summary: list[dict] = Field(default_factory=list)
    exceptions: list[dict] = Field(default_factory=list)
    totals: dict = Field(default_factory=dict)


def reconcile(data,draft=None):
    """Rebuild derived results; reject stale inputs and any double consumption."""
    digest=source_hash(data)
    saved=GroupStage.model_validate(draft or {'source_hash':digest}).model_dump()
    if saved['reconciliation_mode']!='group_ledger':raise ValueError('第三阶段模式不匹配')
    if saved['source_hash']!=digest:raise ValueError('第二阶段内容已改变，请重新生成第三阶段后再核验')
    review=build_group_review(data)
    rows={r['id']:r for r in data['view_members']}
    invalid={r['row_id'] for r in review['invalid_rows']}
    views={v['id']:v for v in data['views']}
    known=set(normalize_view_tags(a['name'] for a in data.get('axis_catalog',{}).get('axes',[])))
    known.update(view_tag(v) for v in views.values() if v['kind']!='ISOMETRIC')
    profiles={section(p)[0] for p in data.get('profile_catalog',[])}
    used=Counter();groups=[];members=[];ids=set();exclusions=[]
    def consume(row_id,count):
        if row_id not in rows or row_id in invalid:raise ValueError(f'无效的观察行：{row_id}')
        used[row_id]+=count
        if used[row_id]>rows[row_id]['member_cnt']:
            raise ValueError(f'{row_id} 分配或排除数量超过原观察数量，禁止重复扣减')
    for resolution in saved['resolutions']:
        identity=resolution['id']
        if identity in ids:raise ValueError('结果组ID不可重复')
        ids.add(identity)
        profile,multiplier=section(resolution['spec'])
        if profile not in profiles:raise ValueError(f'{profile} 不在第一阶段规格目录，请先核对上游目录')
        if not 1<=multiplier<=8:raise ValueError('无效的组合组件数量')
        if set(resolution['view_tags'])-known:raise ValueError('结果组含目录外的面名称')
        by_source=Counter();assigned=set()
        for allocation in resolution['allocations']:
            row_id=allocation['row_id'];count=allocation['member_cnt']
            if row_id in assigned:raise ValueError('同一结果组内不能重复填写观察行')
            assigned.add(row_id);consume(row_id,count)
            by_source[rows[row_id]['source_view']]+=count
        if any(count!=resolution['member_cnt'] for count in by_source.values()):
            raise ValueError('每个来源分配给结果组的数量必须等于该组数量；部分重叠请拆组')
        # A source must contain this member; conflicting original labels may be
        # corrected explicitly, with the original row retained in the audit.
        if any(view_tag(views[s]) not in resolution['view_tags'] for s in by_source):
            raise ValueError('结果组所属面必须包含每个来源视图的面名称')
        group={**resolution,'profile':profile,'component_multiplier':multiplier,
               'confirmed':True,'confirmed_member_cnt':resolution['member_cnt'],
               'source_counts':dict(by_source)}
        groups.append(group)
        for index in range(resolution['member_cnt']):
            assembly_id=f'{identity}-a{index+1}'
            for component in range(multiplier):
                members.append({'id':f'{assembly_id}-c{component+1}','assembly_id':assembly_id,
                    'component_index':component+1,'group_id':identity,'profile':profile,
                    'length_mm':resolution['length_mm'],'view_tags':resolution['view_tags'],
                    'observation_ids':sorted(assigned),'provenance_scope':'group',
                    'reason':resolution['reason']})
    for exclusion in saved['exclusions']:
        consume(exclusion['row_id'],exclusion['member_cnt']);exclusions.append(exclusion)
    exceptions=[]
    for row_id,row in rows.items():
        if row_id in invalid:
            reason=next(r['reason'] for r in review['invalid_rows'] if r['row_id']==row_id)
            exceptions.append({'observation_id':row_id,'member_cnt':row.get('member_cnt'),
                               'issues':[f'无效观察：{reason}']})
        elif remaining:=row['member_cnt']-used[row_id]:
            exceptions.append({'observation_id':row_id,'member_cnt':remaining,
                'issues':['尚未分配到已确认结果组或明确排除；不计入最终数量']})
    for pending in review['pending_sources']:
        exceptions.append({'observation_id':pending['view_id'],'member_cnt':None,
                           'issues':[f"来源视图未完成识别：{pending['title']} ({pending['status']})"]})
    for analysis in data.get('view_analysis',[]):
        if analysis.get('status')!='completed':continue
        labels=analysis.get('label_count')
        if analysis.get('coverage_differences') or (labels is not None and labels!=analysis.get('member_cnt')):
            exceptions.append({'observation_id':analysis['view_id'],'member_cnt':None,
                'issues':['规格标签与构件观察数量不一致，请返回第二阶段核对；已分配的观察不能消除此覆盖疑点']})
    return {'reconciliation_mode':'group_ledger','source_hash':digest,
        'resolutions':saved['resolutions'],'exclusions':exclusions,'review':review,
        'confirmed_groups':groups,'members':members,'summary':expand_confirmed_groups(groups),
        'exceptions':exceptions,'totals':{'observation_units':review['summary']['valid_observation_units'],
            'allocated_observation_units':sum(used.values())-sum(e['member_cnt'] for e in exclusions),
            'excluded_observation_units':sum(e['member_cnt'] for e in exclusions),
            'pending_observation_units':sum(e['member_cnt'] or 0 for e in exceptions),
            'confirmed_assemblies':sum(g['member_cnt'] for g in groups),
            'confirmed_single_members':len(members),'confirmed_groups':len(groups),
            'pending_source_views':len(review['pending_sources'])}}


def export_sheets(two,three):
    """Same export writer and formula escaping as the coordinate workflow."""
    return [
        ('Quantity Length Summary',['规格','名义长度 mm','单根数量','总长度 mm'],
         [[s['profile'],s['length_mm'],s['quantity'],s['total_length_mm']] for s in three['summary']]),
        ('Member Register',['单根 ID','组合 ID','组件序号','规格','名义长度 mm','所属面','结果组','来源观察（组级）','确认依据'],
         [[m['id'],m['assembly_id'],m['component_index'],m['profile'],m['length_mm'],m['view_tags'],m['group_id'],m['observation_ids'],m['reason']] for m in three['members']]),
        ('Group Allocations',['结果组','规格','所属面','长度 mm','组数量','来源分配','核验依据'],
         [[g['id'],g['spec'],g['view_tags'],g['length_mm'],g['member_cnt'],g['allocations'],g['reason']] for g in three['confirmed_groups']]),
        ('Observation Audit',['观察 ID','来源视图','原规格','原所属面','原数量','原长度 mm','位置','模型疑点','程序检查'],
         [[r['id'],r['source_view'],r['spec'],r['view_tags'],r['member_cnt'],r['length_mm'],r['location'],r.get('issue'),r.get('checks',[])] for r in two['view_members']]),
        ('Excluded Observations',['观察 ID','排除数量','排除依据'],
         [[r['row_id'],r['member_cnt'],r['reason']] for r in three['exclusions']]),
        ('Exceptions',['观察或视图 ID','待确认数量','问题'],
         [[r['observation_id'],r['member_cnt'],r['issues']] for r in three['exceptions']]),
        ('Method Notes',['类型','说明'],[
            ['长度口径','图示参考线名义段长；不包含下料扣减。'],
            ['数量口径','确认组按来源分配；组合钢材只展开一次。待确认项不计入汇总。'],
            ['可追溯范围','逐根编号为组内序号，观察关联保留到组级，不能据此推断精确实体位置。'],
            ['第二阶段指纹',three['source_hash']]])]

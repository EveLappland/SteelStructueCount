"""Per-view takeoff scope. Names, files, axes and elevations are never hardcoded."""
import re
from collections import Counter
from .native_pdf import inside
from .discovery_inventory import labels


def role(view):
    if view.get('kind')=='ISOMETRIC':
        return 'EXCLUDE' if view.get('takeoff_role')=='EXCLUDE' else 'REFERENCE'
    return view.get('takeoff_role','MEMBERS')


def eligible(view):
    return role(view)=='MEMBERS'


def apply_filter(data,pages,audit=None):
    pages={p['id']:p for p in pages};counts=Counter();catalog=set();items=[]
    data['warnings']=[w for w in data.get('warnings',[]) if not w.startswith('用途待确认：')]
    for view in data['views']:
        purpose=role(view);reason=view.get('filter_reason','').strip()
        if not view.get('filter_reviewed'):
            # Strong independent detail titles are safe. A BOB level, a few
            # labels, filename, or another view's plate note is never sufficient.
            detail=re.match(r'^(?:BASE\s+PLATE\s+DETAIL|ANCHOR\s+(?:BOLT\s+)?DETAIL|CONNECTION\s+DETAIL|BOLT\s+SCHEDULE|底板详图|连接详图|螺栓详图)',view['title'],re.I)
            if detail:purpose='EXCLUDE';reason='独立标题明确表明底板、锚栓或连接详图，不统计为线性型钢构件。'
        if view['kind']=='ISOMETRIC' and (purpose=='REFERENCE' or not reason):
            purpose='REFERENCE';reason=reason or '概览用于方向与覆盖核对，不独立新增构件数量。'
        if purpose in ('REFERENCE','EXCLUDE') and not reason:
            purpose='REVIEW';reason='提出了过滤分类但缺少理由，需确认后才可排除。'
        if purpose=='MEMBERS':reason=reason or '独立平面或立面布置视图；没有足够证据将其排除。'
        if purpose=='REVIEW':
            reason=reason or '视图用途不明确，需确认是否包含应计数的完整构件。'
            data['warnings'].append('用途待确认：'+view['title']+'；'+reason)
        view.update(takeoff_role=purpose,filter_reason=reason)
        counts[purpose]+=1
        texts=[t for t in pages.get(view['page_id'],{}).get('texts',[])+[
            m for m in data.get('manual_evidence',[]) if m['page_id']==view['page_id']] if inside(t,view['bbox'])]
        profiles=sorted({p for t in texts for p,_ in labels(t['text'])})
        if purpose in ('MEMBERS','REVIEW'):catalog.update(profiles)
        item=dict(view_id=view['id'],title=view['title'],takeoff_role=purpose,reason=reason,
            filter_reviewed=view.get('filter_reviewed',False),evidence_ids=list(view.get('evidence_ids',[])),
            profile_labels=profiles,included_in_stage_two=purpose=='MEMBERS')
        items.append(item)
        if audit:audit.event('VIEW_SCOPE_CLASSIFIED',**item)
    skipped_titles=[v['title'] for v in data['views'] if role(v) in ('REFERENCE','EXCLUDE')]
    data['warnings']=[w for w in data['warnings'] if not any(
        w.startswith(title+' 区域待确认：') for title in skipped_titles)]
    data['view_filter_summary']={'counts':{k:counts[k] for k in ('MEMBERS','REFERENCE','EXCLUDE','REVIEW')},
        'items':items,'candidate_profiles':sorted(catalog),
        'note':'过滤不删除原图、视图、轴线或证据；待确认用途不计为已识别零根。参考面名称仍可作为构件标签。'}
    all_native={p for page in pages.values() for t in page['texts'] for p,_ in labels(t['text'])}
    catalog.update(set(data.get('profile_catalog',[]))-all_native)
    data['profile_catalog']=sorted(catalog)
    data['view_filter_summary']['candidate_profiles']=sorted(catalog)
    return data

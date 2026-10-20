"""Offline group reconciliation for view ledgers; never confirms engineering counts."""
from collections import Counter,defaultdict
import hashlib
import json
from itertools import combinations

from .profiles import section
from .member_recognition import RecognizedMember,normalize_view_tags,view_tag,member_key
from .view_filter import role


def _identity(key):
    return 'group-'+hashlib.sha256(json.dumps(key,ensure_ascii=False).encode()).hexdigest()[:16]


def build_group_review(data):
    """Exact groups are reconciled as a whole, never via pairwise subtraction.

    'eligible' is only a mechanical trial: coverage and label agreement cannot
    prove that every drawing member was recognized or that lengths are correct.
    """
    views={v['id']:v for v in data['views'] if v['kind']!='ISOMETRIC' and role(v) in ('MEMBERS','REVIEW')}
    analysis={v['view_id']:v for v in data.get('view_analysis',[])}
    catalog=data.get('axis_catalog',{})
    known={view_tag(v) for v in views.values()}|set(normalize_view_tags(a['name'] for a in catalog.get('axes',[])))
    profiles={section(p)[0] for p in data.get('profile_catalog',[])}
    buckets={};seen=set();invalid=[];input_count=0
    for raw in data.get('view_members',[]):
        identity=raw.get('id')
        if not identity or identity in seen:raise ValueError('观察行必须具有不重复的ID')
        seen.add(identity)
        try:
            row=RecognizedMember.model_validate({k:raw[k] for k in RecognizedMember.model_fields}).model_dump()
            source=raw['source_view']
            if source not in views:raise ValueError('来源不是已登记的平面或立面')
            profile,multiplier=section(row['spec'])
            if not profile:raise ValueError('规格不能为空')
        except (ValueError,KeyError,TypeError) as error:
            invalid.append({'row_id':identity,'reason':str(error)})
            continue
        input_count+=row['member_cnt']
        key=member_key(row);tags=key[1]
        group=buckets.setdefault(key,{'id':_identity(key),'profile':profile,'component_multiplier':multiplier,
            'spec':profile if multiplier==1 else f'{multiplier}LF-{profile}',
            'view_tags':list(tags),'length_mm':row['length_mm'],'sources':{},'row_issues':[]})
        origin=group['sources'].setdefault(source,{'view_id':source,'title':views[source]['title'],
            'plane_tag':view_tag(views[source]),'image_file':views[source].get('image_file'),
            'member_cnt':0,'row_ids':[],'locations':[]})
        origin['member_cnt']+=row['member_cnt'];origin['row_ids'].append(identity)
        origin['locations'].append(row['location'])
        issues=list(raw.get('checks',[]))
        if row['issue']:issues.append(row['issue'])
        if not row['location']:issues.append('缺少位置说明')
        if view_tag(views[source]) not in tags:issues.append('未包含当前来源面名称')
        if set(tags)-known:issues.append('目录外的面名称')
        if profile not in profiles:issues.append('规格不在目录内')
        if issues:group['row_issues'].append({'row_id':identity,'issues':sorted(set(issues))})

    groups=[]
    for group in sorted(buckets.values(),key=lambda g:g['id']):
        origins=sorted(group['sources'].values(),key=lambda s:s['view_id'])
        group['sources']=origins
        counts={s['member_cnt'] for s in origins};group['observed_units']=sum(s['member_cnt'] for s in origins)
        group['agreed_member_cnt']=next(iter(counts)) if len(counts)==1 else None
        reasons=[]
        if group['length_mm'] is None:reasons.append('缺少长度')
        if len(group['view_tags'])<2:reasons.append('面标签不足，不能据此跨面自动匹配')
        if group['row_issues']:reasons.append('来源台账含待核验信息')
        for origin in origins:
            state=analysis.get(origin['view_id'])
            if not state or state.get('status')!='completed':
                reasons.append(f"{origin['view_id']} 未完成识别")
            elif state.get('coverage_differences') or (state.get('label_count') is not None and state['label_count']!=state.get('member_cnt')):
                reasons.append(f"{origin['view_id']} 存在规格标签覆盖差异")
        if len(counts)>1:reasons.append('同组各来源数量不一致')
        independent=len({s['plane_tag'] for s in origins})>1
        if len(origins)>1 and not independent:reasons.append('多个来源属于同一个面，缺少独立视图佐证')
        if len(origins)>1:
            group['status']='count_conflict' if len(counts)>1 else 'exact_key_candidate'
        else:group['status']='independent_candidate'
        group['review_reasons']=sorted(set(reasons))
        group['eligible_for_exact_trial']=len(origins)>1 and independent and not reasons
        group['trial_removed_observations']=(group['observed_units']-group['agreed_member_cnt']) if group['eligible_for_exact_trial'] else 0
        group['confirmed']=False
        groups.append(group)

    relations=[]
    by_profile=defaultdict(list)
    for group in groups:by_profile[(group['profile'],group['component_multiplier'])].append(group)
    for family in by_profile.values():
        for left,right in combinations(family,2):
            a=set(left['view_tags']);b=set(right['view_tags'])
            if left['length_mm'] is None or right['length_mm'] is None:continue
            if {s['view_id'] for s in left['sources']}&{s['view_id'] for s in right['sources']}:continue
            kind=None
            if a!=b and (a<b or b<a) and left['length_mm']==right['length_mm']:
                kind='incomplete_tags_candidate'
            elif a==b and 0<abs(left['length_mm']-right['length_mm'])<=1:
                kind='rounding_candidate'
            if kind:relations.append({'kind':kind,'group_ids':[left['id'],right['id']],
                                      'action':'review_only','removed_observations':0})

    # Compare full per-source distributions: several legitimate lengths or
    # profiles may coexist on the same pair of planes.
    distributions=defaultdict(lambda:defaultdict(Counter))
    for group in groups:
        for source in group['sources']:
            distributions[(tuple(group['view_tags']),group['profile'],group['component_multiplier'])][source['view_id']][group['length_mm']]+=source['member_cnt']
    distribution_issues=[]
    for (tags,profile,multiplier),sources in distributions.items():
        signatures={tuple(sorted(values.items(),key=lambda x:(x[0] is None,x[0] or 0))) for values in sources.values()}
        if len(sources)>1 and len(signatures)>1:
            distribution_issues.append({'kind':'length_or_segmentation_distribution_difference',
                'view_tags':list(tags),'profile':profile,'component_multiplier':multiplier,
                'sources':[{'view_id':s,'lengths':[{'length_mm':length,'member_cnt':cnt} for length,cnt in values.items()]}
                           for s,values in sorted(sources.items())]})

    profile_distributions=defaultdict(lambda:defaultdict(Counter))
    for group in groups:
        for source in group['sources']:
            profile_distributions[(tuple(group['view_tags']),group['length_mm'])][source['view_id']][group['spec']]+=source['member_cnt']
    profile_issues=[]
    for (tags,length),sources in profile_distributions.items():
        if len(sources)>1 and len({tuple(sorted(v.items())) for v in sources.values()})>1:
            profile_issues.append({'kind':'profile_distribution_difference','view_tags':list(tags),'length_mm':length,
                'sources':[{'view_id':s,'profiles':dict(values)} for s,values in sorted(sources.items())]})

    pending_sources=[{'view_id':identity,'title':view['title'],
        'status':analysis.get(identity,{}).get('status','missing')}
        for identity,view in views.items() if analysis.get(identity,{}).get('status')!='completed']
    pending_sources.extend({'view_id':p['page_id'],'title':f"{p['file']} 第{p['page']}页",'status':'page_pending'}
        for p in data.get('page_decisions',[]) if p['decision']=='REVIEW'
        or p['decision']=='CONSTRUCTION' and not p.get('view_ids'))
    removed=sum(g['trial_removed_observations'] for g in groups)
    return {'mode':'offline_group_review','engineering_verified':False,'mutates_source':False,
        'summary':{'input_rows':len(seen),'valid_observation_units':input_count,'invalid_rows':len(invalid),
            'exact_key_groups':len(groups),'cross_source_groups':sum(len(g['sources'])>1 for g in groups),
            'count_conflicts':sum(g['status']=='count_conflict' for g in groups),
            'eligible_exact_candidates':sum(g['eligible_for_exact_trial'] for g in groups),
            'trial_removed_observations':removed,'trial_remaining_observations':input_count-removed,
            'confirmed_groups':0,'confirmed_final_quantity':None,
            'single_tag_groups':sum(len(g['view_tags'])<2 for g in groups),
            'missing_length_groups':sum(g['length_mm'] is None for g in groups),
            'pending_source_views':len(pending_sources)},
        'groups':groups,'relations':relations,'distribution_issues':distribution_issues,
        'profile_distribution_issues':profile_issues,
        'pending_sources':pending_sources,'invalid_rows':invalid,
        'note':'试算仅对无现有疑点且覆盖检查一致的完整同键组作一次扣减，仍需人工确认；未知重叠不取最大值、不逐对减数。2LF保持组合，不输出展开后的最终数量。'}


def expand_confirmed_groups(groups):
    """Final summaries only; expansion is derived anew, never mutates input groups."""
    summary=Counter()
    seen=set()
    for group in groups:
        if not group.get('id') or group['id'] in seen:raise ValueError('结果组ID必须存在且不可重复，避免重复展开')
        seen.add(group['id'])
        count=group.get('confirmed_member_cnt')
        if group.get('confirmed') is not True or type(count) is not int or count<=0:
            raise ValueError('只允许展开已确认且数量明确的结果组')
        if group['length_mm'] is None:raise ValueError('缺少长度的组不能进入完整长度汇总')
        if type(group['component_multiplier']) is not int or not 1<=group['component_multiplier']<=8:
            raise ValueError('无效的组合组件数量')
        summary[(group['profile'],group['length_mm'])]+=count*group['component_multiplier']
    return [{'profile':profile,'length_mm':length,'quantity':count,'total_length_mm':length*count}
            for (profile,length),count in sorted(summary.items())]

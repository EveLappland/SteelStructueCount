"""Deterministic calibration, evidenced lengths and conservative entity matching."""
import hashlib
import json
import math
import re
from collections import defaultdict
from .native_pdf import inside

from .domain import LengthCandidate, Member, StageOne, StageTwo, StageThree


def section(profile):
    label = str(profile).upper().replace('×', '*').replace(' ', '')
    label = re.sub(r'/(?:EPB|EPW|EP|EMH|EMTF|LT)\b', '', label)
    match = re.fullmatch(r'(\d+)L(?:F)?[-_]*(L\d+(?:[X*]\d+)?)', label)
    if match:
        multiplier = int(match[1])
        if not 1 <= multiplier <= 8:
            raise ValueError('组合截面组件数量必须在 1 到 8 之间。')
        return match[2].replace('X', '*'), multiplier
    return label, 1


def refs_exist(ids, evidence):
    return bool(ids) and all(identity in evidence for identity in ids)


def numeric_supported(ids, numbers, evidence):
    """Existence of a vector/text ID alone does not prove a numeric dimension."""
    if not refs_exist(ids,evidence):
        return False
    for identity in ids:
        item=evidence[identity]
        if item.get('kind')=='MANUAL' and item.get('text','').strip():
            return True
        if item.get('kind')!='TEXT':
            continue
        text=re.sub(r'\bEL\.\s*','EL ',item.get('text',''),flags=re.I)
        text=re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))','',text)
        for token in re.findall(r'(?<![\w.])[+-]?\d+(?:\.\d+)?(?=$|[^\w.]|mm\b|m\b)',text,flags=re.I):
            if any(abs(abs(float(token))-abs(number))<=0.001 for number in numbers):
                return True
    return False


def all_refs(value):
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith('evidence_ids'):
                result.extend(item)
            else:
                result.extend(all_refs(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(all_refs(item))
    return list(dict.fromkeys(result))


def validate_stage_one(data, evidence, page_ids):
    stage = StageOne.model_validate(data).model_dump()
    ids = [v['id'] for v in stage['views']]
    observations = [o['id'] for o in stage['observations']]
    if len(ids) != len(set(ids)) or len(observations) != len(set(observations)):
        raise ValueError('视图和观察 ID 必须唯一。')
    if not ids:
        raise ValueError('至少需要一个视图。')
    views = {v['id']: v for v in stage['views']}
    for view in views.values():
        if view['page_id'] not in page_ids:
            raise ValueError('视图引用了不存在的 PDF 页。')
        for mark in view['axis_marks']:
            if any(value < 0 or value > 1 for value in mark['point']):
                raise ValueError('轴线图上位置必须是整页归一化坐标。')
    for item in stage['manual_evidence']:
        if item['kind'] != 'MANUAL' or item['page_id'] not in page_ids or not item['text'].strip():
            raise ValueError('人工证据需要有效页面、MANUAL 类型及说明。')
        box=item['bbox']
        if not (0 <= box[0] <= box[2] <= 1 and 0 <= box[1] <= box[3] <= 1):
            raise ValueError('人工证据区域必须位于 PDF 页内。')
    missing = set(all_refs(stage)) - set(evidence)
    if missing:
        raise ValueError('引用了不存在的证据 ID：' + ', '.join(sorted(missing)[:8]))
    for observation in stage['observations']:
        if observation['view_id'] not in views or views[observation['view_id']]['kind'] == 'ISOMETRIC':
            raise ValueError('构件观察必须关联平面或立面，概览不能参与数量。')
        view = views[observation['view_id']]
        for point in (observation['image_start'], observation['image_end']):
            if point is not None and not (view['bbox'][0] <= point[0] <= view['bbox'][2]
                                         and view['bbox'][1] <= point[1] <= view['bbox'][3]):
                raise ValueError('构件图上端点必须位于所属视图区域内。')
    return stage


def calibrate(frame, evidence):
    """Solve signed dimension chains. Contradictory components stay unresolved."""
    values, graph, seeds, issues, sources = {}, defaultdict(list), {}, [], {}
    for axis in frame['axes']:
        key = (axis['direction'], axis['label'])
        if key in seeds or key in sources:
            raise ValueError('同方向轴线名称不能重复。')
        sources[key] = list(axis['evidence_ids'])
        if axis['value_mm'] is not None:
            if refs_exist(axis['evidence_ids'], evidence):
                seeds[key] = float(axis['value_mm'])
            else:
                issues.append(f'{key} 坐标锚点缺少证据。')
        graph[key]
    for dim in frame['dimensions']:
        a, b = (dim['direction'], dim['from_label']), (dim['direction'], dim['to_label'])
        if a not in sources or b not in sources or not numeric_supported(dim['evidence_ids'],[dim['distance_mm']],evidence):
            issues.append(f'{a}→{b} 尺寸缺少轴线或证据。')
            continue
        graph[a].append((b, dim['distance_mm'], dim['evidence_ids']))
        graph[b].append((a, -dim['distance_mm'], dim['evidence_ids']))
    visited = set()
    for first in graph:
        if first in visited:
            continue
        relative, pending, component_sources = {first: 0.0}, [first], set()
        inconsistent = False
        while pending:
            node = pending.pop()
            visited.add(node)
            component_sources.update(sources.get(node, []))
            for target, distance, refs in graph[node]:
                component_sources.update(refs)
                expected = relative[node] + distance
                if target in relative:
                    if abs(relative[target] - expected) > 1:
                        inconsistent = True
                else:
                    relative[target] = expected
                    pending.append(target)
        offsets = [seeds[node] - delta for node, delta in relative.items() if node in seeds]
        if not offsets:
            issues.append(f'{first[0]} 方向轴线组缺少坐标原点：' + ','.join(n[1] for n in relative))
            continue
        if inconsistent or max(offsets) - min(offsets) > 1:
            issues.append('尺寸链或坐标锚点矛盾：' + ','.join(n[1] for n in relative))
            continue
        for node, delta in relative.items():
            values[node] = delta + offsets[0]
            sources[node] = sorted(component_sources)
    return values, sources, issues


def resolve_point(point, frame, values, sources, evidence):
    coords, refs, issues = [], [], []
    for direction, name in [('X','x'), ('Y','y')]:
        ref = point[name]
        if ref['axis_label'] is not None and ref['value_mm'] is not None:
            issues.append(f'{direction} 不能同时指定轴线与直接坐标。')
            coords.append(None)
            continue
        if ref['axis_label'] is not None:
            key = direction, ref['axis_label']
            value = values.get(key)
            refs.extend(sources.get(key, []))
        else:
            raw_value=ref['value_mm']
            value = raw_value if raw_value is not None and numeric_supported(ref['evidence_ids'],[raw_value],evidence) else None
        if value is None:
            issues.append(f'{direction} 坐标未确定。')
        offset = ref['offset_mm']
        if offset and not numeric_supported(ref['evidence_ids'],[offset],evidence):
            issues.append(f'{direction} 偏移缺少尺寸证据。')
        if ref['towards'] is not None:
            towards = values.get((direction, ref['towards']))
            if value is None or towards is None or towards == value:
                issues.append(f'{direction} 偏移方向未确定。')
            else:
                offset = abs(offset) * (1 if towards > value else -1)
        coords.append(None if value is None else value + offset)
        refs.extend(ref['evidence_ids'])
    z = point['z']
    comparable=[z['value'], z['value']*1000 if z['unit']=='m' else z['value']/1000] if z['value'] is not None else []
    if z['value'] is None or not numeric_supported(z['evidence_ids'],comparable,evidence):
        issues.append('标高缺少数值或证据。')
        coords.append(None)
    else:
        elevation = z['value'] * (1000 if z['unit'] == 'm' else 1)
        coords.append(elevation - frame['z_origin_m'] * 1000 + z['offset_mm'])
    if not numeric_supported(frame['origin_evidence_ids'],[frame['z_origin_m'],frame['z_origin_m']*1000],evidence):
        issues.append('标高原点缺少证据。')
    if z['basis'] != z['reference_basis'] and not z['offset_mm']:
        issues.append('标高基准变换缺少明确偏移，不能将 TOS/BOB 当作中心线。')
    if z['offset_mm'] and not numeric_supported(z['evidence_ids'],[z['offset_mm']],evidence):
        issues.append('标高偏移缺少对应的数值证据。')
    refs.extend(z['evidence_ids'] + frame['origin_evidence_ids'])
    return coords, refs, issues


def canonical_axis(direction,label,frame):
    matches=[axis['label'] for axis in frame['axes'] if axis['direction']==direction
             and label in [axis['label'],*axis.get('aliases',[])]]
    return matches[0] if len(matches)==1 else label


def normalize_coordinates(point,view,frame,values,sources,evidence,dimension_texts):
    """Canonicalize existing model numbers using proved axes/dimensions; never measure pixels."""
    changes=[]
    for direction,name in [('X','x'),('Y','y')]:
        ref=point[name]
        before={key:ref[key] for key in ('axis_label','value_mm','offset_mm','towards')}
        if ref['axis_label'] is not None:
            ref['axis_label']=canonical_axis(direction,ref['axis_label'],frame)
        if ref['towards'] is not None:
            ref['towards']=canonical_axis(direction,ref['towards'],frame)
        label=ref['axis_label']
        value=ref['value_mm']
        if label is not None and value is not None and (direction,label) in values:
            anchor=values[(direction,label)]
            offset=ref['offset_mm']
            if ref['towards'] is not None and (direction,ref['towards']) in values:
                offset=abs(offset)*(1 if values[(direction,ref['towards'])]>anchor else -1)
            if abs(value-anchor)<=0.001 or abs(value-(anchor+offset))<=0.001:
                ref['value_mm']=None
        elif label is None and value is not None:
            exact=[key for key,coordinate in values.items() if key[0]==direction and abs(coordinate-value)<=0.001]
            if len(exact)==1:
                ref.update(axis_label=exact[0][1],value_mm=None)
            elif ref['offset_mm']==0:
                options=[]
                for key,coordinate in values.items():
                    if key[0]!=direction:continue
                    delta=value-coordinate
                    supported=[t['id'] for t in dimension_texts if t['id'] in ref['evidence_ids']
                               and numeric_supported([t['id']],[delta],evidence)]
                    if supported and abs(delta)>0.001:
                        options.append((abs(delta),key[1],delta,supported))
                if options:
                    _,axis,delta,proof=min(options,key=lambda item:(item[0],item[1]))
                    ref.update(axis_label=axis,value_mm=None,offset_mm=delta)
                    ref['evidence_ids']=list(dict.fromkeys(ref['evidence_ids']+proof))
        key=(direction,ref['axis_label'])
        after={key:ref[key] for key in ('axis_label','value_mm','offset_mm','towards')}
        if after!=before:
            proof=list(dict.fromkeys(ref['evidence_ids']+sources.get(key,[])))
            changes.append({'direction':direction,'before':before,'after':after,'evidence_ids':proof,
                            'reason':'数值不变：消除与轴线一致的冗余坐标，或写成已知轴线加图示尺寸偏移；未从像素推算。'})
    return changes


def build_lengths(stage_one, evidence):
    data = StageOne.model_validate(stage_one).model_dump()
    frame, views = data['frame'], {v['id']: v for v in data['views']}
    for axis in frame['axes']:
        aliases=set(axis.get('aliases',[]))
        for identity in axis['evidence_ids']:
            text=evidence.get(identity,{}).get('text','').strip()
            if text and (text==axis['label'] or text.rsplit('/',1)[-1]==axis['label']):
                aliases.add(text)
        axis['aliases']=sorted(aliases)
    values, sources, frame_issues = calibrate(frame, evidence)
    dimension_texts={}
    for view in views.values():
        dimension_texts[view['id']]=[item for item in evidence.values() if item.get('kind')=='TEXT'
            and item.get('page_id')==view['page_id'] and inside(item,view['bbox'])
            and re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?\s*(?:mm|m)?',item.get('text','').strip(),re.I)]
    candidates = []
    for observation in data['observations']:
        view = views[observation['view_id']]
        normalizations=[]
        for name in ('start','end'):
            changes=normalize_coordinates(observation[name],view,frame,values,sources,evidence,dimension_texts[view['id']])
            normalizations.extend({'endpoint':name,**change} for change in changes)
        # A view title can supply its fixed third coordinate; never infer an in-plane offset.
        if refs_exist(view['evidence_ids'],evidence):
            for name in ('start','end'):
                point=observation[name]
                if view['fixed_axis'] in ('X','Y') and view['fixed_label']:
                    ref=point[view['fixed_axis'].lower()]
                    if ref['axis_label'] is None and ref['value_mm'] is None:
                        ref['axis_label']=canonical_axis(view['fixed_axis'],view['fixed_label'],frame)
                        ref['evidence_ids']=list(dict.fromkeys(ref['evidence_ids']+view['evidence_ids']))
                elif view['fixed_axis']=='Z' and view['elevation_m'] is not None and point['z']['value'] is None:
                    z=point['z']
                    z.update(value=view['elevation_m'],unit='m',basis=view['elevation_basis'],
                             reference_basis=view['elevation_basis'])
                    z['evidence_ids']=list(dict.fromkeys(z['evidence_ids']+view['evidence_ids']))
        profile, multiplier = section(observation['profile'])
        start, sr, si = resolve_point(observation['start'], frame, values, sources, evidence)
        end, er, ei = resolve_point(observation['end'], frame, values, sources, evidence)
        issues = si + ei
        if not profile or profile in {'UNKNOWN', 'NONE', 'NULL', 'N/A'}:
            issues.append('规格未确定。')
        if observation['deferred']:
            issues.append('观察保留待确认（识别歧义或人工暂缓）。')
        if observation['image_start'] is None or observation['image_end'] is None:
            issues.append('缺少原图首尾定位。')
        if not refs_exist(observation['evidence_ids'], evidence):
            issues.append('缺少构件线或规格证据。')
        for label in ('start_boundary', 'end_boundary'):
            boundary = observation[label]
            if boundary['kind'] in ('UNKNOWN', 'CONTINUOUS') or not refs_exist(boundary['evidence_ids'], evidence):
                issues.append(f'{label} 缺少明确端部、拼接或规格变化证据。')
        if len(set(view['visible_axes'])) != 2 or view['fixed_axis'] is None:
            issues.append('视图的显示方向或固定方向未确定。')
        if view['kind']=='PLAN' and (view['fixed_axis']!='Z' or set(view['visible_axes'])!={'X','Y'}):
            issues.append('平面视图必须使用 X/Y 和固定标高。')
        if view['kind']=='ELEVATION' and (view['fixed_axis'] not in ('X','Y') or 'Z' not in view['visible_axes']):
            issues.append('立面视图必须包含标高方向和固定平面轴线。')
        bases = [observation[p]['z']['reference_basis'] for p in ('start','end')]
        if bases[0] != bases[1]:
            issues.append('首尾使用不同标高参考基准，需明确统一。')
        length, formula = None, ''
        if None not in start + end:
            delta = [end[k] - start[k] for k in range(3)]
            varied = [k for k, distance in enumerate(delta) if abs(distance) > 1e-6]
            if len(varied) == 3:
                issues.append('三维斜撑不在首版支持范围。')
            elif not varied:
                issues.append('首尾重合或投影无法确定真实长度。')
            else:
                length = round(math.sqrt(sum(d*d for d in delta)), 3)
                formula = 'sqrt(' + ' + '.join(f'{abs(d):g}²' for d in delta if abs(d)>1e-6) + ') mm'
            if view['fixed_axis']:
                k = 'XYZ'.index(view['fixed_axis'])
                if abs(start[k] - end[k]) > 1:
                    issues.append('构件不在所属正投影视图的固定坐标平面内。')
                if view['fixed_axis'] in ('X','Y') and view['fixed_label']:
                    fixed_label=canonical_axis(view['fixed_axis'],view['fixed_label'],frame)
                    plane = values.get((view['fixed_axis'],fixed_label))
                    # Axis-relative offsets must be explicitly evidenced, not an unrelated frame.
                    for point in ('start','end'):
                        ref = observation[point][view['fixed_axis'].lower()]
                        if plane is None or ref['axis_label'] != fixed_label:
                            issues.append('构件固定轴线与视图标题不一致。')
                if view['fixed_axis'] == 'Z' and view['elevation_m'] is not None:
                    for point in ('start','end'):
                        z = observation[point]['z']
                        el_m = z['value'] if z['unit']=='m' else (z['value']/1000 if z['value'] is not None else None)
                        if el_m is None or abs(el_m-view['elevation_m'])>0.000001 or z['basis'] != view['elevation_basis']:
                            issues.append('构件基准标高与平面标题不一致。')
        semantic_points = []
        for point in ('start','end'):
            semantic_points.append({k:{name:value for name,value in v.items() if name!='evidence_ids'}
                                    for k,v in observation[point].items()})
        semantic = json.dumps(sorted(semantic_points, key=lambda p:json.dumps(p,sort_keys=True)),sort_keys=True)
        candidates.append(LengthCandidate(observation_id=observation['id'], profile=profile,
            component_count=multiplier, start_mm=start if None not in start else None,
            end_mm=end if None not in end else None, reference_basis='|'.join(bases)+'|'+observation['reference_line'],
            semantic_key=semantic, length_mm=length, formula=formula,
            evidence_ids=list(dict.fromkeys(sr+er+all_refs(observation))),
            issues=list(dict.fromkeys(issues)), confirmed=not issues and length is not None,
            normalizations=normalizations).model_dump())
    catalog = defaultdict(list)
    for candidate in candidates:
        if candidate['confirmed']:
            catalog[(candidate['profile'],candidate['length_mm'])].append(candidate['observation_id'])
    return StageTwo(**data, candidates=candidates, frame_issues=frame_issues,
                    length_catalog=[{'profile':p,'length_mm':l,'observation_ids':ids}
                                    for (p,l),ids in sorted(catalog.items())]).model_dump()


def points_close(a,b):
    # Euclidean tolerance, not 1 mm independently in each component.
    return math.dist(a,b) <= 1.000000001


def same_segment(a,b):
    return ((points_close(a['start_mm'],b['start_mm']) and points_close(a['end_mm'],b['end_mm'])) or
            (points_close(a['start_mm'],b['end_mm']) and points_close(a['end_mm'],b['start_mm'])))


def line_relationship(a,b):
    p,q,r,s = a['start_mm'],a['end_mm'],b['start_mm'],b['end_mm']
    vector=[q[k]-p[k] for k in range(3)]
    norm=sum(v*v for v in vector)
    if not norm:
        return None
    parameters=[]
    for point in (r,s):
        t=sum((point[k]-p[k])*vector[k] for k in range(3))/norm
        projected=[p[k]+t*vector[k] for k in range(3)]
        if not points_close(projected,point):
            return None
        parameters.append(t)
    left,right=sorted(parameters)
    overlap=min(1,right)-max(0,left)
    if overlap>1/math.sqrt(norm):
        return 'OVERLAP'
    if abs(overlap)<=1/math.sqrt(norm):
        return 'ADJACENT'
    return None


def match_entities(stage_two, decisions=()):
    stage=StageTwo.model_validate(stage_two).model_dump()
    observations={o['id']:o for o in stage['observations']}
    valid={c['observation_id']:c for c in stage['candidates'] if c['confirmed']}
    exceptions=[{'observation_id':c['observation_id'],'issues':c['issues']} for c in stage['candidates'] if not c['confirmed']]
    parent={identity:identity for identity in valid}
    members, matches, conflicts = [], [], []
    def root(identity):
        while parent[identity]!=identity:
            identity=parent[identity]
        return identity
    by_pair={}
    for decision in decisions:
        pair=tuple(sorted([decision['left'],decision['right']]))
        if pair[0]==pair[1] or pair in by_pair or not all(p in valid for p in pair):
            raise ValueError('匹配决定需要两个不同、有效且不重复的观察 ID。')
        by_pair[pair]=decision
    distinct_pairs, blocked=set(),set()
    ids=sorted(valid)
    for i,left in enumerate(ids):
        for right in ids[i+1:]:
            a,b=valid[left],valid[right]
            oa,ob=observations[left],observations[right]
            exact=same_segment(a,b)
            semantic_equal=a['semantic_key']==b['semantic_key']
            relation='COINCIDENT' if exact else line_relationship(a,b)
            compatible=(a['profile']==b['profile'] and a['component_count']==b['component_count']
                        and oa['material']==ob['material'] and a['reference_basis']==b['reference_basis']
                        and oa['assembly_identity']==ob['assembly_identity']
                        and oa['segment_identity']==ob['segment_identity'])
            decision=by_pair.get((left,right))
            if decision:
                if decision['action']=='SAME' and not (exact and compatible):
                    raise ValueError('确认重复要求端点、规格、基准、分段和组合身份一致。请先修正第二阶段数据。')
                if decision['action']=='DISTINCT':
                    distinct_pairs.add((left,right))
                    matches.append({**decision,'kind':'MANUAL_DISTINCT'})
                    continue
                if decision['action']=='DEFER':
                    blocked.update((left,right))
                    conflicts.append({**decision,'kind':relation or 'MANUAL_DEFER'})
                    continue
            same_view=oa['view_id']==ob['view_id']
            if exact and compatible and (not same_view or (decision and decision['action']=='SAME')):
                # Do not allow tolerance chains (A~B, B~C, but A !~ C) or override a DISTINCT decision.
                group_a=[p for p in ids if root(p)==root(left)]
                group_b=[p for p in ids if root(p)==root(right)]
                if any(tuple(sorted((p,q))) in distinct_pairs or not same_segment(valid[p],valid[q])
                       for p in group_a for q in group_b):
                    blocked.update(group_a+group_b)
                    conflicts.append({'left':left,'right':right,'kind':'AMBIGUOUS_CLUSTER','reason':'匹配链超过容差或违反独立构件决定。'})
                    continue
                parent[root(right)]=root(left)
                matches.append({'left':left,'right':right,'kind':'DUPLICATE',
                                'reason':decision['reason'] if decision else
                                ('一致语义端点及毫米坐标的跨视图观察。' if semantic_equal else '毫米端点在 1 mm 舍入容差内且构件属性一致。')})
            elif relation:
                # Crossings are intentionally not relationships; adjacency never joins members.
                conflicts.append({'left':left,'right':right,'kind':relation,
                                  'reason':'同位置属性冲突、同视图重叠或分段关系需确认。'})
                blocked.update((left,right))
    # Manual decisions may have been processed after earlier automatic unions.
    for left,right in distinct_pairs:
        if root(left)==root(right):
            blocked.update([p for p in ids if root(p)==root(left)])
            conflicts.append({'left':left,'right':right,'kind':'DECISION_CONFLICT','reason':'独立决定与其他重复匹配冲突。'})
    groups=defaultdict(list)
    for identity in ids:
        groups[root(identity)].append(identity)
    for group in groups.values():
        if set(group)&blocked:
            exceptions.extend({'observation_id':p,'issues':['跨视图或分段匹配冲突未解决。']} for p in group)
            continue
        candidate=valid[group[0]]
        observation=observations[group[0]]
        assembly='M-'+hashlib.sha256('|'.join(sorted(group)).encode()).hexdigest()[:16]
        refs=sorted({e for p in group for e in valid[p]['evidence_ids']})
        start,end=sorted([candidate['start_mm'],candidate['end_mm']])
        for index in range(1,candidate['component_count']+1):
            members.append(Member(id=f'{assembly}-{index}',assembly_id=assembly,component_index=index,
                profile=candidate['profile'],material=observation['material'],start_mm=start,end_mm=end,
                length_mm=candidate['length_mm'],observation_ids=group,evidence_ids=refs).model_dump())
    summary=defaultdict(lambda:{'quantity':0,'total_length_mm':0})
    for member in members:
        key=member['profile'],member['material'],member['length_mm']
        summary[key]['quantity']+=1
        summary[key]['total_length_mm']+=member['length_mm']
    return StageThree(decisions=list(decisions),matches=matches,conflicts=conflicts,members=members,
                      exceptions=exceptions,summary=[{'profile':p,'material':m,'length_mm':l,**totals}
                                                    for (p,m,l),totals in sorted(summary.items())]).model_dump()

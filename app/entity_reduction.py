"""Occurrence-conserving cross-view entity matching and length reconciliation.

Tags index candidate relationships, not unique members. No reference-answer input.
"""
from collections import Counter, defaultdict
from itertools import combinations

from pydantic import Field
from .domain_base import Contract
from .label_facts import digest
from .profiles import section


class EntityClaim(Contract):
    observation_ids: list[str] = Field(min_length=1)
    length_source_id: str | None
    reason: str = Field(min_length=1, max_length=2400)


class EntityReply(Contract):
    entities: list[EntityClaim]


def compatible(a,b):
    """Reciprocal named-plane evidence; length deliberately not a gate.

    Same-plane copies and symmetry are not independent matching evidence.
    """
    return (a['view_uid'] != b['view_uid'] and a['source_plane'] != b['source_plane']
            and len(set(a['view_tags']))>1 and len(set(b['view_tags']))>1
            and section(a['spec']) == section(b['spec'])
            and a['source_plane'] in b['view_tags'] and b['source_plane'] in a['view_tags'])


def find_cases(observations):
    """Connected components keep multiplicity/count conflicts together."""
    neighbors = {i:set() for i in observations}
    by_spec = defaultdict(list)
    for identity,row in observations.items():
        if len(set(row['view_tags']))>1:
            by_spec[section(row['spec'])].append(identity)
    for ids in by_spec.values():
        for a,b in combinations(ids,2):
            if compatible(observations[a],observations[b]):
                neighbors[a].add(b); neighbors[b].add(a)
    cases,seen = [],set()
    for identity in sorted(observations):
        if identity in seen or not neighbors[identity]:
            continue
        stack,ids = [identity],set()
        while stack:
            current = stack.pop()
            if current in ids:
                continue
            ids.add(current); stack.extend(neighbors[current]-ids)
        seen.update(ids)
        cases.append({'id':'match-'+digest(sorted(ids))[:16], 'kind':'MATCH',
                      'observation_ids':sorted(ids)})
    return cases, sorted(set(observations)-seen)


def make_entity(ids, observations, *, method, reason, candidate=None, source_id=None, quantity=1):
    ids = sorted(ids); rows = [observations[i] for i in ids]
    length = candidate['length_mm'] if candidate is not None else None
    profile,multiplier = section(rows[0]['spec'])
    source_counts = Counter(r['view_uid'] for r in rows)
    return {'id':'entity-'+digest(ids)[:20],'spec':rows[0]['spec'],'profile':profile,
            'component_multiplier':multiplier,'member_cnt':quantity,
            'observation_ids':ids,'source_view_counts':dict(sorted(source_counts.items())),
            'view_tags':sorted({t for r in rows for t in r['view_tags']}),
            'length_mm':length, 'length_source_id':source_id,'measurement':candidate,
            'method':method,'reason':reason,
            'status':'UNKNOWN' if length is None else ('AUTOMATIC_FALLBACK' if method=='FALLBACK' else 'MODEL_OR_RULE_DERIVED')}


def independent(ids, observations, reason, method='FALLBACK'):
    return [make_entity([i], observations, method=method, reason=reason,
                        candidate=observations[i]['measurement'],source_id=i) for i in sorted(ids)]


def exact_groups(case, observations):
    """Only equal per-view distributions with complete pairwise relationships.

    Return homogeneous quantity groups: never pretend arbitrary pairings of
    indistinguishable occurrences establish per-member physical identities.
    """
    ids=case['observation_ids']; per_view=defaultdict(list)
    for i in ids:
        r=observations[i]
        if r['length_mm'] is None or r.get('issue') or r.get('local_checks') or r.get('review_notes') or r.get('status')!='MODEL_DERIVED':
            return None
        per_view[r['view_uid']].append(i)
    signatures=[]
    for local in per_view.values():
        signatures.append(Counter((tuple(sorted(observations[i]['view_tags'])), observations[i]['length_mm']) for i in local))
    if not signatures or any(s!=signatures[0] for s in signatures):
        return None
    if any(not compatible(observations[a],observations[b]) for a,b in combinations(ids,2)
           if observations[a]['view_uid']!=observations[b]['view_uid']):
        return None
    buckets=defaultdict(list)
    for i in ids:
        r=observations[i]; buckets[(tuple(sorted(r['view_tags'])),r['length_mm'])].append(i)
    result=[]
    for bucket in buckets.values():
        counts=Counter(observations[i]['view_uid'] for i in bucket)
        result.append(make_entity(bucket, observations,method='EQUAL_DISTRIBUTION',
            reason='所属面相互对应且各视图规格/标签/长度数量分布一致；保留同视图数量，组内逐根配对未确定。',
            candidate=observations[bucket[0]]['measurement'],source_id=bucket[0],quantity=next(iter(counts.values()))))
    return result


def validate_reply(payload, case, observations):
    """Validate a complete partition before adopting any model conclusion."""
    if case['kind']!='MATCH':
        raise ValueError('第三阶段仅允许匹配消歧，不执行长度重算任务')
    reply=EntityReply.model_validate(payload).model_dump()
    targets=case['observation_ids']; assigned=[i for e in reply['entities'] for i in e['observation_ids']]
    if Counter(assigned)!=Counter(targets):
        raise ValueError('本次全部observation_id必须恰好归属一次，不得漏项、重复、新增')
    entities=[]
    for claim in reply['entities']:
        ids=claim['observation_ids']; rows=[observations[i] for i in ids]
        if len({r['view_uid'] for r in rows})!=len(rows):
            raise ValueError('同一视图的不同钢材标签不得合并为一根')
        if any(not compatible(observations[a],observations[b]) for a,b in combinations(ids,2)):
            raise ValueError('跨视图匹配缺少规格一致及双方所属面的相互对应，不得按对称性合并')
        source,candidate=claim['length_source_id'],None
        if source is not None and source not in ids:
            raise ValueError('长度来源必须属于本实体')
        if len(ids)==1:
            # Keeping an observation independent cannot alter or erase its length.
            entities.extend(independent(ids,observations,claim['reason'],method='AI_MATCH'))
            continue
        if source is not None:
            if source not in ids or observations[source]['measurement'] is None or observations[source]['length_mm'] is None:
                raise ValueError('长度来源必须是本实体具有有效尺寸依据的观察')
            candidate=observations[source]['measurement']
        elif any(r['length_mm'] is not None for r in rows):
            raise ValueError('匹配实体存在已有长度时必须选择来源；无法消歧可保留独立观察，不得抹除原长度')
        entities.append(make_entity(ids, observations,method='AI_MATCH',
            reason=claim['reason'],candidate=candidate,source_id=source))
    return entities


def conservation(entities, observations):
    assigned=[i for e in entities for i in e['observation_ids']]
    if Counter(assigned)!=Counter(observations.keys()):
        raise ValueError('全局观察守恒失败：每条标签必须且只能归属一个实体/数量组')
    counts=Counter()
    for entity in entities:
        ids=entity['observation_ids']; n=entity['member_cnt']
        if type(n) is not int or n<1:
            raise ValueError('实体数量必须为正整数')
        for a,b in combinations(ids,2):
            if observations[a]['view_uid']!=observations[b]['view_uid'] and not compatible(observations[a],observations[b]):
                raise ValueError('实体中存在没有所属面关联的观察')
        local=Counter(observations[i]['view_uid'] for i in ids)
        if any(v!=n for v in local.values()):
            raise ValueError('同视图标签数量与实体组数量不符')
        counts.update(local)
    expected=Counter(r['view_uid'] for r in observations.values())
    if counts!=expected:
        raise ValueError('逐视图标签数量守恒失败')
    return {'every_observation_assigned_once':True,'per_view_counts_preserved':True,
            'source_view_counts':dict(sorted(counts.items()))}

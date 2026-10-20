"""Quantity-weighted matching against an independent, scoped reference ledger."""
from collections import Counter, defaultdict, deque

from .profiles import section
from .member_recognition import normalize_view_tags


def _units(rows):
    groups=Counter()
    for row in rows:
        profile,multiplier=section(row.get('spec',row.get('profile','')))
        count=row.get('member_cnt',row.get('quantity',1))
        if type(count) is not int or count<=0:raise ValueError('验收数量必须是正整数')
        length=row.get('length_mm')
        if length is not None and (not isinstance(length,(int,float)) or length<=0):
            raise ValueError('验收长度必须为正数或待确认')
        tags=tuple(normalize_view_tags(row['view_tags']))
        if not profile or not tags:raise ValueError('验收必须提供规格与所属面')
        groups[(profile,tags,length)]+=count*multiplier
    return groups


def score(reference,predictions,*,tolerance_mm=1):
    """Maximum-flow matching avoids greedy rounding matches and quantity reuse.

    A missing reference length is still in the denominator and never matches.
    This is strict on the complete plane set, not only profile/length totals.
    """
    if not 0<=tolerance_mm<=1:raise ValueError('只接受最多1mm的毫米舍入差异')
    expected=_units(reference['members']);actual=_units(predictions)
    left=list(expected);right=list(actual)
    graph=defaultdict(dict)
    def edge(a,b,capacity):graph[a][b]=capacity;graph[b][a]=0
    for i,key in enumerate(left):edge('source',('l',i),expected[key])
    for j,key in enumerate(right):edge(('r',j),'sink',actual[key])
    links=[]
    for i,a in enumerate(left):
        for j,b in enumerate(right):
            if a[:2]==b[:2] and a[2] is not None and b[2] is not None and abs(a[2]-b[2])<=tolerance_mm:
                edge(('l',i),('r',j),min(expected[a],actual[b]));links.append((i,j))
    matched=0
    while True:
        previous={'source':None};queue=deque(['source'])
        while queue and 'sink' not in previous:
            node=queue.popleft()
            for nxt,capacity in graph[node].items():
                if capacity>0 and nxt not in previous:previous[nxt]=node;queue.append(nxt)
        if 'sink' not in previous:break
        path=[];node='sink'
        while previous[node] is not None:path.append((previous[node],node));node=previous[node]
        count=min(graph[a][b] for a,b in path)
        for a,b in path:graph[a][b]-=count;graph[b][a]+=count
        matched+=count
    missing=[];extra=[]
    def item(key,count):return dict(profile=key[0],view_tags=list(key[1]),length_mm=key[2],quantity=count)
    for i,key in enumerate(left):
        if remaining:=graph['source'][('l',i)]:missing.append(item(key,remaining))
    for j,key in enumerate(right):
        if remaining:=graph[('r',j)]['sink']:extra.append(item(key,remaining))
    total=sum(expected.values());reported=sum(actual.values())
    precision=matched/reported if reported else 0
    recall=matched/total if total else 0
    return {'expected_single_members':total,'reported_single_members':reported,'matched_single_members':matched,
        'false_positive_members':reported-matched,'missing_or_incorrect_members':total-matched,
        'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0,
        'passed_90_percent':precision>=.9 and recall>=.9 and reference.get('scope',{}).get('complete_pdf_set',False),'tolerance_mm':tolerance_mm,
        'reference_scope_complete':reference.get('scope',{}).get('complete_pdf_set',False),
        'missing':missing,'extra':extra,
        'metric':'按单根数量计权；规格、完整所属面集合和名义段长同时匹配；待确认、漏项不计为正确'}

"""Transparent per-profile accuracy and nominal length error reporting."""
from collections import defaultdict
from .accuracy import _units, score


def _pairs(expected, actual):
    """Pair the smaller sorted list to a subset of the larger at minimum L1 cost."""
    swapped = len(expected) > len(actual)
    small, large = (actual, expected) if swapped else (expected, actual)
    n, m = len(small), len(large)
    costs = [[float('inf')] * (m + 1) for _ in range(n + 1)]
    take = [[False] * (m + 1) for _ in range(n + 1)]
    costs[0] = [0] * (m + 1)
    for i in range(1, n + 1):
        for j in range(i, m + 1):
            paired = costs[i-1][j-1] + abs(small[i-1]-large[j-1])
            skipped = costs[i][j-1]
            take[i][j] = paired <= skipped
            costs[i][j] = min(paired, skipped)
    result = []; i, j = n, m
    while i:
        if take[i][j]:
            a, b = small[i-1], large[j-1]
            result.append((b, a) if swapped else (a, b)); i -= 1
        j -= 1
    return result


def breakdown(reference, predictions):
    expected = _units(reference['members']); actual = _units(predictions)
    profiles = sorted({k[0] for k in expected} | {k[0] for k in actual})
    rows = []
    for profile in profiles:
        def records(units):
            return [dict(spec=p,view_tags=list(tags),length_mm=length,member_cnt=count)
                    for (p,tags,length),count in units.items() if p == profile]
        ref = {**reference,'members':records(expected)}; pred = records(actual)
        metric = score(ref,pred)
        buckets = defaultdict(lambda: [[],[]])
        unknown = [0,0]
        for side, units in enumerate((expected, actual)):
            for (p,tags,length),count in units.items():
                if p != profile: continue
                if length is None: unknown[side] += count
                else: buckets[tags][side].extend([length]*count)
        pairs = []
        for a,b in buckets.values(): pairs.extend(_pairs(sorted(a),sorted(b)))
        errors = [abs(a-b) for a,b in pairs]
        total = metric['expected_single_members']; reported = metric['reported_single_members']
        rows.append(dict(profile=profile,expected_count=total,reported_count=reported,
            count_error=reported-total,count_accuracy=max(0,1-abs(reported-total)/total) if total else 0,
            matched_count=metric['matched_single_members'],precision=metric['precision'],recall=metric['recall'],
            length_paired_count=len(pairs),length_within_1mm_count=sum(e<=1 for e in errors),
            length_accuracy=sum(e<=1 for e in errors)/len(errors) if errors else None,
            length_mae_mm=sum(errors)/len(errors) if errors else None,
            length_max_error_mm=max(errors) if errors else None,
            length_bias_mm=sum(b-a for a,b in pairs)/len(pairs) if pairs else None,
            length_mape=sum(abs(b-a)/a for a,b in pairs)/len(pairs) if pairs else None,
            expected_unpaired_length_count=total-len(pairs),reported_unpaired_length_count=reported-len(pairs),
            unknown_reference_length_count=unknown[0],unknown_reported_length_count=unknown[1]))
    ep={k[0] for k in expected}; ap={k[0] for k in actual}; common=ep&ap
    return dict(expected_profile_count=len(ep),reported_profile_count=len(ap),matched_profile_count=len(common),
        profile_precision=len(common)/len(ap) if ap else 0,profile_recall=len(common)/len(ep) if ep else 0,
        profile_set_accuracy=len(common)/len(ep|ap) if ep|ap else 0,
        missing_profiles=sorted(ep-ap),extra_profiles=sorted(ap-ep),profiles=rows,
        length_pairing_rule='同规格且完整所属面集合相同，按单根展开后最大配对数量、最小绝对长度误差；配对仅用于误差诊断，不证明同一实体。未配对项不进入MAE，但仍计入严格漏项/多计。')

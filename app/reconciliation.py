"""Deterministic screening for targeted post-takeoff reconciliation."""

import re
from collections import defaultdict


VIEW_ID_PATTERN = re.compile(r'f\d{3}-p\d{3}-view_selection\d{2}')


def source_view_ids(record):
    return set(VIEW_ID_PATTERN.findall(str(record.get('source', ''))))


def build_review_queue(records, view_types):
    """Return profile groups with evidence that warrants bounded reconciliation."""
    by_profile = defaultdict(list)
    for record in records:
        if record.get('profile'):
            by_profile[record['profile']].append(record)

    queue = []
    for profile, items in sorted(by_profile.items()):
        types = {
            view_types[view_id]
            for record in items
            for view_id in source_view_ids(record)
            if view_id in view_types
        }
        has_plan = 'PLAN' in types
        has_elevation = 'ELEVATION' in types
        beam_records = [item for item in items if 'beam' in str(item.get('role', '')).lower()]
        bracing_records = [item for item in items if 'bracing' in str(item.get('role', '')).lower()]
        vertical_records = [item for item in items if 'vertical' in str(item.get('role', '')).lower()]
        elevation_only = [item for item in items if 'elevation-only' in str(item.get('role', '')).lower()]
        expanded_angles = [item for item in items if int(item.get('quantity_multiplier') or 1) > 1]
        if not any((has_plan and has_elevation, has_plan and beam_records, bracing_records, vertical_records, elevation_only, expanded_angles)):
            continue
        roles = sorted({str(item.get('role', '')).strip().lower() for item in items if item.get('role')})
        reasons = []
        if has_plan and has_elevation:
            reasons.append('同规格构件同时由平面和立面证据支持，需排除投影重复。')
        if has_plan and beam_records:
            reasons.append('平面梁构件需核验网格/尺寸分段是否被误作物理拼接。')
        if bracing_records:
            reasons.append('斜撑需按实际画出的斜撑线逐条计数，避免网格或端点重复。')
        if vertical_records:
            reasons.append('竖向构件需覆盖每个立面标高区间，排除漏扫。')
        if elevation_only:
            reasons.append('立面补项必须证明在平面中缺失，且不得按投影重复新增。')
        if expanded_angles:
            reasons.append('2L/2LF 等组合角钢必须先核对画线数，再展开为物理件数。')
        if len(roles) > 1:
            reasons.append('同规格跨多种构件角色：' + '；'.join(roles) + '。')
        queue.append({
            'profile': profile,
            'member_ids': [item['member_id'] for item in items],
            'records': items,
            'view_ids': sorted({view_id for item in items for view_id in source_view_ids(item)}),
            'reasons': reasons,
        })
    return queue

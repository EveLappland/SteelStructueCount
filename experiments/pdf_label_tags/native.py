"""Native text occurrences -> view ownership and nearby named reference lines.

This experiment assumes one visible steel label per member/assembly. It does
not infer lengths, splice topology, or unique physical members across views.
All geometric distances are PDF points, never structural millimetres.
"""
from __future__ import annotations

from collections import Counter
import math
import re

from app.axis_catalog import axis_name
from app.discovery_inventory import PROFILE, labels
from app.member_recognition import normalize_view_tag
from app.native_pdf import inside


def center(box):
    return ((box[0]+box[2])/2, (box[1]+box[3])/2)


def point_box(box, page):
    return [v * (page['width_pt'] if i % 2 == 0 else page['height_pt']) for i, v in enumerate(box)]


def orientation(direction):
    x, y = direction
    if abs(x) >= .98:
        return 'HORIZONTAL'
    if abs(y) >= .98:
        return 'VERTICAL'
    return 'DIAGONAL'


def steel_occurrences(page):
    """Count native regex matches, including repeated equal text at other positions.

    Current extraction stores text per line. Multi-label lines are retained as
    separate occurrences but their shared bbox is explicitly marked ambiguous.
    No textual/value deduplication is performed.
    """
    result = []
    for text in page['texts']:
        matches = list(PROFILE.finditer(text['text']))
        for index, match in enumerate(matches):
            profile, multiplier = next(labels(match[0]))
            result.append({'id': f"{text['id']}:label{index+1}", 'source_text_id': text['id'],
                'spec': (f'{multiplier}LF-' if multiplier > 1 else '') + profile,
                'profile': profile, 'component_multiplier': multiplier, 'member_cnt': 1,
                'raw_text': text['text'], 'matched_text': match[0], 'text_range': list(match.span()),
                'bbox': text['bbox'], 'direction': text['style']['direction'],
                'font_size_pt': text['style']['font_size'],
                'orientation': orientation(text['style']['direction']),
                'position_issue': 'Several steel labels share a native text-line bbox' if len(matches) > 1 else ''})
    return result


def _bubble_edges(lines, box, font):
    """Reject compact parallel borders enclosing a name, including overprints.

    PDF exporters may emit the filled bubble and its strokes as different
    paths, so path identity alone cannot reliably exclude these edges.
    """
    excluded = set()
    c = center(box)
    for kind in ('HORIZONTAL', 'VERTICAL'):
        normal = 1 if kind == 'HORIZONTAL' else 0
        tangent = 1-normal
        width = box[tangent+2]-box[tangent]
        edges = [line for line in lines if line['orientation'] == kind
                 and line['span_pt'][0] <= c[tangent] <= line['span_pt'][1]
                 and .8*width <= line['length_pt'] <= width+4*font
                 and abs(line['coordinate_pt']-c[normal]) <= 1.5*font]
        below = [line for line in edges if line['coordinate_pt'] < c[normal]-.25*font]
        above = [line for line in edges if line['coordinate_pt'] > c[normal]+.25*font]
        if below and above:
            excluded.update(line['vector_id'] for line in below+above)
    return excluded


def _trace_reference(seed, lines, excluded, font, page):
    """Follow evidenced collinear segments across only small drafting gaps.

    No infinite extension through a view: an unconnected distant member does
    not acquire this reference. Every extension retains native vector IDs.
    """
    lo, hi = seed['span_pt']
    supported = {seed['vector_id']}
    changed = True
    while changed:
        changed = False
        for line in lines:
            if (line['vector_id'] in supported or line['vector_id'] in excluded
                    or line['orientation'] != seed['orientation']
                    or abs(line['coordinate_pt']-seed['coordinate_pt']) > .1*font):
                continue
            a, b = line['span_pt']
            if max(a-hi, lo-b, 0) > .5*font:
                continue
            supported.add(line['vector_id'])
            new_lo, new_hi = min(lo, a), max(hi, b)
            changed = changed or new_lo < lo or new_hi > hi
            lo, hi = new_lo, new_hi
    normal = 1 if seed['orientation'] == 'HORIZONTAL' else 0
    points = []
    for value in (lo, hi):
        p = [0., 0.]
        p[normal], p[1-normal] = seed['coordinate_pt'], value
        points.append([p[0]/page['width_pt'], p[1]/page['height_pt']])
    return {**seed, 'span_pt': [lo, hi], 'length_pt': hi-lo, 'points': points,
            'support_vector_ids': sorted(supported),
            'trace_tolerance_pt': .1*font, 'max_join_gap_pt': .5*font}


def reference_lines(page, view):
    """Attach each native axis-name bubble to an aligned, nearby PDF line.

    Long dashed lines receive preference over solid dimensions. No project
    names, axis sequence, drawing scale or expected member count is assumed.
    """
    candidates = []
    for vector in page['vectors']:
        if not inside(vector, view['bbox']):
            continue
        a, b = [(p[0]*page['width_pt'], p[1]*page['height_pt']) for p in vector['points']]
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = math.hypot(dx, dy)
        if length < 1:
            continue
        kind = orientation((dx/length, dy/length))
        # Native axis references in this experiment are horizontal/vertical.
        if kind == 'DIAGONAL':
            continue
        normal = 1 if kind == 'HORIZONTAL' else 0
        tangent = 1-normal
        candidates.append({'vector_id': vector['id'], 'orientation': kind,
            'coordinate_pt': (a[normal]+b[normal])/2,
            'span_pt': sorted([a[tangent], b[tangent]]), 'length_pt': length,
            'dashed': bool(re.search(r'\d', (vector['style'].get('dashes') or '').split(']')[0])),
            'points': vector['points']})
    result = []
    for text in page['texts']:
        if not inside(text, view['bbox']) or text['id'] == view['title_id']:
            continue
        name = axis_name(text['text'])
        if not name:
            continue
        box = point_box(text['bbox'], page)
        c = center(box)
        font = text['style']['font_size']
        excluded = _bubble_edges(candidates, box, font)
        possible = []
        for line in candidates:
            if line['vector_id'] in excluded:
                continue
            normal = 1 if line['orientation'] == 'HORIZONTAL' else 0
            tangent = 1-normal
            perp = abs(c[normal]-line['coordinate_pt'])
            lo, hi = line['span_pt']
            gap = max(lo-c[tangent], c[tangent]-hi, 0)
            if perp > font or gap > (box[tangent+2]-box[tangent])/2 + 4*font or line['length_pt'] < 2*font:
                continue
            score = perp/font*10 + gap/font + (0 if line['dashed'] else 4) - math.log1p(line['length_pt']/font)
            possible.append((score, line))
        possible.sort(key=lambda item: (item[0], item[1]['vector_id']))
        selected = _trace_reference(possible[0][1], candidates, excluded, font, page) if possible else None
        result.append({'name': name, 'source_text_id': text['id'], 'bbox': text['bbox'],
                       'line': selected, 'excluded_bubble_vector_ids': sorted(excluded),
                       'status': 'LINE_FOUND' if selected else 'NO_ALIGNED_LINE'})
    return result


def assign_axis(label, axes, page, source_plane):
    """Text direction selects reference-line family; proximity proposes ownership.

    Only an aligned, sufficiently close named line enters view_tags. Bracketing
    names are position context, not claims that a member belongs to both planes.
    """
    c = center(point_box(label['bbox'], page))
    eligible = []
    bracket = {}
    for kind in ('HORIZONTAL', 'VERTICAL'):
        normal = 1 if kind == 'HORIZONTAL' else 0
        lines = [a for a in axes if a['line'] and a['line']['orientation'] == kind]
        lines.sort(key=lambda a: a['line']['coordinate_pt'])
        lower = [a for a in lines if a['line']['coordinate_pt'] <= c[normal]]
        upper = [a for a in lines if a['line']['coordinate_pt'] > c[normal]]
        bracket[kind] = {'before': lower[-1]['name'] if lower else None,
                         'after': upper[0]['name'] if upper else None}
        if label['orientation'] != kind or label['position_issue']:
            continue
        for axis in lines:
            line = axis['line']
            lo, hi = line['span_pt']
            distance = abs(c[normal]-line['coordinate_pt'])
            tolerance = 2*label['font_size_pt']
            if distance <= tolerance and lo-tolerance <= c[1-normal] <= hi+tolerance:
                eligible.append((distance, axis))
    eligible.sort(key=lambda item: (item[0], item[1]['name']))
    # Multiple copies of one named reference are not different hypotheses.
    names = {a['name'] for _, a in eligible}
    selected = eligible[0] if len(names) == 1 else None
    tags = [source_plane] if source_plane else []
    evidence = None
    if selected:
        distance, axis = selected
        if axis['name'] not in tags:
            tags.append(axis['name'])
        evidence = {'axis_text_id': axis['source_text_id'], 'axis_vector_id': axis['line']['vector_id'],
                    'axis_support_vector_ids': axis['line'].get('support_vector_ids', [axis['line']['vector_id']]),
                    'distance_pt': round(distance, 4), 'tolerance_pt': round(2*label['font_size_pt'], 4),
                    'basis': 'Native text direction parallel to named reference line; text center nearby'}
    return {'view_tags': tags, 'axis_match': evidence, 'axis_context': bracket,
            'axis_candidates': [{'name': a['name'], 'distance_pt': round(d, 4)} for d, a in eligible],
            'tag_status': 'AXIS_PROPOSED' if selected else ('AMBIGUOUS' if names else 'SOURCE_PLANE_ONLY')}


def analyze(pages, regions):
    result = {'views': [], 'ownership_conflicts': [], 'outside_labels': []}
    for page in pages:
        local = [v for v in regions if v['page_id'] == page['id']]
        occurrences = steel_occurrences(page)
        owned = {v['id']: [] for v in local}
        for label in occurrences:
            owners = [v for v in local if inside(label, v['bbox'])]
            if len(owners) == 1:
                owned[owners[0]['id']].append(label)
            elif owners:
                result['ownership_conflicts'].append({**label, 'view_ids': [v['id'] for v in owners]})
            else:
                result['outside_labels'].append(label)
        for view in local:
            plane = normalize_view_tag(view['title'])
            steel = owned[view['id']]
            axes = reference_lines(page, view)
            steel.sort(key=lambda row: (row['bbox'][1], row['bbox'][0], row['id']))
            for number, label in enumerate(steel, 1):
                label.update(assign_axis(label, axes, page, plane), number=number,
                             view_id=view['id'], page_id=page['id'])
            counts = Counter(row['spec'] for row in steel)
            result['views'].append({**view, 'plane_name': plane, 'axes': axes, 'labels': steel,
                'width_pt': page['width_pt'], 'height_pt': page['height_pt'],
                'spec_counts': dict(sorted(counts.items())), 'label_count': len(steel)})
    all_labels = [l for v in result['views'] for l in v['labels']]
    specs = Counter(l['spec'] for l in all_labels)
    plane_names = sorted({name for v in result['views'] for name in
                          [v['plane_name'], *(a['name'] for a in v['axes'])]})
    result['summary'] = {'view_count': len(result['views']), 'label_count': len(all_labels),
        'steel_type_count': len(specs), 'spec_counts': dict(sorted(specs.items())),
        'axis_or_plane_name_count': len(plane_names), 'axis_or_plane_names': plane_names,
        'member_count_assuming_one_label_per_member': sum(l['member_cnt'] for l in all_labels),
        'axis_proposed': sum(l['tag_status'] == 'AXIS_PROPOSED' for l in all_labels),
        'source_plane_only': sum(l['tag_status'] == 'SOURCE_PLANE_ONLY' for l in all_labels),
        'ambiguous_axis': sum(l['tag_status'] == 'AMBIGUOUS' for l in all_labels),
        'ownership_conflicts': len(result['ownership_conflicts']), 'outside_labels': len(result['outside_labels'])}
    result['limitations'] = [
        'One native steel specification occurrence is counted as one member/assembly, per experiment premise.',
        'Axis tags are geometric proximity proposals, not a verified 3D member model.',
        'Source-plane-only members may be between named axes or diagonal; no nearest-axis fabrication.',
        'No length calculation, splice splitting, cross-view deduplication or API member recognition.']
    return result

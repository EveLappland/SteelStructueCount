"""Immutable PDF text facts; model geometry remains an explicitly separate claim."""
import os
import re

from .native_pdf import inside


def review_policy():
    mode = os.getenv('LENGTH_REVIEW_MODE', 'economy').strip().lower()
    if mode not in {'economy', 'full'}:
        raise ValueError('LENGTH_REVIEW_MODE must be economy or full')
    calls = int(os.getenv('LENGTH_REVIEW_MAX_CALLS', '2' if mode == 'economy' else '12'))
    if calls < 1:
        raise ValueError('LENGTH_REVIEW_MAX_CALLS must be positive')
    return {'mode': mode, 'max_calls_per_view': calls}


def claim_length_call(item, policy):
    """Charge before dispatch, including failed requests and format retries."""
    used = item.get('length_calls', 0)
    if used >= policy['max_calls_per_view']:
        return False
    item['length_calls'] = used + 1
    item['status']['length_review_calls'] = used + 1
    return True


def text_values(text):
    """Only unambiguous dimension/level tokens; do not mine profile sizes as spans."""
    level = re.fullmatch(r'EL\.?\s*([+-]?\d+(?:\.\d+)?)\s*(?:\([^)]*\))?', text.strip(), re.I)
    if level:
        return [round(float(level[1]) * 1000, 6)]
    dimension = re.fullmatch(r'([+]?[\d]+(?:\.\d+)?)\s*(mm|m)?', text.strip(), re.I)
    if dimension:
        return [round(float(dimension[1]) * (1000 if (dimension[2] or '').lower() == 'm' else 1), 6)]
    return []


def build_facts(page, view):
    box = view['bbox']
    width, height = box[2] - box[0], box[3] - box[1]
    return {'view_id': view['id'], 'page_id': page['id'], 'page': page['page'],
            'file': page['file'], 'page_crop_bbox': box,
            'coordinate_system': 'bbox_view normalized to original view, bbox_page normalized to PDF page',
            'facts': [{'id': t['id'], 'text': t['text'], 'bbox_page': t['bbox'],
                       'bbox_view': [round((t['bbox'][i] - box[i % 2]) / (width if i % 2 == 0 else height), 6)
                                     for i in range(4)],
                       'values_mm': text_values(t['text'])}
                      for t in page['texts'] if inside(t, box)],
            'limits': 'PDF text and location are source facts. Dimension baselines, endpoints, splice and member association are model claims, not program-verified facts.'}


def check_sources(references, registry):
    facts = {f['id']: f for f in registry.get('facts', [])}
    checks = []
    for ref in references:
        ids = ref.get('source_ids', [])
        known = [facts[i] for i in ids if i in facts]
        if not ids or len(known) != len(ids):
            checks.append({'check': 'source_identity', 'reference_id': ref['id'],
                           'message': '尺寸需要引用本视图原始PDF文字ID；缺失或未知ID不算已取得文字依据。'})
        elif not any(abs(value - ref['value_mm']) <= .001 for f in known for value in f['values_mm']):
            checks.append({'check': 'source_value', 'reference_id': ref['id'],
                           'message': '返回尺寸无法与所引用PDF原文的数值/单位对应，不能把推算值冒充原始标注。'})
        if not ref.get('baseline_basis'):
            checks.append({'check': 'baseline_claim', 'reference_id': ref['id'],
                           'message': '需说明尺寸起止基准及如何对应构件端点；文字位置本身不是尺寸基准。'})
        if not ref.get('start') or (ref['kind'] != 'LEVEL' and not ref.get('end')):
            checks.append({'check': 'baseline_anchors', 'reference_id': ref['id'],
                           'message': '需要尺寸真实起止基准点；标高至少需要一个基准点，不能只给文字框。'})
    return checks


def row_source_checks(resolution, references, registry):
    selected = [r for r in references if r['id'] in resolution['reference_ids']]
    checks = check_sources(selected, registry)
    if not selected:
        checks.append({'check': 'missing_dimension_source', 'message': '没有原始尺寸引用。'})
    kinds = resolution.get('endpoint_kinds') or []
    if len(kinds) != 2 or any(k not in {'END', 'SPLICE'} for k in kinds):
        checks.append({'check': 'endpoint_claim', 'message': '两个端点须分别说明真实端部或拼接；相交点、轴线或裁片边界不能独自证明分段。'})
    return checks

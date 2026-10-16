"""Deterministic checks and bounded feedback for one drawing view."""
import json
from collections import Counter
from pathlib import Path

from .discovery_inventory import labels
from .geometry import section
from .native_pdf import inside


def assess_rows(rows, page, view):
    native = Counter(profile for text in page['texts'] if inside(text, view['bbox'])
                     for profile, _ in labels(text['text']))
    recognized = Counter()
    for row in rows:
        recognized[section(row['spec'])[0]] += row['member_cnt']
    coverage = [{'spec': spec, 'label_count': native[spec], 'member_cnt': recognized[spec]}
                for spec in sorted(native.keys() | recognized.keys()) if native[spec] != recognized[spec]]
    feedback = []
    for item in coverage:
        feedback.append({'check': 'profile_label_coverage', **item,
                         'message': 'PDF文字中该规格的标签数与输出候选段数不同。标签可重复，拼接也可能造成差异；请按图复查并在 issue 说明不能消除的差异。'})
    for row in rows:
        for check in row['checks']:
            # The model's own uncertainty is retained in the ledger, but only
            # locally verifiable errors drive another model round.
            if check == row.get('issue'):
                continue
            feedback.append({'check': 'row_validation', 'row_id': row['id'],
                             'spec': row['spec'], 'message': check})
    if not rows:
        feedback.append({'check': 'empty_ledger', 'message': '本视图返回空台账；请核对是否确实没有构件标签。'})
    return {'label_count': sum(native.values()), 'member_cnt': sum(r['member_cnt'] for r in rows),
            'coverage_differences': coverage, 'feedback': feedback}


def revision_prompt(original_prompt, previous, feedback, round_number):
    guidance = (Path(__file__).parent / 'prompts' / 'stage_two_revision.md').read_text(encoding='utf-8')
    data = {'round': round_number, 'previous_ledger': previous, 'local_checks': feedback}
    return original_prompt + '\n\n' + guidance + '\n' + json.dumps(data, ensure_ascii=False, separators=(',', ':'))

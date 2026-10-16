"""Deterministic checks and bounded feedback for one drawing view."""
import json
from collections import Counter
from pathlib import Path

from .discovery_inventory import labels
from .geometry import section
from .native_pdf import inside


def assess_rows(rows, page, view):
    # Each extracted profile label denotes one drawn member. A ledger row may
    # group several members, so compare against the sum of member_cnt, not rows.
    native = Counter(profile for text in page['texts'] if inside(text, view['bbox'])
                     for profile, _ in labels(text['text']))
    steel_counts = Counter()
    for row in rows:
        steel_counts[section(row['spec'])[0]] += row['member_cnt']
    coverage = [{'spec': spec, 'label_count': native[spec], 'member_cnt': steel_counts[spec]}
                for spec in sorted(native.keys() | steel_counts.keys()) if native[spec] != steel_counts[spec]]
    feedback = []
    for item in coverage:
        feedback.append({'check': 'profile_label_coverage', **item,
                         'message': 'PDF文字中该规格的钢材标签数与台账 member_cnt 合计不同。请按图核对每根带标签的钢材及PDF文字抽取结果；不能只凭数量差异增删构件。'})
    for row in rows:
        for check in row['checks']:
            # The model's own uncertainty is retained in the ledger, but only
            # locally verifiable errors drive another model round.
            if check == row.get('issue'):
                continue
            feedback.append({'check': 'row_validation', 'row_id': row['id'],
                             'spec': row['spec'], 'message': check})
    if not rows and native:
        feedback.append({'check': 'empty_ledger', 'message': '本视图返回空台账，但PDF文字中有钢材标签；请核对是否漏识别。'})
    return {'label_count': sum(native.values()), 'member_cnt': sum(steel_counts.values()),
            'coverage_differences': coverage, 'feedback': feedback}


def revision_prompt(original_prompt, previous, feedback, round_number):
    guidance = (Path(__file__).parent / 'prompts' / 'stage_two_revision.md').read_text(encoding='utf-8')
    data = {'round': round_number, 'previous_ledger': previous, 'local_checks': feedback}
    return original_prompt + '\n\n' + guidance + '\n' + json.dumps(data, ensure_ascii=False, separators=(',', ':'))

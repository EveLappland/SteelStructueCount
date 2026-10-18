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


def revision_regressions(previous_rows, proposed_rows, current_tag):
    """Detect lost information between two model rounds, without claiming it is true.

    Native labels support base-profile counts, but cannot settle combination
    prefixes, image-only plane tags or dimensions. Keep those observations until
    the model explicitly reconciles them; never reward their silent deletion.
    """
    prior_angles = Counter()
    next_angles = Counter()
    prior_tags = set()
    next_tags = set()
    for rows, angles, tags in ((previous_rows, prior_angles, prior_tags),
                               (proposed_rows, next_angles, next_tags)):
        for row in rows:
            _, multiplier = section(row['spec'])
            if multiplier > 1:
                angles[multiplier] += row['member_cnt']
            tags.update(tag for tag in row['view_tags'] if tag != current_tag)
    feedback = []
    for multiplier, count in sorted(prior_angles.items()):
        if next_angles[multiplier] < count:
            feedback.append({'check': 'combination_identity_regression',
                             'component_count': multiplier, 'previous_member_cnt': count,
                             'proposed_member_cnt': next_angles[multiplier],
                             'message': '修订后组合前缀或组合段数减少；请依据图片核对，不要仅为消除 PDF 文字差异而改成单角钢。'})
    for tag in sorted(prior_tags - next_tags):
        feedback.append({'check': 'view_tag_regression', 'view_tag': tag,
                         'message': '修订后丢失此前识别的跨面名称；请依据图片核对，第一阶段当前视图目录可能不完整。'})
    old_missing = sum(row['member_cnt'] for row in previous_rows if row['length_mm'] is None)
    new_missing = sum(row['member_cnt'] for row in proposed_rows if row['length_mm'] is None)
    if new_missing > old_missing:
        feedback.append({'check': 'length_regression', 'previous_missing': old_missing,
                         'proposed_missing': new_missing,
                         'message': '修订后缺少长度的钢材增加；请核对是否丢失已识别的尺寸。'})
    return feedback


def revision_prompt(original_prompt, previous, feedback, round_number):
    guidance = (Path(__file__).parent / 'prompts' / 'stage_two_revision.md').read_text(encoding='utf-8')
    data = {'round': round_number, 'previous_ledger': previous, 'local_checks': feedback}
    return original_prompt + '\n\n' + guidance + '\n' + json.dumps(data, ensure_ascii=False, separators=(',', ':'))

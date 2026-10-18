"""Pure local coverage checks; no model API or simulated API response."""
import unittest

from app.stage_two_iteration import assess_rows, revision_regressions
from app.geometry import section


class StageTwoCoverageTests(unittest.TestCase):
    def setUp(self):
        self.view = {'bbox': [0, 0, 1, 1]}
        self.rows = [
            {'id': 'm1', 'spec': 'HEA240', 'member_cnt': 1, 'checks': [], 'issue': None},
            {'id': 'm2', 'spec': 'HEA260', 'member_cnt': 2, 'checks': [], 'issue': None},
        ]

    def test_two_ledger_rows_can_describe_three_labeled_members(self):
        page = {'texts': [
            {'text': profile, 'bbox': [0.1 * index, 0.1, 0.1 * index + 0.05, 0.15]}
            for index, profile in enumerate(('HEA240', 'HEA260', 'HEA260'), 1)
        ]}
        result = assess_rows(self.rows, page, self.view)
        self.assertEqual(result['label_count'], 3)
        self.assertEqual(result['member_cnt'], 3)
        self.assertEqual(result['coverage_differences'], [])
        self.assertEqual(result['feedback'], [])

    def test_one_label_for_two_members_is_a_real_count_difference(self):
        page = {'texts': [
            {'text': 'HEA240', 'bbox': [0.1, 0.1, 0.2, 0.2]},
            {'text': 'HEA260', 'bbox': [0.3, 0.1, 0.4, 0.2]},
        ]}
        result = assess_rows(self.rows, page, self.view)
        self.assertEqual(result['coverage_differences'],
                         [{'spec': 'HEA260', 'label_count': 1, 'member_cnt': 2}])

    def test_empty_view_without_labels_needs_no_coverage_retry(self):
        self.assertEqual(assess_rows([], {'texts': []}, self.view)['feedback'], [])

    def test_pdf_angle_label_and_model_2f_combination_compare_as_one_member(self):
        page = {'texts': [{'text': 'L120*12', 'bbox': [.1, .1, .2, .2]}]}
        rows = [{'id': 'angle', 'spec': '2F-L120*12', 'member_cnt': 1,
                 'checks': [], 'issue': None}]
        self.assertEqual(section('2F-L120*12'), ('L120*12', 2))
        self.assertEqual(assess_rows(rows, page, self.view)['feedback'], [])

    def test_revision_flags_lost_combination_tags_and_length(self):
        before = [{'spec': '2F-L120*12', 'member_cnt': 2,
                   'view_tags': ['EL.1', 'FRAME/A'], 'length_mm': 5000}]
        after = [{'spec': 'L120*12', 'member_cnt': 2,
                  'view_tags': ['EL.1'], 'length_mm': None}]
        checks = revision_regressions(before, after, 'EL.1')
        self.assertEqual({item['check'] for item in checks},
                         {'combination_identity_regression', 'view_tag_regression', 'length_regression'})
        equivalent = [{**before[0], 'spec': '2LF-L120*12'}]
        self.assertEqual(revision_regressions(before, equivalent, 'EL.1'), [])
        corrected_base = [{**before[0], 'spec': '2F-L100*10'}]
        self.assertEqual(revision_regressions(before, corrected_base, 'EL.1'), [])


if __name__ == '__main__':
    unittest.main()

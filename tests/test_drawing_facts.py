"""Local provenance and budget policies; no generated API conversations."""
import os
import unittest
from unittest.mock import patch

from app.drawing_facts import build_facts, check_sources, review_policy, row_source_checks, text_values, claim_length_call


class DrawingFactTests(unittest.TestCase):
    def test_crop_mapping_preserves_original_identity_and_text(self):
        page = {'id': 'page-q', 'page': 2, 'file': 'drawing.pdf', 'texts': [
            {'id': 't1', 'text': '2500', 'bbox': [.3, .4, .4, .5]},
            {'id': 't2', 'text': 'HEA999', 'bbox': [.8, .8, .9, .9]}]}
        facts = build_facts(page, {'id': 'view-q', 'bbox': [.2, .3, .6, .7]})
        self.assertEqual(len(facts['facts']), 1)
        fact = facts['facts'][0]
        self.assertEqual(fact['id'], 't1')
        self.assertEqual(fact['bbox_page'], [.3, .4, .4, .5])
        self.assertEqual(fact['bbox_view'], [.25, .25, .5, .5])
        self.assertEqual(fact['values_mm'], [2500])

    def test_units_and_profile_numbers_are_not_mixed(self):
        self.assertEqual(text_values('EL.12.300(TOS)'), [12300])
        self.assertEqual(text_values('2.5 m'), [2500])
        self.assertEqual(text_values('2500 mm'), [2500])
        self.assertEqual(text_values('HEA250'), [])
        self.assertEqual(text_values('2LF-L120*12'), [])

    def test_source_identity_value_and_geometry_claims_are_independent(self):
        registry = {'facts': [{'id': 't1', 'values_mm': [2500]}]}
        reference = {'id': 'D1', 'kind': 'DIMENSION', 'source_ids': ['t1'], 'value_mm': 2500,
                     'baseline_basis': 'dimension extension lines A to B',
                     'start': {'name': 'A', 'point': [0, 0]}, 'end': {'name': 'B', 'point': [1, 0]}}
        self.assertEqual(check_sources([reference], registry), [])
        self.assertTrue(check_sources([{**reference, 'value_mm': 1250}], registry))
        self.assertTrue(check_sources([{**reference, 'source_ids': ['invented']}], registry))
        self.assertTrue(check_sources([{**reference, 'end': None}], registry))
        relation = {'reference_ids': ['D1'], 'endpoint_kinds': ['END', 'INTERSECTION']}
        self.assertIn('endpoint_claim', [c['check'] for c in row_source_checks(relation, [reference], registry)])
        # Passing this gate still does not claim geometry is program-proven.
        relation['endpoint_kinds'] = ['END', 'SPLICE']
        self.assertEqual(row_source_checks(relation, [reference], registry), [])

    def test_economy_defaults_and_explicit_policy(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(review_policy(), {'mode': 'economy', 'max_calls_per_view': 2})
        with patch.dict(os.environ, {'LENGTH_REVIEW_MODE': 'full', 'LENGTH_REVIEW_MAX_CALLS': '5'}):
            self.assertEqual(review_policy()['max_calls_per_view'], 5)
        with patch.dict(os.environ, {'LENGTH_REVIEW_MAX_CALLS': '0'}):
            with self.assertRaises(ValueError):
                review_policy()

    def test_failed_attempts_also_consume_budget_and_views_are_separate(self):
        policy = {'max_calls_per_view': 2}
        item = {'status': {}}
        self.assertTrue(claim_length_call(item, policy))
        self.assertTrue(claim_length_call(item, policy))
        self.assertFalse(claim_length_call(item, policy))
        self.assertEqual(item['status']['length_review_calls'], 2)
        self.assertTrue(claim_length_call({'status': {}}, policy))


if __name__ == '__main__':
    unittest.main()

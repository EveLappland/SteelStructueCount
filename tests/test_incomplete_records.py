import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from openai.types.chat import ChatCompletion

from app.drawing_input import crop_entries
from app.llm_runner import _apply_lengths, _apply_review_decisions, _call, _normalise


def member(identity, quantity=2, profile='L50X5'):
    return dict(member_id=identity, profile=profile, quantity=quantity,
                material='Q235', status='CONFIRMED', decision='ADD', length_mm=1000)


def response(payload):
    return ChatCompletion(id='test', created=0, model='test', object='chat.completion',
                          choices=[dict(index=0, finish_reason='stop',
                                        message=dict(role='assistant', content=json.dumps(payload)))])


class IncompleteRecordsTest(unittest.TestCase):
    def test_lengths_cannot_reduce_locked_quantity(self):
        draft = _normalise(member('tie', 10, 'IPET270'), 0)
        records = _apply_lengths([draft], {'lengths': [{'member_id': 'tie', 'groups': [
            {'quantity': 1, 'length_mm': 40000, 'source': 'plan', 'evidence': 'continuous line'}]}]})
        self.assertEqual(records[0]['quantity'], 10)
        self.assertEqual(records[0]['status'], 'CONFIRMED')
        self.assertIsNone(records[0]['length_mm'])

    def test_length_partition_preserves_count_and_profile(self):
        draft = _normalise(member('beam', 3, 'HEA240'), 0)
        records = _apply_lengths([draft], {'lengths': [{'member_id': 'beam', 'groups': [
            {'quantity': 2, 'length_mm': 8000, 'source': 'plan', 'evidence': 'dimension'},
            {'quantity': 1, 'length_mm': 5500, 'source': 'plan', 'evidence': 'splice dimension'}]}]})
        self.assertEqual(sum(item['quantity'] for item in records), 3)
        self.assertTrue(all(item['profile'] == 'HEA240' and item['status'] == 'CONFIRMED' for item in records))

    def test_valid_quantity_review_promotes_partial_record_to_confirmed(self):
        draft = _normalise({**member('brace', 32, 'IPET270'), 'status': 'PARTIAL',
                            'decision': 'UNRESOLVED'}, 0)
        spans = [f'span {index + 1}' for index in range(40)]
        records, changes = _apply_review_decisions([draft], [{
            'member_id': 'brace', 'action': 'ADJUST_QUANTITY', 'quantity': 40,
            'boundary_evidence': {'kind': 'MEMBER_MARK', 'view_id': 'plan', 'detail': '40 individually marked spans'},
            'physical_spans': spans, 'reason': '40 visible physical spans',
        }])
        self.assertEqual(records[0]['quantity'], 40)
        self.assertEqual(records[0]['status'], 'CONFIRMED')
        self.assertEqual(records[0]['decision'], 'UPDATE')
        self.assertEqual(changes[0]['after_quantity'], 40)

    def test_review_can_add_member_missing_from_pass_one(self):
        records, changes = _apply_review_decisions([_normalise(member('existing'), 0)], [
            {'member_id': 'existing', 'action': 'KEEP', 'reason': 'visible'},
            {
                'member_id': 'NEW-HEA160-TIER', 'action': 'ADD_MISSING',
                'profile': 'HEA160', 'material': 'S355JR', 'quantity': 2,
                'source': 'elevation-01', 'level': 'EL.110.500 to EL.115.000',
                'location': 'alignment A and B', 'role': 'vertical',
                'physical_spans': ['A tier vertical', 'B tier vertical'],
                'length_mm': 4500, 'length_method': 'LEVEL_DIFFERENCE',
                'reason': 'tier was absent from Pass 1',
            },
        ])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]['profile'], 'HEA160')
        self.assertEqual(records[1]['status'], 'CONFIRMED')
        self.assertEqual(changes[-1]['action'], 'ADD_MISSING')

    def test_split_replaces_merged_cross_node_batch(self):
        draft = _normalise(member('merged', 2, 'HEA240'), 0)
        records, changes = _apply_review_decisions([draft], [{
            'member_id': 'merged', 'action': 'SPLIT', 'reason': 'column node is a member end',
            'boundary_evidence': {'kind': 'END_CONNECTION', 'view_id': 'detail', 'detail': 'end plates on both sides'},
            'replacement_records': [
                {'member_id': 'merged-S1', 'quantity': 2, 'location': 'grid 5-4',
                 'physical_spans': ['A/5-4', 'B/5-4'], 'length_mm': 8000,
                 'length_method': 'DIMENSIONED'},
                {'member_id': 'merged-S2', 'quantity': 2, 'location': 'grid 4-3',
                 'physical_spans': ['A/4-3', 'B/4-3'], 'length_mm': 8000,
                 'length_method': 'DIMENSIONED'},
            ],
        }])
        self.assertEqual([item['member_id'] for item in records], ['merged-S1', 'merged-S2'])
        self.assertTrue(all(item['status'] == 'CONFIRMED' for item in records))
        self.assertEqual(changes[0]['after_quantity'], 4)

    def test_merge_into_removes_false_segment_and_requires_recalculated_target(self):
        target = _normalise(member('full-span-target', 2, 'HEA300'), 0)
        donor = _normalise(member('false-half', 2, 'HEA300'), 1)
        records, changes = _apply_review_decisions([target, donor], [
            {
                'member_id': 'full-span-target', 'action': 'ADJUST_QUANTITY',
                'boundary_evidence': {'kind': 'CONTINUITY_DETAIL', 'view_id': 'detail', 'detail': 'one member mark covers full span'},
                'quantity': 2, 'location': 'alignments A and B, full grid 2-3 span',
                'physical_spans': ['A:2-3', 'B:2-3'], 'length_mm': 16000,
                'length_method': 'DIMENSIONED', 'reason': 'continuous through node',
            },
            {
                'member_id': 'false-half', 'action': 'MERGE_INTO',
                'boundary_evidence': {'kind': 'CONTINUITY_DETAIL', 'view_id': 'detail', 'detail': 'same member mark covers both halves'},
                'target_member_id': 'full-span-target', 'reason': 'not a physical break',
            },
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['member_id'], 'full-span-target')
        self.assertEqual(records[0]['length_mm'], 16000)
        self.assertEqual(changes[-1]['action'], 'MERGE_INTO')
        self.assertEqual(changes[-1]['after_quantity'], 0)

    def test_incomplete_addition_is_not_accepted(self):
        draft = _normalise(member('existing'), 0)
        records, changes = _apply_review_decisions([draft], [{
            'member_id': 'NEW-BAD', 'action': 'ADD_MISSING', 'profile': 'HEA160',
            'quantity': 2, 'physical_spans': ['only one span'], 'reason': 'incomplete',
        }])
        self.assertEqual(len(records), 1)
        self.assertEqual(changes, [])

    def test_geometry_only_cannot_merge_ten_panels_into_one(self):
        draft = _normalise(member('tie', 10, 'IPET270'), 0)
        records, changes = _apply_review_decisions([draft], [{
            'member_id': 'tie', 'action': 'ADJUST_QUANTITY', 'quantity': 1,
            'length_mm': 40000, 'physical_spans': ['continuous drawing line'],
            'reason': 'no splice shown',
        }])
        self.assertEqual(records[0]['quantity'], 10)
        self.assertEqual(records[0]['length_mm'], 1000)
        self.assertEqual(records[0]['status'], 'PARTIAL')
        self.assertEqual(changes[0]['action'], 'UNRESOLVED')

    def test_model_crop_boxes_are_validated_and_bound_to_pdf_page(self):
        pages = [dict(id='f000-p001', file='drawing.pdf', page=1, pages=1,
                      source_path='drawing.pdf', text='')]
        regions = [
            dict(page_id='f000-p001', bbox=[.1, .2, .6, .7], label='Level plan', view_type='PLAN'),
            dict(page_id='missing', bbox=[0, 0, 1, 1]),
            dict(page_id='f000-p001', bbox=[.1, .1, .11, .11]),
        ]
        crops = crop_entries(pages, regions, prefix='test')
        self.assertEqual(len(crops), 1)
        self.assertEqual(crops[0]['parent_id'], 'f000-p001')
        self.assertEqual(crops[0]['id'], 'f000-p001-test01')
        self.assertLess(crops[0]['crop'][0], .1)

    def test_invalid_values_remain_unresolved(self):
        for value in (None, 0, -1, '', 'unknown', 1.5, True, 'NaN', 'Infinity'):
            with self.subTest(value=value):
                item = _normalise(member('bad', quantity=value), 8)
                self.assertIsNone(item['quantity'])
                self.assertEqual(item['status'], 'PARTIAL')
                self.assertEqual(item['decision'], 'UNRESOLVED')
        for profile in (None, '', '  ', 'UNKNOWN'):
            self.assertEqual(_normalise(member('bad', profile=profile), 0)['status'], 'PARTIAL')
        self.assertEqual(_normalise(member('ok', quantity='3'), 0)['quantity'], 3)
        self.assertEqual(_normalise(None, 0)['status'], 'PARTIAL')

    def test_raw_response_saved_before_json_parse_failure(self):
        client = MagicMock()
        raw = response({})
        raw.choices[0].message.content = 'not json'
        client.chat.completions.create.return_value = raw
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'response.json'
            with self.assertRaises(json.JSONDecodeError):
                _call(client, 'test', 'test', [], path)
            self.assertIn('not json', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

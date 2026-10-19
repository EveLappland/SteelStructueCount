import hashlib
import json
import unittest

from app.group_proposal import GroupProposal, unallocated_usable_rows
from app.pipeline import parse_model_content


class GroupIdTests(unittest.TestCase):
    def row(self, identity):
        return dict(id=identity, spec='HEA240', view_tags=['FRAME/A'],
                    length_mm=5448.85, member_cnt=2,
                    allocations=[dict(row_id='original-observation', member_cnt=2)],
                    reason='原模型依据')

    def parse(self, rows):
        return parse_model_content(json.dumps(dict(resolutions=rows)), GroupProposal, {})

    def test_descriptive_id_is_stable_and_only_id_changes(self):
        for identity in ['G-A-HEA240-5448.85', '组/A', 'x' * 200]:
            original = self.row(identity)
            actual = self.parse([original])['resolutions'][0]
            self.assertRegex(actual['id'], r'^[A-Za-z0-9_-]{1,120}$')
            self.assertEqual(actual, self.parse([original])['resolutions'][0])
            self.assertEqual({**actual, 'id': identity}, original)
        self.assertEqual(self.parse([self.row('good-ID')])['resolutions'][0]['id'], 'good-ID')

    def test_duplicates_and_normalized_collisions_are_rejected(self):
        for identity in ['good-ID', 'bad.ID']:
            with self.assertRaisesRegex(ValueError, 'ID不可重复'):
                self.parse([self.row(identity), self.row(identity)])
        canonical = 'group-' + hashlib.sha256(b'bad.ID').hexdigest()
        with self.assertRaisesRegex(ValueError, 'ID不可重复'):
            self.parse([self.row('bad.ID'), self.row(canonical)])

    def test_other_contract_errors_are_not_repaired(self):
        for changes in [dict(id=None), dict(id=''), dict(member_cnt=0), dict(length_mm=-1)]:
            with self.assertRaises(ValueError):
                self.parse([{**self.row('bad.ID'), **changes}])

    def test_unknown_profile_stays_pending_while_known_rows_require_allocation(self):
        two={'profile_catalog':['HEA240'],'view_members':[
            dict(id='known',spec='HEA240',length_mm=1000,view_tags=['FRAME/A']),
            dict(id='unknown',spec='L60*10',length_mm=1000,view_tags=['FRAME/A'])]}
        result={'review':{'invalid_rows':[]},'exceptions':[
            dict(observation_id='known',member_cnt=1),dict(observation_id='unknown',member_cnt=2)]}
        original=json.dumps(result)
        self.assertEqual(unallocated_usable_rows(two,result),[result['exceptions'][0]])
        self.assertEqual(json.dumps(result),original)

"""Stage-three invariants against recorded real stage-two outputs, no API doubles."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from app.group_reconciliation import reconcile_exact, reconcile, source_hash
from app.member_recognition import member_key

ROOT = Path(__file__).resolve().parents[1]


class RecordedStageThreeTests(unittest.TestCase):
    def test_real_reply_replay_changes_only_automatic_lengths_not_member_identity(self):
        old=ROOT/'tmp/full_economy_02_05/stage_2.json'
        new=ROOT/'tmp/full_economy_02_05_repaired/stage_2.json'
        if not old.is_file() or not new.is_file():
            self.skipTest('requires completed real economy run and deterministic replay')
        before=json.loads(old.read_text(encoding='utf-8'))
        after=json.loads(new.read_text(encoding='utf-8'))
        identities=lambda d:[(r['id'],r['spec'],r['member_cnt'],r['view_tags'],r['source_view']) for r in d['view_members']]
        self.assertEqual(identities(before),identities(after))
        originals={r['id']:r for r in before['view_members']}
        for row in after['view_members']:
            if originals[row['id']]['length_mm']!=row['length_mm']:
                self.assertEqual(row['length_arbitration']['final_expression']['role'],'arbiter')
                self.assertTrue(row['length_arbitration']['automatic'])
        report=json.loads((new.parent/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['requests_by_stage'],{})
        self.assertTrue(report['checks']['no_manual_edits'])
        self.assertTrue(report['checks']['all_observations_accounted_for'])

    def test_exact_reconciliation_preserves_keys_sources_and_accounts_for_unknowns(self):
        files = [ROOT / 'tmp/arbitration_02_test/stage_two.json',
                 ROOT / 'tmp/arbitration_04_all_review/stage_two.json']
        files = [p for p in files if p.is_file()]
        if not files:
            self.skipTest('requires existing real stage-two responses')
        for path in files:
            with self.subTest(source=path.parent.name):
                two = json.loads(path.read_text(encoding='utf-8'))
                snapshot = deepcopy(two)
                before = source_hash(two)
                three = reconcile_exact(two)
                self.assertEqual(two, snapshot)
                self.assertEqual(before, three['source_hash'])
                self.assertEqual(three, reconcile(two, three))
                self.assertFalse(three['exclusions'])
                rows = {r['id']: r for r in two['view_members']}
                allocated = set()
                for group in three['confirmed_groups']:
                    for allocation in group['allocations']:
                        allocated.add(allocation['row_id'])
                        self.assertEqual(member_key(group), member_key(rows[allocation['row_id']]))
                pending = {e['observation_id'] for e in three['exceptions']}
                self.assertEqual(set(rows), allocated | (pending & set(rows)))
                self.assertTrue(all(r['id'] in pending for r in rows.values() if r['length_mm'] is None))
                self.assertEqual(len(three['members']), sum(g['member_cnt'] * g['component_multiplier']
                                                            for g in three['confirmed_groups']))
                self.assertEqual(sum(s['quantity'] for s in three['summary']), len(three['members']))


if __name__ == '__main__':
    unittest.main()

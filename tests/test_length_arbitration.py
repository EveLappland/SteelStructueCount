"""Pure contracts and saved real replies; no mocked LLM/API conversations."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.ai_concurrency import view_concurrency
from app.grid_workflow import Task, ledger_rows
from app.length_arbitration import (ArbitrationResponse, EvidencePlan,
                                    LengthArbitration, candidate)
from app.length_expert import LengthExpertResponse, assess_reply

ROOT = Path(__file__).resolve().parents[1]


class ArbitrationContractTests(unittest.TestCase):
    def test_env_limit_and_explicit_override_and_five_default(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(view_concurrency(), 5)
        with patch.dict('os.environ', {'LLM_MAX_CONCURRENCY': '7'}):
            self.assertEqual(view_concurrency(), 7)
            self.assertEqual(view_concurrency({'max_concurrency': 2}), 2)

    def test_roles_use_shared_task_contract_and_unique_request_ids(self):
        for kind, contract in [('length_evidence', EvidencePlan),
                               ('length_verifier', LengthExpertResponse),
                               ('length_arbiter', ArbitrationResponse)]:
            task = Task('drawing-q', kind, {}, 'prompt', round=1)
            self.assertIs(task.contract, contract)
            self.assertIn(kind, task.name)

    def test_candidate_arithmetic_rejects_unknown_and_keeps_combination_identity(self):
        result = candidate('row-q', 'master', {'method': 'DIAGONAL',
            'horizontal_mm': 3, 'vertical_mm': 4, 'evidence': 'pure arithmetic fixture'})
        self.assertEqual(result['length_mm'], 5)
        self.assertTrue(result['selectable'])
        self.assertFalse(candidate('row-q', 'expert', None)['selectable'])
        self.assertFalse(candidate('row-q', 'expert', result['length_input'], errors=['bad reference'])['selectable'])


class RecordedArbitrationTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / 'tmp/stage12_length_expert_04_test'
        if not (root / 'report.json').exists():
            self.skipTest('requires previously saved real API run')
        self.one = json.loads((root / 'stage_one.json').read_text(encoding='utf-8'))
        data = json.loads((root / 'stage_two.json').read_text(encoding='utf-8'))
        run = json.loads((root / 'report.json').read_text(encoding='utf-8'))
        self.view = self.one['views'][0]
        analysis = data['view_analysis'][0]
        path = ROOT / 'tmp/length_expert_feature_server/data/jobs' / run['job_id'] / analysis['trace_file']
        master = json.loads((path / 'master_result.json').read_text(encoding='utf-8'))
        self.rows = ledger_rows(master, self.view, self.one, [])
        expert = json.loads((path / 'length_expert/result.json').read_text(encoding='utf-8'))
        self.plan = json.loads((path / 'input.json').read_text(encoding='utf-8'))['grid']
        self.assessment = assess_reply(expert, self.rows, [self.plan['width_px'], self.plan['height_px']])

    def test_expert_remains_candidate_and_blind_verifier_has_no_prior_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            item = {'view': self.view, 'rows': deepcopy(self.rows), 'plan': self.plan,
                    'directory': Path(folder), 'entry': {'image_path': 'original.png'},
                    'context': {'current_view_tag': self.view['plane_name'], 'axis_names': []},
                    'status': {}, 'calls': 0}
            from app.audit import AuditLog
            original = deepcopy(item['rows'])
            controller = LengthArbitration(item, [item], self.assessment, Path(folder),
                AuditLog(Path(folder), stage=2), lambda _: None)
            self.assertEqual(item['rows'], original)
            _, _, prompt = controller.package('length_verifier')
            payload = json.loads(prompt.split('输入工作包：\n', 1)[1])
            self.assertNotIn('candidates', payload)
            for row in payload['target_members']:
                self.assertNotIn('length_mm', row)
                self.assertNotIn('endpoints', row)
            _, _, judged = controller.package('length_arbiter')
            self.assertIn('candidates', json.loads(judged.split('输入工作包：\n', 1)[1]))
            # No accidental row deletion even when no judge answer is available.
            controller.failed('length_arbiter', 'deliberate local failure-path check')
            self.assertEqual([(r['id'], r['spec'], r['view_tags'], r['member_cnt']) for r in original],
                             [(r['id'], r['spec'], r['view_tags'], r['member_cnt']) for r in item['rows']])
            self.assertTrue(all(not r['length_arbitration']['manual_required']
                                for r in item['rows'] if r.get('length_arbitration')))

    def test_subset_validation_does_not_demand_other_unknown_rows(self):
        identity = next(iter(self.assessment['updates']))
        update = self.assessment['updates'][identity]
        payload = {'references': self.assessment['payload']['references'],
                   'results': [update['resolution']], 'context_request': None}
        checked = assess_reply(payload, self.rows, [self.plan['width_px'], self.plan['height_px']], target_ids=[identity])
        self.assertEqual(checked['missing'], [])
        self.assertFalse(any(f['check'] == 'target_coverage' for f in checked['feedback']))

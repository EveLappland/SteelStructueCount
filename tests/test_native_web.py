"""Actual HTTP/storage/contracts and local reduction; no mocked model replies."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from app.main import create_app
from app.native_web import NativeRuns
from test_entity_reconciliation import local_stage_two
from test_workflow import pdf_bytes


class NativeWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'tmp/label_lengths/sample'
        self.source.parent.mkdir(parents=True)
        local_stage_two(self.source, 'a'*64)
        path = self.source/'stage_two.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['views'][0]['title'] = 'Generic LEVEL plan'
        path.write_text(json.dumps(data), encoding='utf-8')
        self.app = create_app(self.root, start_workers=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def body(self):
        sources = self.client.get('/api/native/sources').json()
        return {'source_ids':[sources[0]['id']], 'request_id':'one-request'}

    def test_web_entry_is_versioned_and_legacy_has_visible_link(self):
        response = self.client.get('/workflow')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertRegex(response.text, r'/native_workflow.js\?v=[a-f0-9]{12}')
        self.assertIn('/workflow', self.client.get('/').text)

    def test_importing_service_does_not_interrupt_another_worker(self):
        value = self.client.post('/api/native/runs/match', json=self.body()).json()
        self.app.state.native_runs.update(value['id'], state='running', stage=3)
        other = NativeRuns(self.root, False)
        self.assertEqual(other.get(value['id'])['state'], 'running')
        other.recover()
        self.assertEqual(other.get(value['id'])['state'], 'interrupted')

    def test_submission_idempotency_and_invalid_source_rejection(self):
        body = self.body()
        first = self.client.post('/api/native/runs/match', json=body)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post('/api/native/runs/match', json=body)
        self.assertEqual(first.json()['id'], second.json()['id'])
        retry_url = '/api/native/runs/'+first.json()['id']+'/rerun'
        self.assertEqual(self.client.post(retry_url,json={'request_id':'retry'}).status_code,409)
        self.assertEqual(len(self.client.get('/api/native/runs').json()), 1)
        changed = {**body, 'reasoning_effort':'max'}
        self.assertEqual(self.client.post('/api/native/runs/match', json=changed).status_code, 409)
        for ids in [['unknown'], body['source_ids']*2]:
            self.assertEqual(self.client.post('/api/native/runs/match', json={**body,'source_ids':ids}).status_code, 422)

    def test_corrupt_delivery_rejected_before_task_is_created(self):
        body = self.body()
        path = self.source/'stage_two.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['members'][0]['spec'] = 'HEM999'
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(self.client.post('/api/native/runs/match', json=body).status_code, 422)
        self.assertEqual(self.client.get('/api/native/runs').json(), [])

    def test_real_local_reduction_download_and_restart_persistence(self):
        value = self.client.post('/api/native/runs/match', json=self.body()).json()
        # A single independent observation needs no LLM; this runs production
        # orchestration/export/verification, without substituting an API reply.
        self.app.state.native_runs.worker(value['id'])
        status = self.client.get('/api/native/runs/'+value['id']).json()
        self.assertEqual(status['state'], 'completed', status)
        self.assertEqual(status['summary']['api_request_attempts'], 0)
        self.assertEqual(status['summary']['entity_quantity'], 1)
        url = '/api/native/runs/'+value['id']+'/files/'
        self.assertTrue(self.client.get(url+'stage_three/stage_three.xlsx').content.startswith(b'PK'))
        self.assertIn('只选择已有长度来源', self.client.get(url+'stage_three/index.html').text)
        self.assertEqual(self.client.get(url+'..%2F..%2Fnative_runs.sqlite3').status_code, 404)
        restored = NativeRuns(self.root, False)
        restored.recover()
        self.assertEqual(restored.get(value['id'])['state'], 'completed')
        next_run = self.client.post('/api/native/runs/'+value['id']+'/rerun', json={'request_id':'rerun'}).json()
        self.assertNotEqual(next_run['id'], value['id'])
        self.assertEqual(next_run['inputs'], value['inputs'])
        self.assertEqual(restored.get(value['id'])['state'], 'completed')

    def test_upload_validates_real_pdf_and_duplicate_files(self):
        response = self.client.post('/api/native/runs/pdf', files=[('files',('generic.pdf',pdf_bytes(),'application/pdf'))])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['mode'], 'PDF')
        for files in [[('files',('broken.pdf',b'not a pdf','application/pdf'))],
                      [('files',('a.pdf',pdf_bytes(),'application/pdf'))]*2]:
            self.assertEqual(self.client.post('/api/native/runs/pdf',files=files).status_code, 422)
        restored = NativeRuns(self.root, False)
        self.assertEqual(restored.get(response.json()['id'])['state'], 'queued')
        restored.recover()
        state = restored.get(response.json()['id'])
        self.assertEqual(state['state'], 'interrupted')


if __name__ == '__main__':
    unittest.main()

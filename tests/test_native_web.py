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

    def test_workflow_returns_to_original_page_with_versioned_adapter(self):
        response = self.client.get('/workflow?run=existing-task')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.url.path,'/')
        self.assertEqual(response.url.params['job'],'existing-task')
        self.assertRegex(response.text,r'/fixed_stage.js\?v=[a-f0-9]{12}')
        self.assertIn('id="pause-stages"',response.text)
        self.assertTrue(self.client.get('/api/native/capabilities').json()['stage_controls'])

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
        archive=self.client.get('/api/native/runs/'+value['id']+'/evidence-download')
        self.assertEqual(archive.status_code,200)
        self.assertTrue(archive.content.startswith(b'PK'))
        restored = NativeRuns(self.root, False)
        restored.recover()
        self.assertEqual(restored.get(value['id'])['state'], 'completed')
        next_run = self.client.post('/api/native/runs/'+value['id']+'/rerun', json={'request_id':'rerun'}).json()
        self.assertNotEqual(next_run['id'], value['id'])
        self.assertEqual(next_run['inputs'], value['inputs'])
        self.assertEqual(restored.get(value['id'])['state'], 'completed')

    def test_manual_stage_three_confirmation_is_versioned_and_idempotent(self):
        body=self.body()
        value=self.client.post('/api/native/runs/match',json=body).json()
        self.app.state.native_runs.update(value['id'],auto_run=False)
        self.app.state.native_runs.worker(value['id'])
        base='/api/native/runs/'+value['id']
        stage=self.client.get(base+'/stages/3').json()
        self.assertEqual(stage['status'],'awaiting_review')
        self.assertEqual(stage['data']['workflow'],'native_labels')
        self.assertIsNone(self.client.get(base).json()['download_url'])
        self.assertEqual(self.client.post(base+'/stages/3/approve',json={'revision':99}).status_code,409)
        for _ in range(2):
            response=self.client.post(base+'/stages/3/approve',json={'revision':stage['revision']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['state'],'completed')

    def test_restart_two_preserves_sources_history_and_invalidates_downloads(self):
        value=self.client.post('/api/native/runs/match',json=self.body()).json()
        service=self.app.state.native_runs
        service.worker(value['id'])
        base='/api/native/runs/'+value['id']
        old=self.client.get(base).json()['download_url']
        original=self.client.get(old).content
        stage=self.client.get(base+'/stages/2').json()
        self.assertEqual(stage['data']['views'][0]['plane_name'],'LEVEL')
        self.assertEqual(self.client.get(stage['data']['views'][0]['image_url']).status_code,200)
        response=self.client.post(base+'/stages/2/retry',json={'revision':stage['revision']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['state'],'queued')
        self.assertEqual(response.json()['active_stage'],2)
        self.assertIsNone(response.json()['download_url'])
        self.assertEqual(self.client.get(old).content,original)
        self.assertEqual(self.client.get(base+'/stages/3').json()['status'],'stale')
        self.assertTrue(service.get(value['id'])['history'])

    def test_upload_retains_pause_policy_and_omits_secrets_from_state(self):
        response=self.client.post('/api/native/runs/pdf',files=[('files',('generic.pdf',pdf_bytes(),'application/pdf'))],
            data={'auto_run':'false','stop_after_stage':'2','api_key':'local-only-test-value',
                  'max_task_concurrency':'10','max_concurrency':'5'})
        self.assertEqual(response.status_code,200,response.text)
        saved=self.app.state.native_runs.get(response.json()['id'])
        self.assertFalse(saved['auto_run'])
        self.assertEqual(saved['stop_after_stage'],2)
        self.assertEqual(saved['settings']['max_task_concurrency'],10)
        self.assertEqual(saved['settings']['max_concurrency'],5)
        self.assertNotIn('local-only-test-value',json.dumps(saved))

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

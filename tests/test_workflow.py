"""Store lifecycle and active API integration (no coordinate workflow)."""
import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf as fitz
from fastapi.testclient import TestClient
from app.main import create_app
from app.store import JobStore,StoreError

def pdf_bytes():
    document=fitz.open()
    page=document.new_page(width=600,height=400)
    page.insert_text((30,340),'PLAN AT EL.100.000(TOS)',fontsize=14)
    page.insert_text((310,340),'ELEVATION ON ALIGNMENT A',fontsize=14)
    page.insert_text((40,50),'8000 16000 6000',fontsize=12)
    page.insert_text((40,70),'HEA260',fontsize=12)
    page.draw_line((40,100),(240,100))
    page.draw_line((310,100),(510,100))
    result=document.tobytes()
    document.close()
    return result


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=JobStore(Path(self.tmp.name)/'jobs.sqlite3')
        self.store.create('job')
        self.store.claim('job',1)
        self.store.complete_stage('job',1,{'draft':1})

    def tearDown(self):self.tmp.cleanup()

    def test_stale_versions_and_repeated_approval(self):
        self.store.edit('job',1,1,{'draft':2},'correct endpoint')
        with self.assertRaises(StoreError):self.store.approve('job',1,1)
        self.assertEqual(self.store.approve('job',1,2),2)
        self.assertIsNone(self.store.approve('job',1,2))
        self.assertEqual(len(self.store.get('job')['stages']),2)

    def test_upstream_edits_invalidate_downstream_and_hide_export(self):
        self.store.approve('job',1,1)
        self.store.claim('job',2);self.store.complete_stage('job',2,{'length':1000})
        self.store.approve('job',2,1)
        self.store.claim('job',3);self.store.complete_stage('job',3,{'count':1})
        self.store.approve('job',3,1)
        self.store.claim('job',4);self.store.finish('job',{'workbook':'old.xlsx'})
        self.store.edit('job',1,1,{'draft':3},'change length evidence')
        self.assertEqual(self.store.stage('job',2)['status'],'stale')
        self.assertEqual(self.store.stage('job',3)['status'],'stale')
        self.assertNotIn('workbook',self.store.get('job'))
        self.assertTrue(any(s['data']=={'count':1} for s in self.store.snapshots('job')))

    def test_restart_marks_busy_work_interrupted(self):
        self.store.approve('job',1,1);self.store.claim('job',2)
        reopened=JobStore(self.store.path);reopened.recover()
        self.assertEqual(reopened.get('job')['state'],'interrupted')
        self.assertEqual(reopened.stage('job',1)['status'],'approved')
        self.assertEqual(reopened.retry('job',2,1),2)
        self.assertEqual(reopened.stage('job',2)['revision'],2)
        self.assertTrue(any(h['action']=='retry' for h in JobStore(self.store.path).stage('job',2)['history']))

    def test_retry_never_overwrites_manual_draft(self):
        self.store.edit('job',1,1,{'draft':'human'},'manual correction')
        self.store.retry('job',1,2);self.store.claim('job',1)
        self.store.complete_stage('job',1,{'draft':'model retry'})
        self.assertEqual(self.store.stage('job',1)['data'],{'draft':'human'})
        self.assertTrue(any(s['action']=='retry_proposal' for s in self.store.snapshots('job')))

    def test_export_failure_retries_export_only(self):
        for stage in (1,2,3):
            if stage>1:self.store.claim('job',stage);self.store.complete_stage('job',stage,{'draft':stage})
            self.store.approve('job',stage,1)
        self.store.claim('job',4);self.store.fail('job',4,'disk error')
        self.assertEqual(self.store.retry('job',3,1),4)
        self.assertEqual(self.store.stage('job',3)['status'],'approved')

    def test_completed_job_restarts_two_preserving_one_and_history(self):
        self.store.approve('job',1,1)
        self.store.claim('job',2);self.store.complete_stage('job',2,{'length':'old'})
        self.store.edit('job',2,1,{'length':'human'},'correction')
        self.store.approve('job',2,2)
        self.store.claim('job',3);self.store.complete_stage('job',3,{'count':1})
        self.store.approve('job',3,1)
        self.store.claim('job',4);self.store.finish('job',{'workbook':'old.xlsx','total':1})
        original_one=self.store.stage('job',1)
        with self.assertRaises(StoreError):self.store.retry('job',2,1)
        self.assertEqual(self.store.get('job')['state'],'completed')
        self.assertEqual(self.store.retry('job',2,2),2)
        self.assertEqual(self.store.stage('job',1),original_one)
        two=self.store.stage('job',2)
        self.assertEqual((two['revision'],two['status'],two['data']),(3,'queued',{}))
        self.assertEqual(self.store.stage('job',3)['status'],'stale')
        self.assertEqual(self.store.stage('job',3)['revision'],2)
        self.assertEqual(self.store.get('job')['active_stage'],2)
        self.assertNotIn('workbook',self.store.get('job'))
        history=self.store.snapshots('job')
        self.assertTrue(any(s['action']=='retry' and s['data']=={'length':'human'} for s in history))
        self.assertTrue(any(s['action']=='invalidated' and s['data']=={'count':1} for s in history))
        with self.assertRaises(StoreError):self.store.retry('job',2,3)
        with self.assertRaises(StoreError):self.store.approve('job',3,2)
        self.store.claim('job',2);self.store.complete_stage('job',2,{'length':'new'})
        # Explicit restart adopts new output even when old revisions had manual edits.
        self.assertEqual(self.store.stage('job',2)['data'],{'length':'new'})
        self.store.approve('job',2,3)
        self.assertEqual(self.store.stage('job',3)['data'],{})
        self.store.claim('job',3);self.store.complete_stage('job',3,{'count':2})
        self.store.approve('job',3,2)
        self.store.claim('job',4);self.store.finish('job',{'workbook':'new.xlsx','total':2})
        self.assertEqual(self.store.get('job')['total'],2)

    def test_completed_stage_two_retry_api_hides_previous_downloads(self):
        root=Path(self.tmp.name)/'api'
        app=create_app(root,start_workers=False)
        with TestClient(app) as client:
            identity=client.post('/api/jobs',files=[('files',('drawing.pdf',b'%PDF','application/pdf'))]).json()['id']
            store=app.state.store
            for stage in (1,2,3):
                store.claim(identity,stage)
                store.complete_stage(identity,stage,{'workflow':'image_first','stage':stage})
                store.approve(identity,stage,1)
            store.claim(identity,4);store.finish(identity,{'workbook':'old.xlsx','views_archive':'old.zip'})
            prefix=f'/api/jobs/{identity}'
            self.assertEqual(client.get(prefix+'/stages/2').json()['status'],'approved')
            response=client.post(prefix+'/stages/2/retry',json={'revision':1,'settings':{'reasoning_effort':'high'}})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['active_stage'],2)
            self.assertEqual(response.json()['state'],'queued')
            self.assertTrue(response.json()['auto_run'])
            self.assertIsNone(response.json()['download_url'])
            self.assertIsNone(response.json()['views_download_url'])
            self.assertEqual(client.get(prefix+'/download').status_code,404)
            self.assertEqual(client.post(prefix+'/stages/2/retry',json={'revision':1}).status_code,409)

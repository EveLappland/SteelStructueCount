import copy,json,tempfile,threading,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock,patch
from fastapi.testclient import TestClient

from app.ai_concurrency import view_concurrency
from app.audit import AuditLog
from app.drawing_input import pdf_manifest
from app.domain import StageOne,View
from app.image_workflow import freeze_images,recognize_views
from app.native_pdf import extract_page,write_json
from app.main import create_app
from test_workflow import pdf_bytes


class ViewConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        source=self.directory/'drawing.pdf';source.write_bytes(pdf_bytes())
        entries=pdf_manifest([source]);page=extract_page(entries[0])
        write_json(self.directory/'pdf_manifest.json',entries)
        write_json(self.directory/'native'/f"{page['id']}.json",page)
        views=[View(id=f'view-{i}',page_id=page['id'],kind='PLAN',bbox=[0,0,1,1],
            title=f'PLAN AT EL.{100+i}.000(TOS)',title_bbox=[0,.8,1,1],visible_axes=['X','Y'],
            fixed_axis='Z',elevation_m=100+i,elevation_basis='TOS',region_confirmed=True).model_dump()
            for i in range(3)]
        self.one=StageOne(workflow='image_first',views=views,profile_catalog=['HEA260'],analysis_pending=True).model_dump()
        freeze_images(self.directory,self.one)
        self.audit=AuditLog(self.directory,2,secrets=('concurrency-test-secret',))

    def tearDown(self):self.tmp.cleanup()

    def run_views(self,model,limit='3',*,reuse_saved=False,writer=None,effort='high'):
        with patch('app.image_workflow._api_settings',return_value=('https://example.test','concurrency-test-secret','deepseek-flash')),patch('app.pipeline.OpenAI') as clients,patch('app.pipeline.model_call',side_effect=model):
            if writer:
                with patch('app.image_workflow.write_json',side_effect=writer):
                    result=recognize_views(self.directory,self.one,{'max_concurrency':limit,'reasoning_effort':effort},lambda _:None,self.audit,reuse_saved=reuse_saved)
            else:result=recognize_views(self.directory,self.one,{'max_concurrency':limit,'reasoning_effort':effort},lambda _:None,self.audit,reuse_saved=reuse_saved)
            self.assertEqual(clients.return_value.close.call_count,3)
            return result

    @staticmethod
    def payload(entry):
        index=int(entry['id'].split('-')[-1])
        return {'members':[{'spec':'HEA260','view_tags':[f'EL.{100+index}.000(TOS)'],
            'member_cnt':1,'length_mm':6000,'location':f'position {index}','issue':None}]}

    def test_three_requests_overlap_and_reverse_completion_keeps_ordered_atomic_checkpoints(self):
        original=copy.deepcopy(self.one);started=Queue();checkpoints=Queue()
        release={f'view-{i}':threading.Event() for i in range(3)}
        def model(client,model,prompt,entry,*args,**kwargs):
            started.put(entry['id'])
            if not release[entry['id']].wait(10):raise RuntimeError('test release timeout')
            return self.payload(entry)
        def writer(path,data):
            write_json(path,data)
            if path.name=='image_recognition_checkpoint.tmp':checkpoints.put(copy.deepcopy(data))
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_views,model,writer=writer)
            try:
                self.assertEqual({started.get(timeout=10) for _ in range(3)},set(release))
                for identity in reversed(list(release)):
                    release[identity].set()
                    while True:
                        snapshot=checkpoints.get(timeout=10)
                        state=next(a for a in snapshot['view_analysis'] if a['view_id']==identity)
                        if state['status']=='completed':break
                    ids=[r['source_view'] for r in snapshot['view_members']]
                    self.assertEqual(ids,sorted(ids))
                    self.assertEqual(len(ids),len(set(ids)))
                result=future.result(timeout=10)
            finally:
                for event in release.values():event.set()
        self.assertEqual([r['source_view'] for r in result['view_members']],list(release))
        self.assertEqual(self.one,original)
        saved=json.loads((self.directory/'image_recognition_checkpoint.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['view_members'],result['view_members'])
        self.assertFalse((self.directory/'image_recognition_checkpoint.tmp').exists())
        events=self.audit.read(limit=1000)['items']
        self.assertEqual(max(e['active_views'] for e in events if e['event']=='VIEW_RECOGNITION_STARTED'),3)

    def test_one_failure_does_not_cancel_other_views_or_expose_the_key(self):
        def model(client,model,prompt,entry,*args,**kwargs):
            if entry['id']=='view-1':raise RuntimeError('failure concurrency-test-secret')
            return self.payload(entry)
        result=self.run_views(model)
        self.assertEqual([r['source_view'] for r in result['view_members']],['view-0','view-2'])
        self.assertEqual([r['status'] for r in result['view_analysis']],['completed','failed','completed'])
        self.assertNotIn('concurrency-test-secret',json.dumps(result))
        self.assertNotIn('concurrency-test-secret',(self.directory/'logs/events.jsonl').read_text(encoding='utf-8'))

    def test_single_slot_has_the_same_model_inputs_and_result_as_multiple_slots(self):
        calls={};modes={}
        def model(client,model,prompt,entry,*args,**kwargs):
            calls[entry['id']]=(prompt,copy.deepcopy(entry))
            modes[entry['id']]=(kwargs['reasoning_effort'],kwargs['reuse_saved'])
            return self.payload(entry)
        single=self.run_views(model,'1',reuse_saved=True,effort='max')
        first=copy.deepcopy(calls);calls.clear()
        multiple=self.run_views(model,'3',reuse_saved=True,effort='max')
        self.assertEqual(first,calls)
        self.assertEqual(single,multiple)
        self.assertTrue(all(mode==('max',True) for mode in modes.values()))
        batches=[e for e in self.audit.read(limit=1000)['items'] if e['event']=='VIEW_BATCH_STARTED']
        self.assertEqual([e['worker_count'] for e in batches],[1,3])

    def test_large_config_is_limited_by_available_views(self):
        result=self.run_views(lambda client,model,prompt,entry,*a,**kw:self.payload(entry),'300')
        batches=[e for e in self.audit.read(limit=1000)['items'] if e['event']=='VIEW_BATCH_STARTED']
        self.assertEqual(batches[-1]['configured_concurrency'],300)
        self.assertEqual(batches[-1]['worker_count'],3)
        self.assertEqual(len(result['view_members']),3)

    def test_local_coverage_difference_does_not_trigger_another_model_call(self):
        calls=[]
        def model(client,model,prompt,entry,*args,**kwargs):
            calls.append(entry['id'])
            return self.payload(entry)
        result=self.run_views(model)
        self.assertEqual(sorted(calls),['view-0','view-1','view-2'])
        self.assertTrue(all(a['rounds_attempted']==1 and a['selected_round']==1
                            for a in result['view_analysis']))

    def test_duplicate_view_ids_fail_before_any_api_call(self):
        self.one['views'][1]['id']=self.one['views'][0]['id']
        with patch('app.image_workflow._api_settings',return_value=('https://example.test','k','m')),patch('app.pipeline.model_call') as model:
            with self.assertRaisesRegex(ValueError,'视图ID重复'):
                recognize_views(self.directory,self.one,{},lambda _:None,self.audit)
            model.assert_not_called()

    def test_limit_configuration_and_validation(self):
        with patch.dict('os.environ',{'LLM_MAX_CONCURRENCY':'2'}):
            self.assertEqual(view_concurrency(),2)
            self.assertEqual(view_concurrency({'max_concurrency':'3'}),3)
            self.assertEqual(view_concurrency({'max_concurrency':''}),2)
        with patch.dict('os.environ',{},clear=True):self.assertEqual(view_concurrency(),3)
        for value in ['0','3.0','invalid',True,-1]:
            with self.assertRaises(ValueError):view_concurrency({'max_concurrency':value})


class ConcurrencyFormTests(unittest.TestCase):
    def test_blank_upload_setting_uses_default_and_invalid_limits_create_no_job(self):
        with tempfile.TemporaryDirectory() as temp,TestClient(create_app(Path(temp),start_workers=False)) as client:
            for value in ('','1','8','20','300'):
                response=client.post('/api/jobs',files=[('files',('drawing.pdf',pdf_bytes(),'application/pdf'))],
                                     data={'max_concurrency':value})
                self.assertEqual(response.status_code,200,response.text)
            before=len(client.get('/api/jobs').json())
            for value in ('0','-1','3.5'):
                response=client.post('/api/jobs',files=[('files',('drawing.pdf',pdf_bytes(),'application/pdf'))],
                                     data={'max_concurrency':value})
                self.assertEqual(response.status_code,422,response.text)
            self.assertEqual(len(client.get('/api/jobs').json()),before)

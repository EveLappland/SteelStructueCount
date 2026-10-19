import copy,json,tempfile,unittest,threading
from pathlib import Path
from unittest.mock import patch,Mock
import pymupdf as fitz
from fastapi.testclient import TestClient
from app.audit import AuditLog
from app.stage_one import PageAssessment,ConstructionCatalog,ConstructionView,prepare,refresh_catalog
from app.main import create_app
from app.pipeline import validate_draft
from app.member_recognition import view_tag


def document(pages=2):
    pdf=fitz.open()
    for i in range(pages):
        page=pdf.new_page(width=600,height=400)
        page.insert_text((30,340),'CONNECTION DETAILS' if i==0 else 'STRUCTURE PLAN Q',fontsize=14)
        page.insert_text((40,70),'HEA999' if i==0 else 'HEA260 /EPB 2LF-L100*10',fontsize=12)
        page.draw_rect(fitz.Rect(40,100,500,300))
    result=pdf.tobytes();pdf.close();return result


def catalog():
    return {'views':[{'title':'STRUCTURE PLAN Q','name':'DECK-Q','kind':'PLAN','bbox':[0,0,.95,1],
        'plane_names':['DECK-Q','GRID-M','HEIGHT+12'],'steel_types':['HEA260 /EPB','2LF-L100*10','HEA260']}]}


class StageOneTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'uploads').mkdir();(self.root/'uploads/arbitrary.pdf').write_bytes(document())
        self.audit=AuditLog(self.root,1)
    def tearDown(self):self.tmp.cleanup()

    def run_prepare(self,call,concurrency=1,**options):
        with patch('app.pipeline.OpenAI'),patch('app.settings._api_settings',return_value=('https://example.test','k','m')),patch('app.pipeline.model_call',side_effect=call):
            return prepare(self.root,{'max_concurrency':str(concurrency)},lambda _:None,self.audit,**options)

    def test_parallel_requests_out_of_order_with_serial_rendering_and_stable_checkpoint(self):
        from app.drawing_input import render_data_uri
        main_thread=threading.get_ident();both_purposes=threading.Barrier(2)
        second_catalog_done=threading.Event();clients=[];calls=[]
        def render(*args,**kwargs):
            self.assertEqual(threading.get_ident(),main_thread)
            return render_data_uri(*args,**kwargs)
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            self.assertNotEqual(threading.get_ident(),main_thread)
            self.assertTrue(entry['prepared_data_uri'].startswith('data:image/jpeg;base64,'))
            clients.append(client);calls.append((entry['page'],contract))
            self.assertTrue(kwargs['reuse_saved']);self.assertEqual(kwargs['reasoning_effort'],'high')
            if contract is PageAssessment:
                both_purposes.wait(timeout=5)
                return {'decision':'CONSTRUCTION','reason':'建造图'}
            if entry['page']==1:self.assertTrue(second_catalog_done.wait(timeout=5))
            else:second_catalog_done.set()
            return catalog()
        with patch('app.drawing_input.render_data_uri',side_effect=render),patch('app.pipeline.OpenAI',side_effect=lambda **kw:Mock()):
            # run_prepare normally patches the constructor; call prepare directly here.
            with patch('app.settings._api_settings',return_value=('https://example.test','k','m')),patch('app.pipeline.model_call',side_effect=ai):
                result=prepare(self.root,{'max_concurrency':'2'},lambda _:None,self.audit,reuse_saved=True)
        self.assertEqual(len({id(c) for c in clients}),4)
        for client in clients:client.close.assert_called_once()
        for page in (1,2):
            self.assertLess(calls.index((page,PageAssessment)),calls.index((page,ConstructionCatalog)))
        self.assertEqual([v['id'] for v in result['views']],['f000-p001-view01','f000-p002-view01'])
        checkpoint=json.loads((self.root/'page_catalog_checkpoint.json').read_text(encoding='utf-8'))
        self.assertEqual([p['page'] for p in checkpoint['page_decisions']],[1,2])
        self.assertEqual([v['id'] for v in checkpoint['views']],[v['id'] for v in result['views']])
        self.assertEqual(len(checkpoint['views']),2)
        self.assertFalse((self.root/'page_catalog_checkpoint.tmp').exists())

    def test_parallel_failure_does_not_cancel_other_page_and_reuses_request_slot(self):
        barrier=threading.Barrier(2)
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            if contract is PageAssessment:
                barrier.wait(timeout=5)
                if entry['page']==1:raise TimeoutError('请求超时/限流重试已耗尽')
                return {'decision':'CONSTRUCTION','reason':'建造图'}
            return catalog()
        result=self.run_prepare(ai,concurrency=2)
        self.assertEqual(len(result['views']),1)
        self.assertEqual(result['views'][0]['page_id'],'f000-p002')
        self.assertEqual(result['catalog_summary']['pending_pages'],1)
        self.assertIn('超时',result['page_decisions'][0]['issue'])

    def test_preview_failure_stays_page_pending(self):
        from app.drawing_input import render_data_uri
        def render(entry,**kwargs):
            if entry['page']==1:raise ValueError('预览渲染失败')
            return render_data_uri(entry,**kwargs)
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            self.assertEqual(entry['page'],2)
            if contract is PageAssessment:return {'decision':'CONSTRUCTION','reason':'建造图'}
            return catalog()
        with patch('app.drawing_input.render_data_uri',side_effect=render):
            result=self.run_prepare(ai,concurrency=2)
        self.assertEqual(len(result['views']),1)
        self.assertIn('预览渲染失败',result['page_decisions'][0]['issue'])

    def test_more_pages_than_slots_never_exceed_limit_and_all_skips_are_recorded(self):
        (self.root/'uploads/arbitrary.pdf').write_bytes(document(pages=5))
        lock=threading.Lock();barrier=threading.Barrier(2);active=0;peak=0
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            nonlocal active,peak
            self.assertIs(contract,PageAssessment)
            with lock:active+=1;peak=max(peak,active)
            try:
                if entry['page']<=2:barrier.wait(timeout=5)
                return {'decision':'SKIP','reason':'节点详图'}
            finally:
                with lock:active-=1
        result=self.run_prepare(ai,concurrency=2)
        self.assertEqual(peak,2)
        self.assertEqual([r['page'] for r in result['page_decisions']],list(range(1,6)))
        self.assertEqual(result['catalog_summary']['skipped_pages'],5)
        self.assertFalse((self.root/'views').exists())

    def test_assess_before_localize_and_render_only_adopted_construction_views(self):
        calls=[]
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            calls.append((entry['page'],contract,entry['image_max_dimension']))
            if contract is PageAssessment:return {'decision':'SKIP' if entry['page']==1 else 'CONSTRUCTION','reason':'节点详图' if entry['page']==1 else '有效建造平面'}
            return catalog()
        result=self.run_prepare(ai)
        self.assertEqual(calls,[(1,PageAssessment,1400),(2,PageAssessment,1400),(2,ConstructionCatalog,3600)])
        self.assertEqual(result['profile_catalog'],['HEA260','L100*10'])
        self.assertEqual(result['catalog_summary']['plan_names'],['DECK-Q'])
        self.assertEqual(result['catalog_summary']['plane_name_count'],3)
        self.assertEqual(view_tag(result['views'][0]),'DECK-Q')
        self.assertNotIn('visible_axes',result['views'][0])
        self.assertFalse((self.root/'views/f000-p001.jpg').exists())
        self.assertFalse(list((self.root/'views').glob('*f000-p001*')))
        self.assertTrue((self.root/'views'/result['views'][0]['image_file']).is_file())
        pix=fitz.Pixmap(str(self.root/'views'/result['views'][0]['image_file']))
        self.assertEqual((pix.xres,pix.yres),(300,300))
        self.assertEqual(result['page_decisions'][0]['reason'],'节点详图')
        # Editable lists stay AI/manual-derived; native HEA999 never pollutes them.
        result['views'][0]['steel_types']=['SHS200*200*8'];result['views'][0]['axis_labels']=['GRID-Z']
        result=validate_draft(self.root,1,result)
        self.assertEqual(result['profile_catalog'],['SHS200*200*8'])
        self.assertEqual(result['catalog_summary']['plane_name_count'],2)

    def test_all_skipped_or_uncertain_pages_do_not_create_images_or_localization_calls(self):
        calls=[]
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            calls.append(contract)
            return {'decision':'SKIP' if entry['page']==1 else 'REVIEW','reason':'没有完整构件布置或无法确定'}
        result=self.run_prepare(ai)
        self.assertEqual(calls,[PageAssessment,PageAssessment])
        self.assertFalse(result['views'])
        self.assertFalse((self.root/'views').exists())
        self.assertEqual(result['catalog_summary']['pending_pages'],1)
        self.assertTrue(any('页面待确认' in w for w in result['warnings']))

    def test_bad_catalog_is_page_pending_without_partial_results_or_whole_job_failure(self):
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            if contract is PageAssessment:return {'decision':'CONSTRUCTION','reason':'有效布置图'}
            if entry['page']==1:raise ValueError('模型返回格式无效')
            return catalog()
        result=self.run_prepare(ai)
        self.assertEqual(len(result['views']),1)
        self.assertEqual(result['catalog_summary']['pending_pages'],1)
        self.assertIn('模型返回格式无效',result['page_decisions'][0]['issue'])

    def test_delivery_contract_has_no_global_coordinates_or_member_fields(self):
        self.assertEqual(set(ConstructionView.model_fields),{'title','name','kind','bbox','plane_names','steel_types'})
        with self.assertRaises(ValueError):ConstructionView.model_validate({**catalog()['views'][0],'visible_axes':['X','Y']})
        with self.assertRaises(ValueError):ConstructionView.model_validate({**catalog()['views'][0],'bbox':[.9,0,.1,1]})

    def test_repeated_named_plane_on_multiple_pages_has_two_images_but_one_plane_name(self):
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            if contract is PageAssessment:return {'decision':'CONSTRUCTION','reason':'建造图'}
            return catalog()
        result=self.run_prepare(ai)
        self.assertEqual(result['catalog_summary']['view_count'],2)
        self.assertEqual(result['catalog_summary']['plan_names'],['DECK-Q'])

    def test_duplicate_crop_is_pending_without_partially_adopted_views(self):
        def ai(client,model,prompt,entry,path,contract,**kwargs):
            if contract is PageAssessment:return {'decision':'CONSTRUCTION','reason':'建造图'}
            return {'views':catalog()['views']*2}
        result=self.run_prepare(ai)
        self.assertFalse(result['views'])
        self.assertEqual(result['catalog_summary']['pending_pages'],2)
        self.assertFalse((self.root/'views').exists())

    def test_api_keeps_ai_catalog_and_lists_skipped_pages_without_rendering(self):
        with tempfile.TemporaryDirectory() as temp,TestClient(create_app(Path(temp),start_workers=False)) as client:
            identity=client.post('/api/jobs',files=[('files',('drawing.pdf',document(),'application/pdf'))]).json()['id']
            store=client.app.state.store;store.claim(identity,1)
            folder=client.app.state.jobs_directory/identity
            def ai(client,model,prompt,entry,path,contract,**kwargs):
                if contract is PageAssessment:return {'decision':'SKIP' if entry['page']==1 else 'CONSTRUCTION','reason':'用途说明'}
                return catalog()
            with patch('app.pipeline.OpenAI'),patch('app.settings._api_settings',return_value=('https://example.test','k','m')),patch('app.pipeline.model_call',side_effect=ai):
                data=prepare(folder,{},lambda _:None,AuditLog(folder,1))
            store.complete_stage(identity,1,data)
            result=client.get(f'/api/jobs/{identity}/stages/1').json()['data']
            self.assertEqual(result['profile_catalog'],['HEA260','L100*10'])
            self.assertEqual(result['page_decisions'][0]['decision'],'SKIP')
            self.assertFalse((folder/'views/f000-p001.jpg').exists())
            # An explicit preview request works, without eager screenshot generation.
            self.assertEqual(client.get(f'/api/jobs/{identity}/views/f000-p001.jpg').status_code,200)
            for page in data['page_decisions']:page.update(decision='SKIP',view_ids=[])
            data['views']=[]
            pages=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((folder/'native').glob('*.json'))]
            data=refresh_catalog(data,pages)
            store.edit(identity,1,1,data,'全部页面不采用')
            response=client.post(f'/api/jobs/{identity}/stages/1/approve',json={'revision':2,'acknowledge_pending':True})
            self.assertEqual(response.status_code,422)

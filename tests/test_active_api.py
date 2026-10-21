"""Real PDF/store/API transitions with no model request or inferred test answer."""
import copy
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from app.domain import StageOne,StageTwo,View
from app.image_workflow import freeze_images
from app.main import create_app
from app.member_recognition import validate_ledger
from app.pipeline import extract
from app.stage_one import refresh_catalog
from test_workflow import pdf_bytes


class ActiveApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.app=create_app(self.root,start_workers=False)
        self.client=TestClient(self.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.job=self.client.post('/api/jobs',files=[('files',('drawing.pdf',pdf_bytes(),'application/pdf'))],data={'auto_run':'false'}).json()['id']
        self.url=f'/api/jobs/{self.job}';self.store=self.app.state.store
        self.folder=self.root/'data/jobs'/self.job
        _,pages=extract(self.folder,lambda _:None,render_previews=False)
        view=View(id='v',page_id=pages[0]['id'],kind='PLAN',title='PLAN AT EL.100.000(TOS)',
                  plane_name='EL.100.000(TOS)',bbox=[0,0,1,1],title_bbox=[0,0,1,1],steel_types=['HEA260']).model_dump()
        one=StageOne(views=[view],page_decisions=[dict(page_id=pages[0]['id'],file='drawing.pdf',page=1,
                     decision='CONSTRUCTION',reason='Local API fixture',view_ids=['v'])]).model_dump()
        self.one=refresh_catalog(one,pages);freeze_images(self.folder,self.one)
        self.store.claim(self.job,1);self.store.complete_stage(self.job,1,self.one)

    def prepare_two(self):
        self.client.post(self.url+'/stages/1/approve',json={'revision':1,'acknowledge_pending':True})
        two=StageTwo(**self.one).model_dump()
        two['view_members']=[dict(id='m',source_view='v',spec='HEA260',member_cnt=1,view_tags=[],
                                 length_mm=6000,location='API fixture',issue=None)]
        two=validate_ledger(two)
        self.store.claim(self.job,2);self.store.complete_stage(self.job,2,two)
        return two

    def test_frozen_image_route_and_stale_edit_rejection(self):
        image=self.one['views'][0]['image_file']
        self.assertTrue(self.client.get(self.url+'/views/'+image).content.startswith(b'\x89PNG'))
        self.assertEqual(self.client.get(self.url+'/views/unknown.png').status_code,404)
        body={'revision':1,'data':self.one,'reason':'Update catalog'}
        self.assertEqual(self.client.patch(self.url+'/stages/1',json=body).status_code,200)
        self.assertEqual(self.client.patch(self.url+'/stages/1',json=body).status_code,409)

    def test_home_versions_scripts_and_disables_html_cache(self):
        response=self.client.get('/')
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertRegex(response.text,r'/app\.js\?v=[a-f0-9]{12}')
        self.assertNotIn('id="discovery-inventory"',response.text)
        self.assertIn('id="stage12-only"',response.text)

    def test_two_stage_policy_is_persisted_and_invalid_cap_creates_no_job(self):
        result=self.client.post('/api/jobs',files=[('files',('drawing.pdf',pdf_bytes(),'application/pdf'))],
                                data={'auto_run':'true','stop_after_stage':'2'})
        self.assertEqual(result.status_code,200)
        job=self.client.get('/api/jobs/'+result.json()['id']).json()
        self.assertTrue(job['auto_run']);self.assertEqual(job['stop_after_stage'],2)
        before=len(self.client.get('/api/jobs').json())
        invalid=self.client.post('/api/jobs',files=[('files',('drawing.pdf',pdf_bytes(),'application/pdf'))],
                                 data={'stop_after_stage':'5'})
        self.assertEqual(invalid.status_code,422)
        self.assertEqual(len(self.client.get('/api/jobs').json()),before)

    def test_stage_two_grid_images_are_delivered_only_when_referenced(self):
        two=self.prepare_two()
        view=self.one['views'][0]
        from app.grid_layout import freeze_grid
        plan=freeze_grid(self.folder,view['id'],self.folder/'views'/view['image_file'])
        two['recognition_strategy']='grid_agents'
        two['view_analysis']=[{'view_id':'v','status':'completed','grid_tiles':plan['tiles']}]
        self.store.edit(self.job,2,1,two,'Grid geometry reference')
        image=plan['tiles'][0]['image_file']
        self.assertEqual(self.client.get(self.url+'/views/'+image).status_code,200)
        self.assertEqual(self.client.get(self.url+'/views/grid-unreferenced.png').status_code,404)

    def test_second_stage_cannot_mutate_upstream_views(self):
        two=self.prepare_two();changed=copy.deepcopy(two)
        changed['views'][0]['bbox']=[0,0,.8,1]
        result=self.client.patch(self.url+'/stages/2',json={'revision':1,'data':changed,'reason':'Forbidden crop edit'})
        self.assertEqual(result.status_code,422)
        self.assertEqual(self.store.stage(self.job,2)['data'],two)

    def test_upstream_edit_invalidates_downstream_and_keeps_old_asset(self):
        self.prepare_two();old=self.one['views'][0]['image_file']
        changed=copy.deepcopy(self.one);changed['views'][0]['bbox']=[0,0,.9,1]
        result=self.client.patch(self.url+'/stages/1',json={'revision':1,'data':changed,'reason':'Crop correction'})
        self.assertEqual(result.status_code,200)
        self.assertNotEqual(result.json()['data']['views'][0]['image_file'],old)
        self.assertEqual(self.store.stage(self.job,2)['status'],'stale')
        self.assertEqual(self.client.get(self.url+'/views/'+old).status_code,200)

    def test_recrop_invalidates_the_image_bound_quantity_reference(self):
        view=self.one['views'][0]
        view['quantity_reference']={'total_count':1,'spec_counts':[{'spec':'HEA260','member_cnt':1}],'issue':None}
        view['quantity_reference_image']=view['image_file']
        view['bbox']=[0,0,.9,1]
        freeze_images(self.folder,self.one)
        self.assertIsNone(view['quantity_reference'])
        self.assertEqual(view['quantity_reference_image'],'')
        refresh_catalog(self.one,extract(self.folder,lambda _:None,render_previews=False)[1])
        self.assertEqual(self.one['catalog_summary']['uncounted_views'],1)

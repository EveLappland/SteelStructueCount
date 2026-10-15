import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app.view_filter import apply_filter,role
from app.domain import StageOne,View
from app.image_workflow import freeze_images,recognize_views
from app.drawing_input import pdf_manifest
from app.native_pdf import extract_page,write_json
from app.audit import AuditLog
from app.group_dedup import build_group_review
from app.member_recognition import validate_ledger
from app.pipeline import model_schema,ViewResponse,validate_draft,discover,parse_model_content
import json
from test_workflow import pdf_bytes


class ViewFilterTests(unittest.TestCase):
    def fixture(self):
        page={'id':'p','texts':[{'id':'t1','text':'HEA260','bbox':[.1,.1,.2,.2]},
                             {'id':'t2','text':'HEA500','bbox':[.7,.1,.8,.2]}]}
        views=[dict(id='plan',page_id='p',kind='PLAN',title='PLAN AT EL.-9.100(BOB)',bbox=[0,0,.5,1]),
               dict(id='foundation',page_id='p',kind='PLAN',title='BASE PLAN Q',bbox=[.5,0,1,1],
                    takeoff_role='REFERENCE',filter_reason='仅显示柱截面投影与柱脚定位，不新增完整钢材段。')]
        return {'views':views,'warnings':[],'profile_catalog':['HEA260','HEA500']},[page]

    def test_mixed_sheet_filters_by_view_preserves_planes_and_unknown_bob(self):
        data,pages=self.fixture();result=apply_filter(data,pages)
        self.assertEqual([role(v) for v in result['views']],['MEMBERS','REFERENCE'])
        self.assertEqual(len(result['views']),2)
        self.assertEqual(result['profile_catalog'],['HEA260'])
        self.assertEqual(result['view_filter_summary']['counts']['REFERENCE'],1)

    def test_missing_filter_reason_stays_pending_and_manual_override_survives(self):
        data,pages=self.fixture();data['views'][0].update(takeoff_role='EXCLUDE',filter_reason='')
        result=apply_filter(data,pages)
        self.assertEqual(role(result['views'][0]),'REVIEW')
        self.assertTrue(any(w.startswith('用途待确认：') for w in result['warnings']))
        result['views'][0].update(takeoff_role='MEMBERS',filter_reviewed=True,filter_reason='人工核对为有效布置图')
        result=apply_filter(result,pages)
        self.assertEqual(role(result['views'][0]),'MEMBERS')
        self.assertFalse(result['warnings'])

    def test_explicit_detail_title_is_excluded_but_can_be_included_manually(self):
        data,pages=self.fixture();data['views'][0]['title']='BASE PLATE DETAILS FOR QZ'
        self.assertEqual(role(apply_filter(data,pages)['views'][0]),'EXCLUDE')
        data['views'][0].update(takeoff_role='MEMBERS',filter_reviewed=True,filter_reason='人工确认包含完整型钢构件')
        self.assertEqual(role(apply_filter(data,pages)['views'][0]),'MEMBERS')

    def test_isometric_is_reference_even_for_legacy_inputs(self):
        self.assertEqual(role({'kind':'ISOMETRIC'}),'REFERENCE')

    def test_model_must_classify_without_claiming_manual_review(self):
        schema=model_schema(ViewResponse)['$defs']['View']
        self.assertIn('takeoff_role',schema['required'])
        self.assertIn('filter_reason',schema['required'])
        self.assertNotIn('filter_reviewed',schema['properties'])

    def test_excluded_local_section_accepts_partial_axes_but_members_and_boxes_stay_strict(self):
        raw=dict(id='local',page_id='p',title='SECTION Q',kind='ELEVATION',bbox=[.6,.1,.95,.6],
            title_bbox=[.65,.5,.85,.55],visible_axes=['Z'],fixed_axis=None,
            takeoff_role='EXCLUDE',filter_reason='仅柱脚连接剖面，不是完整型钢构件。')
        result=parse_model_content(json.dumps({'views':[raw]}),ViewResponse,{'id':'p'})
        self.assertEqual(result['views'][0]['visible_axes'],['Z'])
        with self.assertRaises(ValueError):View.model_validate({**raw,'takeoff_role':'MEMBERS'})
        self.assertEqual(View.model_validate({**raw,'takeoff_role':'REVIEW'}).visible_axes,['Z'])
        with self.assertRaises(ValueError):View.model_validate({**raw,'bbox':[.9,.1,.6,.6]})

    def test_extra_details_survive_native_catalog_reconciliation_including_detail_only_page(self):
        for has_native in (True,False):
            with self.subTest(has_native=has_native),tempfile.TemporaryDirectory() as temp:
                directory=Path(temp)
                title={'title':'PLAN AT EL.5.000(TOS)','kind':'PLAN','title_bbox':[.1,.55,.4,.6],
                    'elevation_m':5,'elevation_basis':'TOS','alignment':None,'evidence_id':'t1'}
                page=dict(id='p',file='arbitrary.pdf',page=1,width_pt=1000,height_pt=1000,vectors=[],
                    titles=[title] if has_native else [],texts=[dict(id='t1',page_id='p',kind='TEXT',text=title['title'],bbox=title['title_bbox']),
                    dict(id='t2',page_id='p',kind='TEXT',text='SECTION Q',bbox=[.65,.5,.85,.55])])
                main=View(id='native',page_id='p',title=title['title'],kind='PLAN',bbox=[.1,.1,.4,.6],
                    title_bbox=title['title_bbox'],structure_bbox=[.15,.15,.35,.5],region_confirmed=False,
                    evidence_ids=['t1']).model_dump()
                detail=View(id='extra',page_id='p',title='SECTION Q',kind='ELEVATION',bbox=[.6,.1,.95,.6],
                    structure_bbox=[.65,.15,.9,.48],title_bbox=[.65,.5,.85,.55],visible_axes=['Z'],
                    evidence_ids=['t2'],takeoff_role='EXCLUDE',filter_reason='柱脚连接剖面，非完整构件').model_dump()
                write_json(directory/'pdf_manifest.json',[dict(id='p',file='arbitrary.pdf',page=1,source_path='unused.pdf')])
                seed=StageOne(views=[main] if has_native else [],analysis_pending=True).model_dump()
                with patch('app.pipeline.load_native',return_value=[page]),patch('app.pipeline._api_settings',return_value=('https://example.test','k','m')),patch('app.pipeline.OpenAI'),patch('app.pipeline.model_call',return_value={'views':([main] if has_native else [])+[detail]}):
                    result=discover(directory,{},lambda _:None,audit=AuditLog(directory,1),seed=seed,regions_only=True)
                excluded=[v for v in result['views'] if v['takeoff_role']=='EXCLUDE']
                self.assertEqual(len(excluded),1)
                self.assertEqual(excluded[0]['visible_axes'],['Z'])
                self.assertEqual(result['view_filter_summary']['counts']['EXCLUDE'],1)

    def test_filtered_views_make_no_calls_and_do_not_become_missing_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);pdf=directory/'drawing.pdf';pdf.write_bytes(pdf_bytes())
            entries=pdf_manifest([pdf]);page=extract_page(entries[0])
            write_json(directory/'pdf_manifest.json',entries);write_json(directory/'native'/f"{page['id']}.json",page)
            views=[View(id=f'v{i}',page_id=page['id'],title=f'PLAN AT EL.{100+i}.000(TOS)',kind='PLAN',
                bbox=[0,0,1,1],title_bbox=[0,.8,1,1],takeoff_role=purpose,
                filter_reason='测试用可追溯分类理由',region_confirmed=i==0).model_dump()
                for i,purpose in enumerate(('MEMBERS','REFERENCE','EXCLUDE','REVIEW'))]
            one=StageOne(workflow='image_first',views=views,profile_catalog=['HEA260']).model_dump()
            freeze_images(directory,one)
            payload={'members':[dict(spec='HEA260',view_tags=['EL.100.000(TOS)'],member_cnt=1,
                                     length_mm=6000,location='梁',issue=None)]}
            with patch('app.image_workflow._api_settings',return_value=('https://example.test','key','model')),patch('app.pipeline.OpenAI'),patch('app.pipeline.model_call',return_value=payload) as model:
                result=recognize_views(directory,one,{},lambda _:None,AuditLog(directory,2))
            self.assertEqual(model.call_count,1)
            self.assertEqual([a['status'] for a in result['view_analysis']],['completed','filtered','filtered','filter_pending'])
            self.assertEqual([p['view_id'] for p in build_group_review(result)['pending_sources']],['v3'])
            bad=copy.deepcopy(result);bad['view_members'][0]['source_view']='v1'
            with self.assertRaisesRegex(ValueError,'纳入统计'):validate_ledger(bad)
            old_one=copy.deepcopy(one);old_one['views']=old_one['views'][:1]
            for key in ('takeoff_role','filter_reason','filter_reviewed'):old_one['views'][0].pop(key,None)
            old_two={**copy.deepcopy(result),'views':copy.deepcopy(old_one['views']),'view_analysis':[result['view_analysis'][0]]}
            validated=validate_draft(directory,2,old_two,stage_one=old_one)
            self.assertEqual(len(validated['view_members']),1)

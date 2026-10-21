"""Geometry, contracts and saved real replies; no model-generated test answers."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from app.ai_concurrency import view_concurrency
from app.grid_layout import plan_grid,freeze_grid
from app.grid_contracts import GridChildResponse,GridMasterResponse
from app.grid_validation import assess_master
from app.grid_workflow import locate_children,frame_context,ledger_rows
from app.member_recognition import validate_ledger
from app.quantity_reference import QuantityReference,assess_quantity
from app.drawing_input import render_data_uri

ROOT=Path(__file__).resolve().parents[1]


class GridContractTests(unittest.TestCase):
    def test_count_units_are_member_cnt_not_json_rows_and_keep_combinations(self):
        ref={'total_count':3,'spec_counts':[{'spec':'HEA260','member_cnt':2},{'spec':'2LF-L100*10','member_cnt':1}],'issue':None}
        quantity=assess_quantity([{'spec':'HEA260','member_cnt':2},{'spec':'2F-L100*10','member_cnt':1}],ref)
        self.assertTrue(quantity['matches'])
        self.assertEqual(quantity['actual_total'],3)
        self.assertFalse(assess_quantity([{'spec':'HEA260','member_cnt':1}],ref)['matches'])
        self.assertIsNone(assess_quantity([],None)['matches'])
        with self.assertRaises(ValueError):QuantityReference.model_validate({**ref,'total_count':4})
        with self.assertRaises(ValueError):QuantityReference.model_validate({'total_count':None,'spec_counts':[],'issue':None})

    def test_master_cannot_rewrite_source_view_and_child_has_no_group_count(self):
        self.assertEqual(set(GridMasterResponse.model_fields),{'members','decisions'})
        self.assertNotIn('member_cnt',GridChildResponse.model_json_schema()['$defs']['GridCandidate']['properties'])

    def test_grid_one_and_two_dimensions_and_preserve_every_pixel(self):
        self.assertEqual((plan_grid(2008,5669)['columns'],plan_grid(2008,5669)['rows']),(1,4))
        self.assertEqual((plan_grid(6110,2982)['columns'],plan_grid(6110,2982)['rows']),(4,2))
        self.assertEqual(len(plan_grid(2107,2000)['tiles']),1)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'views').mkdir()
            image=Image.new('RGB',(2201,45),'white');image.putpixel((1200,20),(1,9,35))
            source=root/'views/source.png';image.save(source,dpi=(300,300))
            plan=freeze_grid(root,'view-q',source)
            for tile in plan['tiles']:
                path=root/'views'/tile['image_file']
                with Image.open(path) as crop:self.assertEqual(crop.tobytes(),image.crop(tile['crop_bbox_px']).tobytes())
                self.assertEqual(base64.b64decode(render_data_uri({'image_path':str(path)}).split(',',1)[1]),path.read_bytes())

    def test_global_request_limit_validation(self):
        with patch.dict('os.environ',{'LLM_MAX_CONCURRENCY':'3'}):
            self.assertEqual(view_concurrency(),3)
            self.assertEqual(view_concurrency({'max_concurrency':'1'}),1)
            self.assertEqual(view_concurrency({'max_concurrency':'300'}),300)
        for value in ['0','3.5',True,-1]:
            with self.assertRaises(ValueError):view_concurrency({'max_concurrency':value})


class RecordedGridTests(unittest.TestCase):
    def setUp(self):
        self.run=ROOT/'tmp/grid_stage_two/runs/20261021_image_02'
        if not (self.run/'master_result.json').exists():self.skipTest('需要已保存的真实 API 回复')
        self.master=json.loads((self.run/'master_result.json').read_text(encoding='utf-8'))
        self.children=json.loads((self.run/'child_results.json').read_text(encoding='utf-8'))
        self.plan=json.loads((ROOT/'tmp/grid_stage_two/inputs/20261021_image_01/grids/manifest.json').read_text(encoding='utf-8'))

    def test_contract_migration_of_actual_replies_and_unknown_lengths_stay_unknown(self):
        payload=GridMasterResponse.model_validate({key:self.master[key] for key in ('members','decisions')}).model_dump(mode='json')
        parsed={row['id']:GridChildResponse.model_validate(row['result']).model_dump(mode='json') for row in self.children}
        located=locate_children(self.plan,parsed)
        review=assess_master({**payload,'view':self.master['view']},located,self.plan['tiles'])
        self.assertTrue(review['valid'],review['feedback'])
        self.assertEqual(sum(m['length']['method']=='UNKNOWN' for m in payload['members']),2)
        original=json.loads((self.run/'located_candidates.json').read_text(encoding='utf-8'))
        self.assertEqual(json.loads(json.dumps(located)),original)

    def test_provenance_survives_local_edit_validation(self):
        data=json.loads((self.run/'stage_two.json').read_text(encoding='utf-8'))
        original=copy.deepcopy(data['view_members'])
        result=validate_ledger(data)
        for old,new in zip(original,result['view_members']):
            for field in ('candidate_ids','endpoints','segmentation_status','original_label_point'):
                self.assertEqual(old[field],new[field])

    def test_source_title_axes_and_quantity_come_from_first_stage(self):
        view={'id':'view-q','title':'CONSTRUCTION DECK Q','kind':'PLAN','plane_name':'DECK-Q',
              'steel_types':['HEA260'],'quantity_reference':{'total_count':2,'spec_counts':[{'spec':'HEA260','member_cnt':2}],'issue':None}}
        one={'axis_catalog':{'per_view':{'view-q':['DECK-Q','PROJECT/7']}},'profile_catalog':['HEA260']}
        frame=frame_context(view,one)
        self.assertEqual(frame['current_view_tag'],'DECK-Q')
        self.assertEqual(frame['quantity_reference'],view['quantity_reference'])
        self.assertEqual(frame['axis_names'],['DECK-Q','PROJECT/7'])


class RecordedStage12Tests(unittest.TestCase):
    def test_unresolved_local_checks_do_not_discard_actual_api_candidates(self):
        report=ROOT/'tmp/stage12_feature_test_01'
        run=ROOT/'tmp/stage12_feature_server/data/jobs/5c27f6eba4914b84818876e270356fc7/grid_runs/attempt-grid-c2fd3651706a4ca6a523c7b26a451f31/f000-p001-view01'
        if not (run/'master_result.json').exists():self.skipTest('需要已保存的第一、二阶段真实 API 回复')
        one=json.loads((report/'stage_one.json').read_text(encoding='utf-8'))
        view=next(v for v in one['views'] if v['id']=='f000-p001-view01')
        payload=json.loads((run/'f000-p001-view01_master00_format00.json').read_text(encoding='utf-8'))
        checks=json.loads((run/'f000-p001-view01_master00_format00_checks.json').read_text(encoding='utf-8'))['feedback']
        self.assertTrue(checks)
        rows=ledger_rows(payload,view,one,checks)
        self.assertEqual(len(rows),len(payload['members']))
        self.assertTrue(any(row['grid_checks'] for row in rows))
        for original,row in zip(payload['members'],rows):
            self.assertEqual(original['spec'],row['spec'])
            self.assertEqual(original['length'],row['length_input'])
            self.assertIn(view['plane_name'],row['view_tags'])
        rechecked=validate_ledger({**one,'view_members':rows})['view_members']
        self.assertEqual([row['grid_checks'] for row in rechecked],[row['grid_checks'] for row in rows])

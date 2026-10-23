"""Only local contracts/arithmetic/geometry; no simulated API responses."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from pydantic import ValidationError
from app.label_facts import digest, in_view, load
from app.label_length_contracts import LabelLength, check_ids, evaluate, different
from app.label_length_context import prepare, frame_input
from app.label_length_workflow import review_batches


FACTS = [{'id':'dim-span','values_mm':[7200]}, {'id':'dim-end','values_mm':[2100]},
         {'id':'level-top','values_mm':[44000]}, {'id':'level-bottom','values_mm':[39000]},
         {'id':'steel-label','values_mm':[]}]


def claim(kind='STRAIGHT', dimensions=None):
    return {'label_id':'stable-label','kind':kind,'dimensions': dimensions if dimensions is not None else [[{'text_id':'dim-span','factor':1}]],
            'endpoints':['axis Q to splice P','axis R end'],'basis':'Dimension baselines correspond to these endpoints','issue':''}


class LabelLengthTests(unittest.TestCase):
    def test_reviews_keep_every_target_once_in_small_direction_batches(self):
        labels=[{'label_id':str(i),'direction':[0,1] if i%2 else [1,0],
                 'bbox_view':[i/30,.2,i/30+.01,.3]} for i in range(17)]
        targets={str(i) for i in range(15)}
        batches=review_batches({'labels':labels},targets,4)
        flat=[i for batch in batches for i in batch]
        self.assertEqual(set(flat),targets)
        self.assertEqual(len(flat),len(targets))
        self.assertTrue(all(len(batch)<=4 for batch in batches))
    def test_splice_subtraction_uses_native_operands(self):
        a = claim(dimensions=[[{'text_id':'dim-span','factor':1},{'text_id':'dim-end','factor':-1}]])
        result = evaluate(a,FACTS)
        self.assertEqual(result['length_mm'],5100)

    def test_level_difference_and_hypotenuse_are_computed_locally(self):
        a = claim('BRACE',[[{'text_id':'dim-end','factor':1}],
                           [{'text_id':'level-top','factor':1},{'text_id':'level-bottom','factor':-1}]])
        result = evaluate(a,FACTS)
        self.assertEqual(result['dimension_values_mm'],[2100,5000])
        self.assertEqual(result['length_mm'],5423.1)

    def test_profile_numbers_cannot_be_used_as_dimensions(self):
        result = evaluate(claim(dimensions=[[{'text_id':'steel-label','factor':1}]]),FACTS)
        self.assertFalse(result['usable'])

    def test_unknown_source_and_negative_length_are_rejected(self):
        for terms in ([{'text_id':'invented','factor':1}], [{'text_id':'dim-span','factor':-1}]):
            self.assertIsNone(evaluate(claim(dimensions=[terms]),FACTS)['length_mm'])

    def test_model_cannot_return_identity_changes_or_direct_computed_length(self):
        for key,value in [('spec','OTHER'),('view_tags',['OTHER']),('length_mm',10)]:
            a = claim(); a[key] = value
            with self.assertRaises(ValidationError): LabelLength.model_validate(a)

    def test_missing_duplicate_extra_ids_are_rejected(self):
        for ids in ([],['a','a'],['a','b']):
            with self.assertRaises(ValueError):
                check_ids({'members':[{'label_id':i} for i in ids]},['a'],'master')

    def test_conflicting_triangle_legs_trigger_review_even_with_same_hypotenuse(self):
        a = evaluate(claim('BRACE',[[{'text_id':'dim-end','factor':1}],[{'text_id':'dim-span','factor':1}]]),FACTS)
        b = evaluate(claim('BRACE',[[{'text_id':'dim-span','factor':1}],[{'text_id':'dim-end','factor':1}]]),FACTS)
        self.assertTrue(different(a,b))

    def test_coordinate_transform_uses_view_crop(self):
        result = in_view([.3,.4,.5,.6],[.2,.2,.8,.8])
        self.assertAlmostEqual(result[0],1/6)
        self.assertAlmostEqual(result[3],2/3)

    def test_each_seam_label_has_exactly_one_owner(self):
        with TemporaryDirectory() as folder:
            p=Path(folder); image=p/'source.png'; Image.new('RGB',(2400,800),'white').save(image)
            view={'view_id':'generic','labels':[{'label_id':str(i),'bbox_view':[x,.4,x,.5]} for i,x in enumerate((0,.5,1))]}
            plan,owners = prepare(p,image,view)
            self.assertEqual(len(owners),3)
            self.assertEqual(set(owners.values()),{t['id'] for t in plan['tiles']})

    def test_child_context_excludes_off_crop_text_and_keeps_program_axis_order(self):
        view={'view_id':'v','title':'Generic','kind':'PLAN','plane_name':'ROOF',
              'axis_order':{'HORIZONTAL':['Q','R'],'VERTICAL':[]},'axes':[],
              'text_facts':[{'id':str(i),'text':'dimension','values_mm':[1234],
                             'bbox_view':[x,.3,x,.31],'bbox_page':[0,0,1,1]} for i,x in enumerate((.2,.8))]}
        full=frame_input(view)
        local=frame_input(view,{'crop_bbox_px':[0,0,500,1000]},{'width_px':1000,'height_px':1000})
        self.assertEqual(len(full['text_facts']),2)
        self.assertEqual([f['id'] for f in local['text_facts']],['0'])
        self.assertEqual(local['axis_order'],view['axis_order'])
        self.assertNotIn('bbox_page',local['text_facts'][0])
        self.assertEqual(len(view['text_facts']),2)

    def test_manifest_hash_tampering_is_rejected_before_api(self):
        with TemporaryDirectory() as folder:
            p=Path(folder)/'facts.json'
            content={'format':'native-label-facts/v1','views':[]}
            content['bundle_sha256'] = digest(content)
            p.write_text(json.dumps(content),encoding='utf-8')
            self.assertEqual(load(p)['views'],[])
            content['views'] = [{'view_id':'edited'}]
            p.write_text(json.dumps(content),encoding='utf-8')
            with self.assertRaises(ValueError): load(p)


if __name__ == '__main__': unittest.main()

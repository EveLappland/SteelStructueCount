"""Local conservation, matching contracts and arithmetic; no mocked API calls."""
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
import hashlib
import json
import unittest

from PIL import Image

from app.entity_reduction import (compatible, conservation, exact_groups, find_cases,
    independent, validate_reply)
from app.entity_report import component_rows
from app.entity_workflow import compact_observation, run
from app.entity_inputs import read_inputs
from app.label_facts import FORMAT, digest
from app.label_length_contracts import evaluate


def observation(identity,view,plane,other,length=7200,spec='HEA220'):
    facts=[{'id':'dimension','values_mm':[length or 7200]}]
    answer={'label_id':identity,'kind':'STRAIGHT','dimensions':[[{'text_id':'dimension','factor':1}]],
            'endpoints':['left connection','right connection'],'basis':'Dimension between connection references','issue':''}
    candidate=evaluate(answer,facts) if length is not None else None
    return {'observation_id':identity,'label_id':identity,'view_uid':view,'source_plane':plane,
            'spec':spec,'component_multiplier':1,'member_cnt':1,'view_tags':[plane,other],
            'length_mm':length,'measurement':candidate,'issue':'','local_checks':[],
            'review_notes':[],'status':'MODEL_DERIVED','bbox_view':[.2,.3,.3,.4],
            'direction':[1,0],'kind':'STRAIGHT','selected_role':'expert'}


def claim(ids,source=None):
    return {'observation_ids':ids,'length_source_id':source,
            'reason':'Corresponding endpoints and source planes'}


def local_stage_two(directory, source_hash):
    """Fictional local provenance fixture, not a simulated model transport."""
    directory.mkdir();image=directory/'original.png'
    Image.new('RGB',(100,100),'white').save(image)
    row=observation('local-label','local-view','LEVEL','LINE')
    label={k:row[k] for k in ('label_id','spec','component_multiplier','member_cnt','view_tags','bbox_view','direction')}
    native={'view_id':'local-view','title':'Generic LEVEL plan','plane_name':'LEVEL','image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
            'image_size_px':[100,100],'labels':[label],'text_facts':[{'id':'dimension','values_mm':[7200]}]}
    bundle={'format':FORMAT,'source_sha256':source_hash,'views':[native]};bundle['bundle_sha256']=digest(bundle)
    selected={k:row[k] for k in ('label_id','spec','member_cnt','component_multiplier','view_tags','bbox_view','length_mm','kind')}
    selected.update({k:row['measurement']['answer'][k] for k in ('endpoints','basis','issue')})
    selected['dimension_values_mm']=row['measurement']['dimension_values_mm']
    selected.update(view_id='local-view',view_title='Generic LEVEL plan',status='MODEL_DERIVED',
                    selected_role='local',formulas=row['measurement']['formulas'],candidates={'local':row['measurement']})
    result={'format':'label-lengths/v1','stage_one_sha256':bundle['bundle_sha256'],
            'views':[{'view_id':'local-view','state':'completed','image_file':'original.png'}],'members':[selected]}
    (directory/'stage_one_facts.snapshot.json').write_text(json.dumps(bundle),encoding='utf-8')
    (directory/'stage_two.json').write_text(json.dumps(result),encoding='utf-8')
    return directory


class EntityTests(unittest.TestCase):
    def test_source_hash_namespaces_identical_local_ids_and_duplicate_sources_fail(self):
        with TemporaryDirectory() as folder:
            a=local_stage_two(Path(folder)/'a','a'*64)
            b=local_stage_two(Path(folder)/'b','b'*64)
            rows,views,sources=read_inputs([a,b])
            self.assertEqual(len(rows),2);self.assertEqual(len(views),2)
            self.assertEqual(len({r['label_id'] for r in rows.values()}),1)
            with self.assertRaisesRegex(ValueError,'重复输入'):read_inputs([a,a])

    def test_input_row_or_image_changes_fail_before_any_request(self):
        with TemporaryDirectory() as folder:
            a=local_stage_two(Path(folder)/'a','a'*64)
            path=a/'stage_two.json';original=path.read_text(encoding='utf-8')
            result=json.loads(original);result['members'][0]['view_tags']=['invented']
            path.write_text(json.dumps(result),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'固定标签'):read_inputs([a])
            path.write_text(original,encoding='utf-8')
            result=json.loads(original);result['members'][0]['length_mm']=999
            path.write_text(json.dumps(result),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'原始证据'):read_inputs([a])
            path.write_text(original,encoding='utf-8')
            Image.new('RGB',(100,100),'black').save(a/'original.png')
            with self.assertRaisesRegex(ValueError,'指纹'):read_inputs([a])

    def test_same_view_equal_labels_are_distinct_quantity(self):
        rows={r['observation_id']:r for r in [observation('a1','plan','LEVEL','LINE'),
            observation('a2','plan','LEVEL','LINE'),observation('b1','elev','LINE','LEVEL'),
            observation('b2','elev','LINE','LEVEL')]}
        cases,orphans=find_cases(rows)
        self.assertFalse(orphans)
        entities=exact_groups(cases[0],rows)
        self.assertEqual(len(entities),1)
        self.assertEqual(entities[0]['member_cnt'],2)
        self.assertEqual(len(entities[0]['observation_ids']),4)
        self.assertTrue(conservation(entities,rows)['every_observation_assigned_once'])

    def test_count_conflict_never_uses_minimum_or_maximum_by_default(self):
        rows={}
        for view,plane,other,n in [('a','LEVEL','LINE',3),('b','LINE','LEVEL',5)]:
            for i in range(n):
                identity=f'{view}{i}';rows[identity]=observation(identity,view,plane,other)
        cases,_=find_cases(rows)
        self.assertIsNone(exact_groups(cases[0],rows))
        entities=independent(rows,rows,'No adopted matching evidence')
        self.assertEqual(sum(e['member_cnt'] for e in entities),8)
        conservation(entities,rows)

    def test_unknown_and_different_lengths_still_enter_same_matching_case(self):
        a=observation('a','plan','LEVEL','LINE',None)
        b=observation('b','elev','LINE','LEVEL',6900)
        rows={'a':a,'b':b}; cases,_=find_cases(rows)
        self.assertEqual(len(cases),1)
        entities=validate_reply({'entities':[claim(['a','b'],'b')]},cases[0],rows)
        self.assertEqual(entities[0]['length_mm'],6900)
        self.assertEqual(entities[0]['member_cnt'],1)
        conservation(entities,rows)
        a['length_mm']=7100
        self.assertTrue(compatible(a,b))

    def test_parallel_planes_and_same_plane_copies_are_not_duplicates(self):
        a=observation('a','view1','ALIGNMENT-Q','LEVEL')
        b=observation('b','view2','ALIGNMENT-R','LEVEL')
        c=observation('c','view3','ALIGNMENT-Q','LEVEL')
        self.assertFalse(compatible(a,b));self.assertFalse(compatible(a,c))
        cases,orphans=find_cases({'a':a,'b':b,'c':c})
        self.assertFalse(cases);self.assertEqual(len(orphans),3)

    def test_same_view_merge_and_nonreciprocal_match_are_rejected(self):
        rows={'a':observation('a','one','LEVEL','LINE'),
              'b':observation('b','one','LEVEL','LINE')}
        case={'kind':'MATCH','observation_ids':['a','b']}
        with self.assertRaisesRegex(ValueError,'同一视图'):
            validate_reply({'entities':[claim(['a','b'],'a')]},case,rows)
        rows['b']['view_uid']='two'
        with self.assertRaisesRegex(ValueError,'相互对应'):
            validate_reply({'entities':[claim(['a','b'],'a')]},case,rows)

    def test_partition_rejects_missing_duplicate_and_extra_observations(self):
        rows={'a':observation('a','one','LEVEL','LINE')}
        case={'kind':'MATCH','observation_ids':['a']}
        for groups in ([],[claim(['a']),claim(['a'])],[claim(['outside'])]):
            with self.assertRaises(ValueError):validate_reply({'entities':groups},case,rows)

    def test_recalculation_fields_and_length_tasks_are_rejected(self):
        rows={'a':observation('a','one','LEVEL','LINE',None)}
        case={'kind':'MATCH','observation_ids':['a']}
        for field,value in [('revision',None),('length_mm',1234),('measurement',{}),('dimensions',[])]:
            c=claim(['a']);c[field]=value
            with self.assertRaises(ValueError):validate_reply({'entities':[c]},case,rows)
        with self.assertRaisesRegex(ValueError,'重算'):
            validate_reply({'entities':[claim(['a'])]},{**case,'kind':'LENGTH'},rows)

    def test_known_length_source_must_belong_to_entity(self):
        rows={'a':observation('a','one','LEVEL','LINE')}
        with self.assertRaises(ValueError):
            validate_reply({'entities':[claim(['a'],'elsewhere')]},
                           {'kind':'MATCH','observation_ids':['a']},rows)

    def test_conservation_catches_double_assignment_and_quantity_collapse(self):
        rows={i:observation(i,'one','LEVEL','LINE') for i in ('a','b')}
        entities=independent(rows,rows,'Keep separate')
        with self.assertRaises(ValueError):conservation(entities+entities,rows)
        merged=deepcopy(entities[0]);merged['observation_ids']=['a','b']
        with self.assertRaisesRegex(ValueError,'数量'):
            conservation([merged],rows)

    def test_assembly_expansion_is_derived_once_and_unknown_retains_quantity(self):
        rows={'a':observation('a','one','LEVEL','LINE',None,spec='2LF-L70*7')}
        entities=independent(rows,rows,'Keep unknown assembly')
        first=component_rows(entities);second=component_rows(entities)
        self.assertEqual(len(first),2);self.assertEqual(first,second)
        self.assertEqual(len({c['component_id'] for c in first}),2)
        self.assertTrue(all(c['length_mm'] is None for c in first))
        self.assertEqual(entities[0]['member_cnt'],1)

    def test_single_tag_bypasses_matching_even_when_unknown_or_with_issue(self):
        rows={'a':observation('a','one','LEVEL','LINE',None),'b':observation('b','two','LINE','LEVEL')}
        rows['a']['view_tags']=['LEVEL'];rows['a']['issue']='Endpoint uncertain'
        cases,orphans=find_cases(rows)
        self.assertEqual(cases,[]);self.assertEqual(set(orphans),{'a','b'})

    def test_independent_keep_cannot_clear_known_length(self):
        rows={'a':observation('a','one','LEVEL','LINE')}
        entities=validate_reply({'entities':[claim(['a'])]},{'kind':'MATCH','observation_ids':['a']},rows)
        self.assertEqual(entities[0]['length_mm'],7200)
        self.assertEqual(entities[0]['measurement'],rows['a']['measurement'])

    def test_matched_entity_cannot_erase_existing_length(self):
        rows={'a':observation('a','one','LEVEL','LINE'), 'b':observation('b','two','LINE','LEVEL',None)}
        with self.assertRaisesRegex(ValueError,'抹除'):
            validate_reply({'entities':[claim(['a','b'])]}, {'kind':'MATCH','observation_ids':['a','b']},rows)

    def test_full_spatial_description_reaches_matcher_without_expression_output(self):
        r=observation('a','one','LEVEL','LINE');r.update(axis_context={},basis='position evidence '*100)
        r['endpoints']=['grid Q to splice P','splice P to node R']
        compact=compact_observation(r)
        self.assertEqual(compact['basis'],r['basis']);self.assertEqual(compact['endpoints'],r['endpoints'])
        self.assertNotIn('current_measurement',compact)

    def test_local_run_keeps_independent_observation_without_review_task(self):
        with TemporaryDirectory() as folder:
            source=local_stage_two(Path(folder)/'input','a'*64)
            result=run([source],Path(folder)/'output',{},lambda _:None,call_api=False)
            self.assertEqual(result['cases'],[])
            self.assertEqual(result['summary']['api_request_attempts'],0)
            self.assertEqual(result['entities'][0]['length_mm'],7200)
            self.assertEqual(result['entities'][0]['method'],'INDEPENDENT')


if __name__=='__main__':unittest.main()

import unittest

from app.domain import CoordinateFrame, Observation, PointRef, StageOne, View
from app.geometry import build_lengths, calibrate, match_entities, section, validate_stage_one


EVIDENCE={'e':{'id':'e','page_id':'p','kind':'TEXT','bbox':[0,0,.1,.1],
               'text':'EL.100.000(TOS) EL.103.000(TOS) EL.111.500(TOS) 0 50 100 150 480 925 4000 6000 8000 16000 20000 24000'}}


def point(x,y,z=100,basis='TOS',offset=0):
    return PointRef(x={'axis_label':x,'evidence_ids':['e']},
                    y={'axis_label':y,'evidence_ids':['e']},
                    z={'value':z,'basis':basis,'reference_basis':basis,'offset_mm':offset,'evidence_ids':['e']}).model_dump()


def observation(identity='a',view_id='plan',start=None,end=None,profile='HEA260'):
    return Observation(id=identity,view_id=view_id,profile=profile,material='S355JR',
        image_start=[.2,.2],image_end=[.7,.2],start=start or point('1','A'),end=end or point('2','A'),
        start_boundary={'kind':'END','evidence_ids':['e']},
        end_boundary={'kind':'END','evidence_ids':['e']},evidence_ids=['e']).model_dump()


def fixture(observations=None):
    frame=CoordinateFrame(axes=[
        {'direction':'X','label':'1','value_mm':0,'evidence_ids':['e']},
        {'direction':'X','label':'2'}, {'direction':'X','label':'3'},
        {'direction':'Y','label':'A','value_mm':0,'evidence_ids':['e']},
        {'direction':'Y','label':'B'},
    ],dimensions=[
        {'direction':'X','from_label':'1','to_label':'2','distance_mm':8000,'evidence_ids':['e']},
        {'direction':'X','from_label':'2','to_label':'3','distance_mm':16000,'evidence_ids':['e']},
        {'direction':'Y','from_label':'A','to_label':'B','distance_mm':6000,'evidence_ids':['e']},
    ],z_origin_m=100,origin_evidence_ids=['e'])
    views=[View(id='plan',page_id='p',title='PLAN AT EL.100.000(TOS)',kind='PLAN',bbox=[0,0,1,1],
                title_bbox=[0,.9,.3,1],visible_axes=['X','Y'],fixed_axis='Z',elevation_m=100,elevation_basis='TOS'),
           View(id='elevation-A',page_id='p',title='ELEVATION A',kind='ELEVATION',bbox=[0,0,1,1],
                title_bbox=[0,.9,.3,1],visible_axes=['X','Z'],fixed_axis='Y',fixed_label='A',mirrored=True)]
    return StageOne(views=views,frame=frame,observations=observations or [observation()]).model_dump()


class CoordinateAndLengthTest(unittest.TestCase):
    def test_dimension_chain_and_axis_names(self):
        data=fixture()
        values,_,issues=calibrate(data['frame'],EVIDENCE)
        self.assertEqual(values[('X','3')],24000)
        self.assertEqual(values[('Y','B')],6000)
        self.assertFalse(issues)
        # Axis names are labels, not numeric indices or page x/y.
        for a in data['frame']['axes']:
            if a['direction']=='X':a['label']='grid-'+a['label']
        for d in data['frame']['dimensions']:
            if d['direction']=='X':d['from_label']='grid-'+d['from_label'];d['to_label']='grid-'+d['to_label']
        self.assertEqual(calibrate(data['frame'],EVIDENCE)[0][('X','grid-3')],24000)

    def test_conflicting_chain_blocks_component(self):
        data=fixture()
        data['frame']['dimensions'].append({'direction':'X','from_label':'1','to_label':'3',
                                           'distance_mm':20000,'evidence_ids':['e']})
        values,_,issues=calibrate(data['frame'],EVIDENCE)
        self.assertNotIn(('X','1'),values)
        self.assertIn(('Y','A'),values)
        self.assertTrue(issues)
        self.assertFalse(build_lengths(data,EVIDENCE)['candidates'][0]['confirmed'])

    def test_offset_towards_and_millimetre_elevation(self):
        o=observation()
        o['start']['x'].update(offset_mm=480,towards='2')
        o['end']['x'].update(offset_mm=925,towards='1')
        for p in ('start','end'):
            o[p]['z'].update(value=100000,unit='mm',offset_mm=-100)
        data=build_lengths(fixture([o]),EVIDENCE)
        candidate=data['candidates'][0]
        self.assertTrue(candidate['confirmed'],candidate['issues'])
        self.assertEqual(candidate['start_mm'],[480,0,-100])
        self.assertEqual(candidate['end_mm'],[7075,0,-100])
        self.assertEqual(candidate['length_mm'],6595)

    def test_planar_brace_uses_pythagoras(self):
        o=observation(start=point('1','A'),end=point('2','B'))
        c=build_lengths(fixture([o]),EVIDENCE)['candidates'][0]
        self.assertTrue(c['confirmed'],c['issues'])
        self.assertEqual(c['length_mm'],10000)

    def test_three_dimensional_brace_and_missing_end_are_pending(self):
        o=observation(start=point('1','A'),end=point('2','B',103))
        c=build_lengths(fixture([o]),EVIDENCE)['candidates'][0]
        self.assertFalse(c['confirmed'])
        o=observation();o['end_boundary']['kind']='UNKNOWN'
        self.assertFalse(build_lengths(fixture([o]),EVIDENCE)['candidates'][0]['confirmed'])

    def test_tos_is_not_implicitly_centerline(self):
        o=observation();o['start']['z']['reference_basis']='CENTERLINE'
        c=build_lengths(fixture([o]),EVIDENCE)['candidates'][0]
        self.assertFalse(c['confirmed'])
        self.assertTrue(any('基准' in issue for issue in c['issues']))

    def test_explicit_basis_conversion_and_vertical_splice(self):
        o=observation(view_id='elevation-A',start=point('1','A',100,basis='BOB'),
                      end=point('1','A',111.5,basis='TOS'))
        o['end_boundary']['kind']='SPLICE'
        o['start']['z'].update(offset_mm=50,reference_basis='CENTERLINE')
        o['end']['z'].update(offset_mm=-150,reference_basis='CENTERLINE')
        c=build_lengths(fixture([o]),EVIDENCE)['candidates'][0]
        self.assertTrue(c['confirmed'],c['issues'])
        self.assertEqual(c['length_mm'],11300)

    def test_mirrored_view_does_not_invert_semantic_coordinates(self):
        a=observation('a','plan');b=observation('b','elevation-A')
        b['image_start'],b['image_end']=[.8,.2],[.3,.2]
        data=build_lengths(fixture([a,b]),EVIDENCE)
        self.assertEqual(data['candidates'][0]['start_mm'],data['candidates'][1]['start_mm'])
        self.assertEqual(len(match_entities(data)['members']),1)

    def test_invalid_evidence_is_not_accepted(self):
        data=fixture();data['observations'][0]['evidence_ids']=['invented']
        with self.assertRaises(ValueError):validate_stage_one(data,EVIDENCE,{'p'})

    def test_no_pixel_length_fallback(self):
        data=fixture();data['observations'][0]['start']['x']['axis_label']=None
        c=build_lengths(data,EVIDENCE)['candidates'][0]
        self.assertIsNone(c['start_mm'])
        self.assertFalse(c['confirmed'])

    def test_existing_text_id_does_not_prove_invented_dimension(self):
        evidence={'e':{**EVIDENCE['e'],'text':'EL.100.000(TOS)'}}
        result=build_lengths(fixture(),evidence)
        self.assertFalse(result['candidates'][0]['confirmed'])
        self.assertTrue(result['frame_issues'])

    def test_fixed_axis_can_be_supplied_by_view_title(self):
        data=fixture([observation(view_id='elevation-A')])
        data['views'][1]['evidence_ids']=['e']
        for p in ('start','end'):data['observations'][0][p]['y']['axis_label']=None
        candidate=build_lengths(data,EVIDENCE)['candidates'][0]
        self.assertTrue(candidate['confirmed'],candidate['issues'])
        self.assertEqual(candidate['start_mm'][1],0)

    def test_view_fixed_alignment_must_match(self):
        o=observation(view_id='elevation-A',start=point('1','B'),end=point('2','B'))
        self.assertFalse(build_lengths(fixture([o]),EVIDENCE)['candidates'][0]['confirmed'])


class EntityMatchingTest(unittest.TestCase):
    def test_reverse_endpoints_and_three_projections_count_once(self):
        a=observation('a');b=observation('b','elevation-A',start=a['end'],end=a['start'])
        data=build_lengths(fixture([a,b]),EVIDENCE)
        result=match_entities(data)
        self.assertEqual(len(result['members']),1)
        self.assertEqual(result['members'][0]['observation_ids'],['a','b'])

    def test_two_angles_expand_once_after_deduplication(self):
        data=build_lengths(fixture([observation('a',profile='2LF- L120*12'),
                                   observation('b','elevation-A',profile='2LF- L120*12')]),EVIDENCE)
        result=match_entities(data)
        self.assertEqual(len(result['members']),2)
        self.assertEqual({m['component_index'] for m in result['members']},{1,2})
        self.assertEqual(len({m['assembly_id'] for m in result['members']}),1)
        self.assertEqual(result['summary'][0]['quantity'],2)
        self.assertEqual(result['summary'][0]['total_length_mm'],16000)

    def test_equal_length_different_location_is_not_duplicate(self):
        a=observation('a');b=observation('b',start=point('1','B'),end=point('2','B'))
        result=match_entities(build_lengths(fixture([a,b]),EVIDENCE))
        self.assertEqual(len(result['members']),2)

    def test_different_profile_at_same_endpoints_is_conflict(self):
        data=build_lengths(fixture([observation('a'),observation('b','elevation-A',profile='HEA300')]),EVIDENCE)
        result=match_entities(data)
        self.assertEqual(len(result['members']),0)
        self.assertEqual(len(result['exceptions']),2)
        with self.assertRaises(ValueError):
            match_entities(data,[{'left':'a','right':'b','action':'SAME','reason':'same pixels'}])

    def test_adjacent_and_overlapping_segments_are_not_automatically_joined(self):
        a=observation('a');b=observation('b',start=point('2','A'),end=point('3','A'))
        data=build_lengths(fixture([a,b]),EVIDENCE)
        result=match_entities(data)
        self.assertEqual(result['conflicts'][0]['kind'],'ADJACENT')
        self.assertFalse(result['members'])
        result=match_entities(data,[{'left':'a','right':'b','action':'DISTINCT','reason':'two explicit end connections'}])
        self.assertEqual(len(result['members']),2)
        b['start']=point('1','A');b['start']['x']['offset_mm']=4000
        self.assertEqual(match_entities(build_lengths(fixture([a,b]),EVIDENCE))['conflicts'][0]['kind'],'OVERLAP')

    def test_crossing_braces_do_not_split_or_merge(self):
        a=observation('a',start=point('1','A'),end=point('2','B'))
        b=observation('b',start=point('1','B'),end=point('2','A'))
        result=match_entities(build_lengths(fixture([a,b]),EVIDENCE))
        self.assertEqual(len(result['members']),2)
        self.assertFalse(result['conflicts'])

    def test_one_mm_euclidean_tolerance_and_no_tolerance_chains(self):
        data=build_lengths(fixture([observation('a'),observation('b','elevation-A')]),EVIDENCE)
        for p in ('start_mm','end_mm'):data['candidates'][1][p][0]+=.8
        self.assertEqual(len(match_entities(data)['members']),1)
        for p in ('start_mm','end_mm'):data['candidates'][1][p][1]+=.8
        self.assertFalse(match_entities(data)['members'])
        data=build_lengths(fixture([observation('a'),observation('b','elevation-A'),observation('c','elevation-A')]),EVIDENCE)
        for index,c in enumerate(data['candidates']):
            for p in ('start_mm','end_mm'):c[p][0]+=index*.8
        self.assertFalse(match_entities(data)['members'])

    def test_same_view_duplicate_and_independent_coincident_assemblies(self):
        data=build_lengths(fixture([observation('a'),observation('b')]),EVIDENCE)
        self.assertFalse(match_entities(data)['members'])
        same=match_entities(data,[{'left':'a','right':'b','action':'SAME','reason':'duplicate flange detection'}])
        self.assertEqual(len(same['members']),1)
        separate=match_entities(data,[{'left':'a','right':'b','action':'DISTINCT','reason':'independent coincident pieces'}])
        self.assertEqual(len(separate['members']),2)

    def test_invalid_quantity_prefix_and_deferred_observation(self):
        with self.assertRaises(ValueError):section('99LF-L120*12')
        o=observation();o['deferred']=True
        result=match_entities(build_lengths(fixture([o]),EVIDENCE))
        self.assertFalse(result['members'])
        self.assertEqual(len(result['exceptions']),1)

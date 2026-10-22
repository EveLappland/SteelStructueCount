import copy
import unittest

from app.group_reconciliation import reconcile
from test_group_dedup import fixture,row,TAGS


def resolution(identity='r',count=3,allocations=None,**changes):
    return {'id':identity,'spec':'HEA240','view_tags':TAGS,'length_mm':8000,
        'member_cnt':count,'allocations':allocations or [{'row_id':'p1','member_cnt':count},
            {'row_id':'a1','member_cnt':count}], 'reason':'按原图核对对应分段及数量',**changes}


class ReconciliationTests(unittest.TestCase):
    def test_completed_warning_sources_are_not_mislabeled_as_unprocessed(self):
        two=fixture([row('p1','p'),row('a1','a')])
        for analysis in two['view_analysis']:
            analysis['status']='completed_with_warnings'
        two['view_analysis'][0]['label_count']=4
        draft=reconcile(two);draft['resolutions']=[resolution()]
        result=reconcile(two,draft)
        self.assertEqual(result['totals']['pending_source_views'],0)
        self.assertEqual(result['totals']['confirmed_single_members'],3)
        self.assertTrue(any('覆盖疑点' in issue for e in result['exceptions'] for issue in e['issues']))
        self.assertFalse(any('未完成识别' in issue for e in result['exceptions'] for issue in e['issues']))

    def test_confirmed_groups_are_derived_and_source_is_immutable(self):
        two=fixture([row('p1','p'),row('a1','a')]);before=copy.deepcopy(two)
        three=reconcile(two);self.assertEqual(three['summary'],[])
        three['resolutions']=[resolution()];three['members']=[{'id':'forged'}]
        result=reconcile(two,three)
        self.assertEqual(result['totals']['confirmed_single_members'],3)
        self.assertEqual(result['totals']['allocated_observation_units'],6)
        self.assertEqual(result['exceptions'],[])
        self.assertEqual(reconcile(two,result),result)
        self.assertEqual(two,before)

    def test_partial_overlap_is_explicit_and_conserves_all_observations(self):
        two=fixture([row('p1','p',3),row('a1','a',2)])
        draft=reconcile(two);draft['resolutions']=[resolution(count=2),
            resolution('separate',1,[{'row_id':'p1','member_cnt':1}])]
        result=reconcile(two,draft)
        self.assertEqual(result['totals']['confirmed_single_members'],3)
        self.assertEqual(result['totals']['allocated_observation_units'],5)
        self.assertFalse(result['exceptions'])
        draft['resolutions']=[resolution(count=3,allocations=[{'row_id':'p1','member_cnt':3},{'row_id':'a1','member_cnt':2}])]
        with self.assertRaisesRegex(ValueError,'部分重叠'):reconcile(two,draft)

    def test_double_consumption_stale_source_invalid_labels_and_profiles_are_rejected(self):
        two=fixture([row('p1','p'),row('a1','a')]);draft=reconcile(two)
        draft['resolutions']=[resolution(),resolution('duplicate')]
        with self.assertRaisesRegex(ValueError,'重复扣减'):reconcile(two,draft)
        draft['resolutions']=[resolution()]
        changed=copy.deepcopy(two);changed['view_members'][0]['length_mm']=9000
        with self.assertRaisesRegex(ValueError,'已改变'):reconcile(changed,draft)
        for change in ({'spec':'HEA999'},{'view_tags':['unknown']},{'view_tags':[TAGS[0]]}):
            draft['resolutions']=[resolution(**change)]
            with self.assertRaises(ValueError):reconcile(two,draft)

    def test_combo_expansion_once_and_exclusions_remain_auditable(self):
        two=fixture([row('p1','p',2,spec='2LF-L120*12'),row('a1','a',3,spec='2LF-L120*12')])
        draft=reconcile(two);draft['resolutions']=[resolution(count=2,spec='2LF-L120*12')]
        draft['exclusions']=[{'row_id':'a1','member_cnt':1,'reason':'连接详图重复标签已核对'}]
        result=reconcile(two,draft)
        self.assertEqual(result['summary'][0]['quantity'],4)
        self.assertEqual(len(result['members']),4)
        self.assertEqual(result['totals']['excluded_observation_units'],1)
        self.assertFalse(result['exceptions'])
        self.assertEqual(reconcile(two,result),result)
        result['exclusions'].append({'row_id':'a1','member_cnt':1,'reason':'多排除'})
        with self.assertRaises(ValueError):reconcile(two,result)

    def test_coverage_gaps_cannot_disappear_when_all_existing_rows_are_allocated(self):
        two=fixture([row('p1','p'),row('a1','a')])
        two['view_analysis'][0]['label_count']=4
        two['view_analysis'][2]['status']='region_pending'
        draft=reconcile(two);draft['resolutions']=[resolution()]
        result=reconcile(two,draft)
        self.assertEqual(result['totals']['pending_observation_units'],0)
        self.assertEqual(len(result['exceptions']),2)
        self.assertEqual(result['totals']['pending_source_views'],1)

    def test_distinct_result_ids_do_not_collapse_coincident_spec_length_and_tags(self):
        two=fixture([row('p1','p',2),row('a1','a',2)])
        draft=reconcile(two);draft['resolutions']=[resolution('first',1),resolution('second',1)]
        result=reconcile(two,draft)
        self.assertEqual(len(result['confirmed_groups']),2)
        self.assertEqual(result['summary'][0]['quantity'],2)
        draft['resolutions'][1]['id']='first'
        with self.assertRaisesRegex(ValueError,'ID不可重复'):reconcile(two,draft)


if __name__=='__main__':unittest.main()

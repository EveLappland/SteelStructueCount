"""Deterministic stage handoff and deduplication, with no simulated model calls."""
import copy
import unittest

from app.group_reconciliation import reconcile, reconcile_exact
from app.member_recognition import validate_rows, member_key, key_record
from test_group_dedup import fixture, row, TAGS
from test_group_reconciliation import resolution


class ExactMemberKeyTests(unittest.TestCase):
    def test_source_plane_added_before_key_without_losing_rows_or_counts(self):
        plan={'id':'p','page_id':'page','kind':'PLAN','title':'PLAN AT EL.20.000(TOS)'}
        one={'views':[plan],'profile_catalog':['HEA240'],
             'axis_catalog':{'axes':[{'name':'FRAME/A'},{'name':'FRAME/B'}]}}
        raw=[{**row('one','p',2),'view_tags':['ELEVATION ON ALIGNMENT FRAME/A']},
             {**row('two','p',1),'view_tags':['FRAME/B']},
             {**row('three','p',3),'view_tags':['FRAME/A']}]
        original=copy.deepcopy(raw)
        parsed=validate_rows(raw,plan,one)
        self.assertEqual(len(parsed),3)
        self.assertEqual(sum(r['member_cnt'] for r in parsed),6)
        for result in parsed:
            self.assertIn('EL.20.000(TOS)',result['view_tags'])
            self.assertEqual(result['member_key'],key_record(result))
        self.assertEqual(member_key(parsed[0]),member_key(parsed[2]))
        self.assertNotEqual(member_key(parsed[0]),member_key(parsed[1]))
        self.assertEqual(raw,original)
        self.assertEqual(validate_rows(parsed,plan,one),parsed)

    def test_different_plan_levels_cannot_merge_even_with_same_spec_length(self):
        two=fixture([row('p1','p',47,view_tags=[TAGS[0]]),
                     row('q1','q',51,view_tags=['EL.23.000(TOS)'])])
        two['views'].append({'id':'q','kind':'PLAN','title':'PLAN AT EL.23.000(TOS)'})
        two['axis_catalog']['axes'].append({'name':'EL.23.000(TOS)'})
        before=copy.deepcopy(two)
        result=reconcile_exact(two)
        self.assertEqual(result['totals']['confirmed_single_members'],98)
        self.assertEqual(result['totals']['deduplicated_observation_units'],0)
        self.assertEqual(len(result['resolutions']),2)
        self.assertEqual(two,before)
        draft=reconcile(two)
        draft['resolutions']=[resolution(count=47,view_tags=[TAGS[0],'EL.23.000(TOS)'],
            allocations=[{'row_id':'p1','member_cnt':47},{'row_id':'q1','member_cnt':47}])]
        with self.assertRaisesRegex(ValueError,'key'):reconcile(two,draft)

    def test_same_key_sums_within_source_and_deduplicates_across_sources(self):
        two=fixture([row('p1','p',1),row('p2','p',2),row('a1','a',3)])
        result=reconcile_exact(two)
        self.assertEqual(result['totals']['confirmed_single_members'],3)
        self.assertEqual(result['totals']['allocated_observation_units'],6)
        self.assertEqual(result['totals']['deduplicated_observation_units'],3)
        self.assertEqual(len(result['resolutions'][0]['allocations']),3)
        self.assertEqual(reconcile_exact(two),result)
        self.assertEqual(reconcile(two,result),result)

    def test_different_keys_and_unequal_counts_never_silently_discard_members(self):
        for change in ({'length_mm':8000.5},{'spec':'2LF-L120*12'},
                       {'view_tags':['FRAME/A']},{'member_cnt':2}):
            two=fixture([row('p1','p',3),row('a1','a',3,**change)]) if 'member_cnt' not in change else fixture([row('p1','p',3),row('a1','a',2)])
            result=reconcile_exact(two)
            self.assertEqual(result['totals']['allocated_observation_units'],sum(r['member_cnt'] for r in two['view_members']))
            self.assertEqual(result['totals']['deduplicated_observation_units'],0)
            self.assertFalse(result['exclusions'])

    def test_key_cannot_be_rewritten_by_proposal_or_stale_metadata(self):
        two=fixture([row('p1','p'),row('a1','a')])
        for changes in ({'length_mm':8100},{'view_tags':[*TAGS,'FRAME/1']},
                        {'spec':'2LF-L120*12'}):
            draft=reconcile(two);draft['resolutions']=[resolution(**changes)]
            with self.assertRaisesRegex(ValueError,'key'):reconcile(two,draft)
        two['view_members'][0]['member_key']={**key_record(two['view_members'][0]),'length_mm':1}
        with self.assertRaisesRegex(ValueError,'key'):reconcile_exact(two)

    def test_combination_expansion_once_and_unknown_length_retained_pending(self):
        two=fixture([row('p1','p',2,spec='2LF-L120*12'),
                     row('a1','a',2,spec='2F-L120*12'),row('unknown','p',1,length_mm=None)])
        result=reconcile_exact(two)
        self.assertEqual(result['totals']['confirmed_assemblies'],2)
        self.assertEqual(result['totals']['confirmed_single_members'],4)
        self.assertEqual(result['totals']['pending_observation_units'],1)
        self.assertTrue(any(e['observation_id']=='unknown' for e in result['exceptions']))

import copy
import unittest

from app.group_dedup import build_group_review,expand_confirmed_groups


TAGS=['EL.20.000(TOS)','FRAME/A']


def fixture(rows):
    views=[{'id':'p','kind':'PLAN','title':'PLAN AT EL.20.000(TOS)'},
           {'id':'a','kind':'ELEVATION','title':'ELEVATION ON ALIGNMENT FRAME/A','fixed_label':'FRAME/A'},
           {'id':'b','kind':'ELEVATION','title':'ELEVATION ON ALIGNMENT FRAME/1','fixed_label':'FRAME/1'}]
    counts={v['id']:sum(r['member_cnt'] for r in rows if r['source_view']==v['id']) for v in views}
    return {'views':views,'profile_catalog':['HEA240','L120*12'],
        'axis_catalog':{'axes':[{'name':t} for t in [*TAGS,'FRAME/1']],
                        'per_view':{v['id']:[*TAGS,'FRAME/1'] for v in views}},
        'view_analysis':[{'view_id':v['id'],'status':'completed','member_cnt':counts[v['id']],
                         'label_count':counts[v['id']],'coverage_differences':[]} for v in views],
        'view_members':rows}


def row(identity,source,count=3,**changes):
    return {'id':identity,'source_view':source,'spec':'HEA240','view_tags':TAGS,
            'length_mm':8000,'member_cnt':count,'location':'已核对的构件组','issue':None,'checks':[],**changes}


class GroupDedupTests(unittest.TestCase):
    def test_three_sources_are_reconciled_once_and_do_not_mutate_input(self):
        data=fixture([row('1','p',view_tags=[*TAGS,'FRAME/1']),row('2','a',view_tags=[*TAGS,'FRAME/1']),
                      row('3','b',view_tags=[*TAGS,'FRAME/1'])])
        original=copy.deepcopy(data)
        result=build_group_review(data)
        self.assertEqual(result['summary']['trial_removed_observations'],6)
        self.assertEqual(result['summary']['trial_remaining_observations'],3)
        self.assertEqual(result['summary']['confirmed_groups'],0)
        self.assertIsNone(result['summary']['confirmed_final_quantity'])
        self.assertEqual(build_group_review(data),result)
        self.assertEqual(data,original)

    def test_same_source_independent_rows_are_summed_before_cross_source_comparison(self):
        result=build_group_review(fixture([row('1','p',1),row('2','p',2),row('3','a',3)]))
        group=result['groups'][0]
        self.assertEqual(group['agreed_member_cnt'],3)
        self.assertEqual(sum(len(s['row_ids']) for s in group['sources']),3)
        self.assertEqual(result['summary']['trial_remaining_observations'],3)

    def test_quantity_conflicts_never_use_max_or_min(self):
        result=build_group_review(fixture([row('1','p',3),row('2','a',2)]))
        self.assertEqual(result['summary']['count_conflicts'],1)
        self.assertEqual(result['summary']['trial_remaining_observations'],5)
        self.assertIsNone(result['groups'][0]['agreed_member_cnt'])

    def test_single_tags_and_partial_overlap_only_generate_review_candidates(self):
        result=build_group_review(fixture([row('1','p',view_tags=[TAGS[0]]),row('2','a')]))
        self.assertEqual(result['summary']['trial_removed_observations'],0)
        self.assertEqual(result['relations'][0]['kind'],'incomplete_tags_candidate')

    def test_different_locations_and_assembly_identities_do_not_merge(self):
        result=build_group_review(fixture([row('1','p'),row('2','b',view_tags=[TAGS[0],'FRAME/1']),
                                          row('3','a',spec='2LF-L120*12'),row('4','p',spec='L120*12')]))
        self.assertEqual(len(result['groups']),4)
        self.assertEqual(result['summary']['trial_removed_observations'],0)

    def test_length_distribution_and_rounding_are_reviewed_not_merged(self):
        result=build_group_review(fixture([row('1','p',length_mm=5510),row('2','a',length_mm=6000)]))
        self.assertEqual(result['summary']['trial_removed_observations'],0)
        self.assertEqual(len(result['distribution_issues']),1)
        result=build_group_review(fixture([row('1','p'),row('2','a',length_mm=8000.5)]))
        self.assertEqual(result['relations'][0]['kind'],'rounding_candidate')
        self.assertEqual(result['summary']['trial_removed_observations'],0)
        # Several real lengths present in BOTH sources are not a discrepancy.
        result=build_group_review(fixture([row('1','p',length_mm=5000),row('2','a',length_mm=5000),
                                          row('3','p'),row('4','a')]))
        self.assertFalse(result['distribution_issues'])

    def test_missing_length_row_issue_and_incomplete_coverage_block_trial(self):
        for changes in ({'length_mm':None},{'issue':'拼接待核验'},{'view_tags':['UNKNOWN','FRAME/A']}):
            result=build_group_review(fixture([row('1','p',**changes),row('2','a',**changes)]))
            self.assertEqual(result['summary']['trial_removed_observations'],0)
        data=fixture([row('1','p'),row('2','a')]);data['view_analysis'][0]['coverage_differences']=[{'spec':'HEA240'}]
        self.assertEqual(build_group_review(data)['summary']['trial_removed_observations'],0)

    def test_angle_expansion_requires_confirmation_and_is_idempotent(self):
        result=build_group_review(fixture([row('1','p',2,spec='2LF-L120*12'),row('2','a',2,spec='2LF-L120*12')]))
        group=result['groups'][0]
        with self.assertRaises(ValueError):expand_confirmed_groups([group])
        confirmed={**group,'confirmed':True,'confirmed_member_cnt':2}
        expected=[{'profile':'L120*12','length_mm':8000.0,'quantity':4,'total_length_mm':32000.0}]
        self.assertEqual(expand_confirmed_groups([confirmed]),expected)
        self.assertEqual(expand_confirmed_groups([confirmed]),expected)
        self.assertFalse(group['confirmed'])
        with self.assertRaises(ValueError):expand_confirmed_groups([confirmed,confirmed])

    def test_profile_disagreements_are_candidates_not_forced_replacements(self):
        data=fixture([row('1','p'),row('2','a',spec='HEA300')]);data['profile_catalog'].append('HEA300')
        result=build_group_review(data)
        self.assertEqual(len(result['profile_distribution_issues']),1)
        self.assertEqual(result['summary']['trial_removed_observations'],0)

    def test_duplicate_row_ids_are_rejected(self):
        with self.assertRaises(ValueError):build_group_review(fixture([row('same','p'),row('same','a')]))


if __name__=='__main__':unittest.main()

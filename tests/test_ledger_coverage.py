import unittest

from app.ledger_coverage import refresh_coverage


class CoverageTests(unittest.TestCase):
    def test_manual_count_correction_refreshes_coverage_and_removes_stale_warning(self):
        data={'views':[{'id':'v','title':'Example','page_id':'p','bbox':[0,0,1,1]}],
            'view_analysis':[{'view_id':'v','status':'completed','member_cnt':1,'label_count':2,
                              'coverage_differences':[{'spec':'HEA240','label_count':2,'member_cnt':1}]}],
            'warnings':['Example：2处规格标签与1个候选段不一致，需核对。','保留其他疑点'],
            'view_members':[{'source_view':'v','spec':'HEA240','member_cnt':2}]}
        pages=[{'id':'p','texts':[{'text':'HEA240','bbox':[.1,.1,.2,.2]},
                                 {'text':'HEA240','bbox':[.3,.3,.4,.4]}]}]
        result=refresh_coverage(data,pages)
        self.assertEqual(result['view_analysis'][0]['member_cnt'],2)
        self.assertEqual(result['view_analysis'][0]['coverage_differences'],[])
        self.assertEqual(result['warnings'],['保留其他疑点'])
        result['view_members']=[]
        result=refresh_coverage(result,pages)
        self.assertEqual(result['view_analysis'][0]['status'],'empty')
        self.assertTrue(result['view_analysis'][0]['coverage_differences'])

    def test_unrecognized_view_cannot_be_marked_complete_by_editing_a_count(self):
        data={'views':[],'view_members':[], 'view_analysis':[{'view_id':'pending','status':'region_pending'}]}
        self.assertEqual(refresh_coverage(data,[])['view_analysis'][0]['status'],'region_pending')

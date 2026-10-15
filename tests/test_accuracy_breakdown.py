import unittest
from app.accuracy_breakdown import breakdown, _pairs

class BreakdownTests(unittest.TestCase):
    def test_subset_pairing_and_reversal(self):
        self.assertEqual(_pairs([10,30],[8,11,31]),[(30,31),(10,11)])
        self.assertEqual(_pairs([8,11,31],[10,30]),[(31,30),(11,10)])

    def test_count_length_profile_and_assembly_metrics(self):
        ref={'scope':{},'members':[dict(spec='2LF-L120*12',view_tags=['A'],length_mm=5000,member_cnt=1)]}
        pred=[dict(spec='L120*12',view_tags=['A'],length_mm=5100,member_cnt=1),
              dict(spec='HEA260',view_tags=['B'],length_mm=6000,member_cnt=1)]
        result=breakdown(ref,pred);row=next(r for r in result['profiles'] if r['profile']=='L120*12')
        self.assertEqual(result['profile_precision'],.5)
        self.assertEqual(row['expected_count'],2)
        self.assertEqual(row['count_accuracy'],.5)
        self.assertEqual(row['length_mae_mm'],100)
        self.assertEqual(row['expected_unpaired_length_count'],1)
        self.assertEqual(row['recall'],0)

import unittest

from app.accuracy import score


def member(length=5000,quantity=1,tags=None,spec='HEA240'):
    return {'spec':spec,'view_tags':tags or ['FRAME/A','EL.20.000(TOS)'],
            'length_mm':length,'member_cnt':quantity}


class AccuracyTests(unittest.TestCase):
    def test_same_total_does_not_hide_wrong_spec_length_or_duplicate_plane(self):
        ref={'members':[member(quantity=10)]}
        self.assertEqual(score(ref,[member(quantity=10,length=6000)])['recall'],0)
        self.assertEqual(score(ref,[member(quantity=10,tags=['FRAME/B'])])['recall'],0)
        self.assertEqual(score(ref,[member(quantity=20)])['precision'],.5)
        self.assertFalse(score(ref,[member(quantity=20)])['passed_90_percent'])

    def test_quantity_weighting_combos_and_unknowns_stay_in_denominator(self):
        ref={'members':[member(quantity=4),member(spec='2LF-L120*12',quantity=2),member(length=None,quantity=2)]}
        result=score(ref,[member(quantity=4),member(spec='L120*12',quantity=4)])
        self.assertEqual(result['matched_single_members'],8)
        self.assertEqual(result['expected_single_members'],10)
        self.assertEqual(result['recall'],.8)

    def test_maximum_matching_handles_rounding_ambiguity_without_reusing_counts(self):
        ref={'members':[member(5000),member(5002)]}
        result=score(ref,[member(5001),member(4999)])
        self.assertEqual(result['matched_single_members'],2)
        self.assertEqual(result['precision'],1)
        self.assertEqual(score(ref,[member(5001)])['matched_single_members'],1)
        with self.assertRaises(ValueError):score(ref,[],tolerance_mm=10)

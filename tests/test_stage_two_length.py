"""Local geometry only; these tests never call or imitate the model API."""
import unittest

from app.member_recognition import LengthInput, calculate_length


class StageTwoLengthTests(unittest.TestCase):
    def test_diagonal_uses_supplied_perpendicular_dimensions(self):
        value, formula, issues = calculate_length(LengthInput(
            method='DIAGONAL', direct_mm=None, horizontal_mm=3000,
            vertical_mm=3700, evidence='图示水平和竖向尺寸'))
        self.assertEqual(value, 4763.40)
        self.assertIn('3000', formula)
        self.assertEqual(issues, [])

    def test_diagonal_without_both_legs_is_pending(self):
        value, _, issues = calculate_length(LengthInput(
            method='DIAGONAL', horizontal_mm=3000, vertical_mm=None, evidence='仅见一边'))
        self.assertIsNone(value)
        self.assertTrue(issues)

    def test_direct_length_is_not_recalculated(self):
        value, _, issues = calculate_length(LengthInput(
            method='DIRECT', direct_mm=6000, evidence='图示6000'))
        self.assertEqual(value, 6000)
        self.assertEqual(issues, [])


if __name__ == '__main__':
    unittest.main()

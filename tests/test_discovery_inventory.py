import unittest
from app.discovery_inventory import labels


class DiscoveryInventoryTest(unittest.TestCase):
    def test_label_hints_exclude_dimensions_and_preserve_printed_family(self):
        self.assertEqual(list(labels('2LF- L120*12 HEA260 /EMH IPET270 /LT')),
                         [('L120*12',2),('HEA260',1),('IPET270',1)])
        self.assertFalse(list(labels('EL.107.500 TOS 8000 BP1 N8 10 THK STIFF PIPE RACK')))
        self.assertEqual(list(labels('2L-L80×8 L100x75x8 SHS100*100*6')),
                         [('L80*8',2),('L100*75*8',1),('SHS100*100*6',1)])
        self.assertEqual(list(labels('2F-L120*12 2F- L80×8')),
                         [('L120*12',2),('L80*8',2)])

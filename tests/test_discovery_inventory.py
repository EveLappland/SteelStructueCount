import unittest
from app.discovery_inventory import build_inventory, labels


class DiscoveryInventoryTest(unittest.TestCase):
    def test_label_hints_exclude_dimensions_and_preserve_printed_family(self):
        self.assertEqual(list(labels('2LF- L120*12 HEA260 /EMH IPET270 /LT')),
                         [('L120*12',2),('HEA260',1),('IPET270',1)])
        self.assertFalse(list(labels('EL.107.500 TOS 8000 BP1 N8 10 THK STIFF PIPE RACK')))
        self.assertEqual(list(labels('2L-L80×8 L100x75x8 SHS100*100*6')),
                         [('L80*8',2),('L100*75*8',1),('SHS100*100*6',1)])
        self.assertEqual(list(labels('2F-L120*12 2F- L80×8')),
                         [('L120*12',2),('L80*8',2)])

    def test_catalog_survives_missing_members_and_view_region(self):
        texts=[{'id':'p:t1','text':'HEA260 /EPB','bbox':[.1,.1,.2,.2]},
               {'id':'p:t2','text':'HEA260 /EMH 2LF- L120*12','bbox':[.3,.3,.4,.4]}]
        title={'title':'PLAN AT EL.107.500(TOS)','kind':'PLAN','elevation_m':107.5,
               'elevation_basis':'TOS','alignment':None,'evidence_id':'p:title'}
        pages=[{'id':'p','file':'drawing.pdf','page':1,'texts':texts,'titles':[title]}]
        result=build_inventory({'views':[],'observations':[]},pages)
        self.assertEqual(result['profile_count'],2)
        self.assertEqual(result['view_counts'],{'PLAN':1,'ELEVATION':0,'ISOMETRIC':0})
        self.assertFalse(result['views']['PLAN'][0]['region_identified'])
        self.assertEqual(result['profiles'][0]['profile'],'HEA260')
        self.assertEqual(len(result['profiles'][0]['evidence']),2)
        result=build_inventory({'views':[],'observations':[{'profile':'HEA999'}]},pages)
        self.assertEqual(result['profile_count'],2)
        self.assertEqual(result['unverified_profiles'],['HEA999'])


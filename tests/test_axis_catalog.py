import unittest
from app.axis_catalog import axis_name,build_axis_catalog
from app.member_recognition import make_input,validate_rows


class AxisCatalogTests(unittest.TestCase):
    def test_generic_names_exclude_steel_and_dimension_text(self):
        for name in ('WEST-FRAME/C','BUILDING-Q/12','GRID ALPHA','轴线 9'):
            self.assertIsNotNone(axis_name(name))
        for name in ('6000','A','HEA180/EPB','IPET270/LT','SCALE 1:100'):
            self.assertIsNone(axis_name(name))

    def test_counts_current_axes_without_requiring_elevation_upload(self):
        view={'id':'plan','page_id':'page','kind':'PLAN','title':'PLAN LEVEL 20',
              'bbox':[0,0,.5,1],'axis_marks':[{'label':'C','direction':'X','evidence_ids':['a']} ]}
        page={'id':'page','file':'arbitrary.pdf','page':1,'width_pt':1000,'height_pt':800,'vectors':[],
              'texts':[{'id':'a','text':'WEST-FRAME/C','bbox':[.1,.1,.2,.2]},
                       {'id':'b','text':'WEST-FRAME/12','bbox':[.1,.3,.2,.4]},
                       {'id':'c','text':'OTHER/9','bbox':[.7,.3,.8,.4]}]}
        catalog=build_axis_catalog([page],[view])
        self.assertEqual(catalog['axis_count'],2)
        self.assertEqual(catalog['plan_count'],1)
        self.assertEqual(catalog['elevation_view_count'],0)
        self.assertEqual(catalog['per_view']['plan'],['WEST-FRAME/12','WEST-FRAME/C'])
        one={'views':[view],'profile_catalog':['HEA200']}
        bundle=make_input(page,view,one)
        self.assertEqual(bundle['axis_catalog']['axis_count'],2)
        row={'spec':'HEA200','view_tags':['PLAN LEVEL 20','WEST-FRAME/12'],'member_cnt':1,
             'length_mm':6000,'basis':'轴线12上的梁，跨6000','issue':None}
        self.assertEqual(validate_rows([row],view,{**one,'axis_catalog':catalog})[0]['checks'],[])

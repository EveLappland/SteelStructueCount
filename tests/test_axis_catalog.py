import unittest
from app.axis_catalog import axis_name,build_axis_catalog
from app.member_recognition import make_input,validate_rows


class AxisCatalogTests(unittest.TestCase):
    def test_five_elevations_bind_their_own_axes_independently(self):
        views=[];texts=[]
        for index in range(5):
            x=index/5
            views.append({'id':str(index),'page_id':'p','kind':'ELEVATION','title':f'ELEVATION FRAME-{index}',
                          'fixed_label':f'FRAME-{index}','bbox':[x,0,x+.19,1]})
            texts.append({'id':f't{index}','text':f'GRID SEGMENT-{index}','bbox':[x+.02,.2,x+.1,.22]})
        result=build_axis_catalog([{'id':'p','texts':texts}],views)
        self.assertEqual(result['elevation_view_count'],5)
        for index in range(5):
            self.assertEqual(result['per_view'][str(index)],[f'FRAME-{index}',f'SEGMENT-{index}'])

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
        self.assertEqual(catalog['axis_count'],3)
        self.assertEqual(catalog['plan_count'],1)
        self.assertEqual(catalog['elevation_view_count'],0)
        self.assertEqual(catalog['per_view']['plan'],['PLAN LEVEL 20','WEST-FRAME/12','WEST-FRAME/C'])
        one={'views':[view],'profile_catalog':['HEA200']}
        bundle=make_input(page,view,one)
        self.assertEqual(bundle['axis_catalog']['axis_count'],3)
        row={'spec':'HEA200','view_tags':['PLAN LEVEL 20','WEST-FRAME/12'],'member_cnt':1,
             'length_mm':6000,'location':'轴线12上的梁','issue':None}
        self.assertEqual(validate_rows([row],view,{**one,'axis_catalog':catalog})[0]['checks'],[])

    def test_alignment_and_level_names_preserve_datum_without_counting_dimensions(self):
        self.assertEqual(axis_name('ELEVATION ON ALIGNMENT FRAME-Q/7'),'FRAME-Q/7')
        self.assertEqual(axis_name('PLAN AT EL.23.450(TOS)'),'EL.23.450(TOS)')
        self.assertEqual(axis_name('EL. -2.500 ( bob )'),'EL.-2.500(BOB)')
        self.assertNotEqual(axis_name('EL.23.450(TOS)'),axis_name('EL.23.450(BOB)'))
        for text in ('23.450','1500','TOS','ELEVATION ON ALIGNMENT A & B'):
            self.assertIsNone(axis_name(text))

    def test_elevation_binds_level_planes_even_when_plan_not_uploaded(self):
        view={'id':'frame','page_id':'p','kind':'ELEVATION','title':'ELEVATION ON ALIGNMENT FRAME-X',
              'fixed_label':'FRAME-X','bbox':[0,0,.5,1]}
        page={'id':'p','vectors':[],'texts':[
            {'id':'grid','text':'PROJECT-Z/C','bbox':[.1,.1,.2,.2]},
            {'id':'level','text':'EL.23.450(TOS)','bbox':[.1,.3,.2,.4]},
            {'id':'other','text':'EL.80.000(TOS)','bbox':[.6,.3,.7,.4]}]}
        catalog=build_axis_catalog([page],[view])
        self.assertEqual(catalog['per_view']['frame'],['EL.23.450(TOS)','FRAME-X','PROJECT-Z/C'])
        self.assertEqual(catalog['level_plane_count'],1)
        self.assertEqual(catalog['alignment_count'],2)
        self.assertEqual(catalog['plan_count'],0)
        level=next(a for a in catalog['axes'] if a['kind']=='PLAN')
        self.assertEqual(level['uploaded_view_ids'],[])
        one={'views':[view],'profile_catalog':['HEA200'],'axis_catalog':catalog}
        bundle=make_input(page,view,one)
        self.assertIn({'tag':'EL.23.450(TOS)','kind':'PLAN','alignment':None,'axes':[],'uploaded':False},bundle['view_catalog'])
        row={'spec':'HEA200','view_tags':['FRAME-X','EL.23.450(TOS)'],'member_cnt':1,
             'length_mm':6000,'location':'EL.23.450平面上的横梁','issue':None}
        self.assertEqual(validate_rows([row],view,one)[0]['checks'],[])
        wrong=validate_rows([{**row,'view_tags':['FRAME-X','EL.80.000(TOS)']}],view,one)[0]
        self.assertTrue(any('目录外' in c for c in wrong['checks']))

    def test_uploaded_plan_alias_and_manual_reference_names_remain_bound(self):
        elevation={'id':'e','page_id':'p','kind':'ELEVATION','title':'ELEVATION ON ALIGNMENT BUILDING/C',
                   'fixed_label':'BUILDING/C','bbox':[0,0,.5,1],
                   'axis_labels':['EL.23.450(TOS)'],'axes_reviewed':True}
        plan={'id':'plan','page_id':'p','kind':'PLAN','title':'PLAN AT EL.23.450(TOS)','bbox':[.5,0,1,1]}
        page={'id':'p','vectors':[],'texts':[{'id':'unreviewed','text':'EL.25.000(BOB)','bbox':[.1,.1,.2,.2]}]}
        catalog=build_axis_catalog([page],[elevation,plan])
        self.assertNotIn('EL.25.000(BOB)',catalog['per_view']['e'])
        level=next(a for a in catalog['axes'] if a['name']=='EL.23.450(TOS)')
        self.assertEqual(level['uploaded_view_ids'],['plan'])
        self.assertEqual(catalog['level_plane_count'],1)
        one={'views':[elevation,plan],'axis_catalog':catalog,'profile_catalog':['HEA200']}
        row={'spec':'HEA200','view_tags':['BUILDING/C','EL.23.450(TOS)'],'member_cnt':1,
             'length_mm':6000,'location':'EL.23.450横梁','issue':None}
        checked=validate_rows([row],elevation,one)[0]
        self.assertEqual(checked['checks'],[])
        self.assertIn('EL.23.450(TOS)',checked['view_tags'])
        self.assertNotIn(plan['title'],checked['view_tags'])

    def test_ai_plane_list_is_supplemented_by_native_names_in_its_crop(self):
        view={'id':'v','page_id':'p','kind':'PLAN','title':'PLAN AT EL.20.000(TOS)',
              'plane_name':'EL.20.000(TOS)','axis_labels':['FRAME/A'],
              'bbox':[0,0,.5,1]}
        page={'id':'p','texts':[
            {'id':'inside','text':'FRAME/2','bbox':[.1,.1,.2,.2]},
            {'id':'outside','text':'FRAME/3','bbox':[.7,.1,.8,.2]}]}
        catalog=build_axis_catalog([page],[view])
        self.assertEqual(catalog['per_view']['v'],
                         ['EL.20.000(TOS)','FRAME/2','FRAME/A'])

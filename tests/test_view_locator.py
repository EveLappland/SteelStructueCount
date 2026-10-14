import unittest
from app.view_locator import refine_view_regions, contains
from app.domain import View


def item(identity,text,box):return {'id':identity,'text':text,'bbox':box}


class ViewLocatorTest(unittest.TestCase):
    def fixture(self):
        titles=[{'title':'PLAN LEFT','kind':'PLAN','title_bbox':[.2,.8,.3,.83],'evidence_id':'title-left'},
                {'title':'PLAN RIGHT','kind':'PLAN','title_bbox':[.7,.8,.8,.83],'evidence_id':'title-right'}]
        page={'width_pt':1000,'height_pt':1000,'titles':titles,'vectors':[],
              'texts':[item('title-left','PLAN LEFT',[.2,.8,.3,.83]),item('title-right','PLAN RIGHT',[.7,.8,.8,.83]),
                       item('axis-left','PROJECT/1',[.175,.15,.195,.18]),
                       item('note-left','ALL HORIZONTAL BRACING BELOW TOS',[.175,.84,.4,.86]),
                       item('axis-right','PROJECT/1',[.675,.15,.695,.18]),
                       item('note-right','ALL HORIZONTAL BRACING BELOW TOS',[.675,.84,.9,.86]),
                       item('border','55',[.02,.2,.03,.22]),
                       item('reference','PLAN AT EL.100.000(BOB)',[.92,.3,.99,.32])]}
        views=[View(id='left',page_id='p',title='PLAN LEFT',kind='PLAN',bbox=[.2,.1,.4,.83],
                    title_bbox=titles[0]['title_bbox'],structure_bbox=[.2,.15,.4,.75]).model_dump(),
               View(id='right',page_id='p',title='PLAN RIGHT',kind='PLAN',bbox=[.7,.1,.9,.83],
                    title_bbox=titles[1]['title_bbox'],structure_bbox=[.7,.15,.9,.75]).model_dump()]
        return page,views

    def test_expands_annotation_and_axis_without_assigning_neighbour_evidence(self):
        page,views=self.fixture();result=refine_view_regions(page,views)
        self.assertTrue(contains(result[0]['bbox'],page['texts'][2]['bbox']))
        self.assertTrue(contains(result[0]['bbox'],page['texts'][3]['bbox']))
        self.assertNotIn('axis-right',result[0]['localization']['included_text_ids'])
        self.assertNotIn('border',result[0]['localization']['included_text_ids'])
        self.assertNotIn('reference',result[1]['localization']['included_text_ids'])
        self.assertEqual(result[0]['localization']['status'],'checked')
        self.assertEqual(views[0]['bbox'],[.2,.1,.4,.83])

    def test_neighbour_title_and_title_block_block_automatic_acceptance(self):
        page,views=self.fixture();views[0]['bbox']=[.1,.05,.95,.95]
        page['texts'].append(item('block','SHEET SIZE',[.9,.9,.94,.92]))
        result=refine_view_regions(page,views)
        self.assertFalse(result[0]['region_confirmed'])
        self.assertTrue(any('另一个' in issue for issue in result[0]['localization']['issues']))
        self.assertTrue(any('图签' in issue for issue in result[0]['localization']['issues']))

    def test_nonexistent_boundary_reference_stays_pending(self):
        page,views=self.fixture();views[0]['boundary_evidence_ids']=['invented']
        self.assertFalse(refine_view_regions(page,views)[0]['region_confirmed'])

    def test_neighbour_axis_citation_is_rejected_not_crop_failure(self):
        page,views=self.fixture()
        page['texts'].append(item('inside-right','PROJECT/5',[.72,.2,.75,.22]))
        views[0]['boundary_evidence_ids']=['inside-right']
        result=refine_view_regions(page,views)
        self.assertTrue(result[0]['region_confirmed'])
        self.assertEqual(result[0]['boundary_evidence_ids'],[])
        self.assertEqual(result[0]['localization']['rejected_boundary_evidence'][0]['owner_view'],'right')
        self.assertLess(result[0]['bbox'][2],.5)

    def test_unowned_outside_citation_still_blocks(self):
        page,views=self.fixture();views[0]['boundary_evidence_ids']=['border']
        self.assertFalse(refine_view_regions(page,views)[0]['region_confirmed'])

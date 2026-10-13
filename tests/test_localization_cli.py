import json
import tempfile
import unittest
from pathlib import Path
from tools.test_ai_view_localization import assess_view,load_reference


class GenericLocalizationCliTest(unittest.TestCase):
    def fixture(self):
        page={'titles':[{'title':'ELEVATION CUSTOM','title_bbox':[.2,.8,.4,.85]}],
              'texts':[{'id':'steel','text':'HEA260','bbox':[.25,.3,.35,.35]}]}
        view={'title':'ELEVATION CUSTOM','bbox':[.1,.1,.6,.9],'region_confirmed':True}
        return page,view

    def test_arbitrary_view_without_reference_is_not_a_two_view_test(self):
        page,view=self.fixture()
        result=assess_view(page,view)
        self.assertTrue(result['program_passed'])
        self.assertTrue(result['passed'])
        self.assertIsNone(result['reference_passed'])
        self.assertIsNone(result['expected_evidence_count'])
        self.assertIsNone(result['missing_evidence'])

    def test_reference_missing_label_or_unmatched_view_fails(self):
        page,view=self.fixture()
        reference={'views':[{'title':view['title'],'bbox':[0,0,1,1]}]}
        view['bbox']=[.4,.1,.6,.9]
        self.assertFalse(assess_view(page,view,reference)['reference_passed'])
        reference['views'][0]['title']='ANOTHER VIEW'
        self.assertFalse(assess_view(page,view,reference)['reference_passed'])

    def test_reference_file_and_page_must_match_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'reference.json'
            path.write_text(json.dumps({'file':'other.pdf','page':2,'views':[{'title':'CUSTOM','bbox':[0,0,1,1]}]}))
            self.assertIsNone(load_reference(None,Path('any.pdf'),1))
            with self.assertRaises(ValueError):load_reference(path,Path('other.pdf'),1)
            with self.assertRaises(ValueError):load_reference(path,Path('wrong.pdf'),2)
            self.assertEqual(load_reference(path,Path('other.pdf'),2)['page'],2)

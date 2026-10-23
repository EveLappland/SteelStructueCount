"""Unknown lengths and combination expansion in offline scoring, no API."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from app.entity_report import export
from tools.evaluate_entity_run import evaluate


class EntityEvaluationTests(unittest.TestCase):
    def test_unknown_is_unmatched_and_stays_in_denominator(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            baseline = directory/'reference.xlsx'
            book = Workbook()
            book.active.append(['PROFILE','LENGTH(mm)'])
            for spec, length in [('HEM420',1000),('HEM420',1000),('HEM420',2000),('L90*9',3000),('L90*9',3000)]:
                book.active.append([spec,length])
            book.save(baseline)
            entities = []
            for index, (spec, profile, length, count, multiplier) in enumerate([
                    ('HEM420','HEM420',1000,2,1), ('HEM420','HEM420',None,1,1), ('2LF-L90*9','L90*9',3000,1,2)]):
                entities.append({'id':str(index),'spec':spec,'profile':profile,'length_mm':length,
                    'member_cnt':count,'component_multiplier':multiplier,'view_tags':['LEVEL'],
                    'status':'UNKNOWN' if length is None else 'MODEL_DERIVED',
                    'method':'INDEPENDENT','observation_ids':[],'reason':'Local scoring fixture','measurement':None})
            result = directory/'result'
            result.mkdir()
            export(result, {'entities':entities,'cases':[],'views':[],'observation_entity_map':[],
                'summary':{},'conservation':{}}, {})
            # Export JSON is normally written by the workflow coordinator.
            import json
            (result/'stage_three.json').write_text(json.dumps({'summary':{}}), encoding='utf-8')
            score = evaluate(result, baseline)
            self.assertEqual((score['reference_count'],score['predicted_count'],score['matched']),(5,5,4))
            self.assertEqual(score['unknown_components'],1)
            self.assertEqual((score['precision'],score['recall'],score['f1']),(.8,.8,.8))
            self.assertEqual(score['extra'],[('HEM420',None)])


if __name__ == '__main__':
    unittest.main()

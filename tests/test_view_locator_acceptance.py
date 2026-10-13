"""Native completeness checks for saved LIVE results; no model call in unit tests."""
import json
import unittest
from pathlib import Path
from app.view_locator import refine_view_regions,contains,intersects,evidence_kind


class SavedLocalizationAcceptanceTest(unittest.TestCase):
    def test_saved_live_proposals_cover_reference_annotations(self):
        root=Path(__file__).resolve().parents[1]
        folder=root/'tmp/ai_view_localization'
        if not (folder/'ai_proposals.json').exists():self.skipTest('先运行真实AI定位验收工具。')
        page=json.loads((folder/'native.json').read_text(encoding='utf-8'))
        proposals=json.loads((folder/'ai_proposals.json').read_text(encoding='utf-8'))
        reference=json.loads((root/'tests/fixtures/plan_02_views.json').read_text(encoding='utf-8'))
        views=refine_view_regions(page,proposals)
        self.assertEqual(len(views),2)
        for view in views:
            expected=next(r for r in reference['views'] if r['title']==view['title'])
            for text in page['texts']:
                if contains(expected['bbox'],text['bbox']) and evidence_kind(text['text']):
                    self.assertTrue(contains(view['bbox'],text['bbox']),(view['title'],text['text']))
                if text['text'].strip().upper() in ('REFERENCE DOCUMENTS','SHEET SIZE','DRAWING TITLE'):
                    self.assertFalse(intersects(view['bbox'],text['bbox']))
            self.assertTrue(view['region_confirmed'],view['localization'])

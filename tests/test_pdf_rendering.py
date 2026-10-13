import os
import json
import tempfile
import unittest
from pathlib import Path
import pymupdf as fitz
from app.drawing_input import page_clip, render_jpeg, render_png, render_data_uri

REFERENCE=json.loads((Path(__file__).parent/'fixtures'/'plan_02_views.json').read_text(encoding='utf-8'))
SAMPLE_NAME=REFERENCE['file']
REGIONS=[(v['name'],v['bbox'],v['title']) for v in REFERENCE['views']]


class PdfRenderingTest(unittest.TestCase):
    def test_normalized_crop_renders_the_correct_half_without_resizing(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'two_views.pdf'
            with fitz.open() as pdf:
                page=pdf.new_page(width=400,height=200)
                page.draw_rect(fitz.Rect(0,0,200,200),fill=(1,0,0),color=None)
                page.draw_rect(fitz.Rect(200,0,400,200),fill=(0,0,1),color=None)
                pdf.save(source)
            for crop,color in [([0,0,.5,1],(255,0,0)),([.5,0,1,1],(0,0,255))]:
                entry={'source_path':str(source),'page':1,'crop':crop}
                pix=fitz.Pixmap(render_png(entry,dpi=144))
                self.assertEqual((pix.width,pix.height),(400,400))
                self.assertEqual(pix.pixel(200,200),color)
                self.assertEqual((pix.xres,pix.yres),(144,144))
                self.assertTrue(render_data_uri(entry).startswith('data:image/jpeg;base64,'))
            for bad in ([.5,0,.1,1],[-.1,0,1,1],[0,0,float('nan'),1],[0,0,1]):
                with self.assertRaises(ValueError):render_png({'source_path':str(source),'page':1,'crop':bad})

    def test_real_sample_two_complete_high_resolution_views(self):
        directory=Path(os.getenv('LLM_SAMPLE_DIR',str(Path.home()/'Desktop/TeklaProgram/建模图纸资料/建模图纸资料')))
        source=directory/SAMPLE_NAME
        if not source.exists():self.skipTest('设置 LLM_SAMPLE_DIR 后运行真实 PDF 渲染测试。')
        with fitz.open(source) as pdf:
            page=pdf[0]
            for name,crop,title in REGIONS:
                with self.subTest(view=name):
                    clip=page_clip(page,crop)
                    text=page.get_text('text',clip=clip)
                    self.assertIn(title,text)
                    other=REGIONS[1 if name==REGIONS[0][0] else 0][2]
                    self.assertNotIn(other,text)
                    self.assertNotIn('REFERENCE DOCUMENTS',text)
                    self.assertNotIn('SHEET SIZE',text)
                    for axis in ('302003-PR001I/A','302003-PR001I/B','302003-PR001I/1','302003-PR001I/5'):
                        self.assertIn(axis,text)
                    self.assertIn('OF SPLICE',text)
                    self.assertIn('100mm BELOW TOS',text)
                    image=render_png({'source_path':str(source),'page':1,'crop':crop},dpi=300)
                    pix=fitz.Pixmap(image)
                    self.assertGreater(pix.height,5000)
                    self.assertGreater(pix.width,2000)
                    self.assertLessEqual(abs(pix.width-clip.width*300/72),2)
                    self.assertLessEqual(abs(pix.height-clip.height*300/72),2)
                    self.assertEqual((pix.xres,pix.yres),(300,300))
                    preview=fitz.Pixmap(render_jpeg({'source_path':str(source),'page':1,'crop':crop},max_dimension=2600))
                    self.assertLessEqual(max(preview.width,preview.height),2602)

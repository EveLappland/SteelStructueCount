import base64
import tempfile
import unittest
from pathlib import Path
import pymupdf as fitz
from app.drawing_input import render_data_uri
from app.image_workflow import freeze_images,detail_images
from app.native_pdf import write_json


class FrozenImageTests(unittest.TestCase):
    def test_approved_image_bytes_are_sent_and_crop_edits_create_new_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);document=fitz.open();page=document.new_page(width=300,height=200)
            page.draw_rect(fitz.Rect(30,30,200,140));document.save(root/'drawing.pdf');document.close()
            write_json(root/'pdf_manifest.json',[{'id':'page','source_path':str(root/'drawing.pdf'),'page':1}])
            view={'id':'view','page_id':'page','bbox':[0,0,1,1]}
            data={'views':[view]};freeze_images(root,data)
            first=view['image_file'];original=(root/'views'/first).read_bytes()
            pix=fitz.Pixmap(original);self.assertEqual((pix.xres,pix.yres),(300,300))
            payload=render_data_uri({'image_path':str(root/'views'/first),'image_dpi':300})
            self.assertEqual(base64.b64decode(payload.split(',',1)[1]),original)
            import json
            from unittest.mock import Mock
            from app.pipeline import model_call
            from app.member_recognition import MemberResponse
            client=Mock();client.base_url='https://example.test'
            response=Mock();response.model_dump_json.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"members":[]}'}}]})
            client.chat.completions.create.return_value=response
            model_call(client,'test','只识图',{'id':'view','source_path':str(root/'drawing.pdf'),'page':1,
                       'image_path':str(root/'views'/first),'detail_paths':[str(root/'views'/first)],'image_dpi':300,
                       'allowed_view_tags':['PLAN TEST','PROJECT-Q/12']},
                       root/'responses/attempt-test/result.json',MemberResponse)
            images=[b['image_url']['url'] for b in client.chat.completions.create.call_args.kwargs['messages'][1]['content'] if b['type']=='image_url']
            self.assertEqual(len(images),2)
            self.assertTrue(all(base64.b64decode(url.split(',',1)[1])==original for url in images))
            texts=[b['text'] for b in client.chat.completions.create.call_args.kwargs['messages'][1]['content'] if b['type']=='text']
            schema=json.loads(texts[-1].split('JSON schema:\n',1)[1])
            self.assertEqual(schema['$defs']['RecognizedMember']['properties']['view_tags']['items']['enum'],['PLAN TEST','PROJECT-Q/12'])
            freeze_images(root,data);self.assertEqual(first,view['image_file'])
            view['bbox']=[0,0,.5,1];freeze_images(root,data)
            self.assertNotEqual(first,view['image_file'])
            self.assertEqual((root/'views'/first).read_bytes(),original)
            self.assertEqual(view['image_bbox'],view['bbox'])

    def test_non_png_asset_is_not_sent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fake.png';path.write_text('not a PNG',encoding='utf-8')
            with self.assertRaises(ValueError):render_data_uri({'image_path':str(path)})

    def test_overlapping_detail_bands_keep_original_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'views').mkdir()
            pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,200,4500),False)
            pix.clear_with(255);pix.set_rect(fitz.IRect(0,0,200,2000),(255,0,0));pix.set_dpi(300,300)
            (root/'views/full_300dpi.png').write_bytes(pix.tobytes('png'))
            files=detail_images(root,'full_300dpi.png')
            self.assertEqual(len(files),3)
            second=fitz.Pixmap(str(root/'views'/files[1]))
            self.assertEqual(second.pixel(0,0),pix.pixel(0,1760))
            self.assertEqual(second.pixel(0,300),pix.pixel(0,2060))
            self.assertEqual((second.xres,second.yres),(300,300))

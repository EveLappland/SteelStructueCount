from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pymupdf as fitz
from PIL import Image, ImageChops

from app.drawing_input import render_png
from app.label_colors import colorize_view


def check_colorized_view(tmp_path):
    source = tmp_path / 'drawing.pdf'
    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    page.insert_text((30, 40), 'HEA240 /EPW', fontsize=14)
    page.insert_text((30, 85), 'EL.119.000(TOS)', fontsize=14)
    page.insert_text((30, 130), 'N8', fontsize=14)
    page.insert_text((30, 175), '4000', fontsize=14)
    page.insert_text((300, 95), '2LF-L120*12', fontsize=14, rotate=90)
    page.draw_line((20, 190), (550, 190))
    doc.save(source)
    doc.close()
    entry = {'source_path': str(source), 'page': 1, 'crop': [0, 0, 1, 1]}
    original = render_png(entry, dpi=300)
    colored, audit = colorize_view(entry, original)
    before = Image.open(BytesIO(original)).convert('RGB')
    after = Image.open(BytesIO(colored)).convert('RGB')
    assert before.size == after.size
    assert [(item['text'], item['kind']) for item in audit] == [
        ('HEA240 /EPW', 'steel'), ('EL.119.000(TOS)', 'axis'),
        ('2LF-L120*12', 'steel')]
    assert abs(audit[-1]['direction'][1]) > 0.5
    changed = ImageChops.difference(before, after)
    assert changed.getbbox()
    # The note, dimension and line are far from target glyph rectangles.
    assert changed.crop((0, 480, before.width, before.height)).getbbox() is None


class LabelColorTests(unittest.TestCase):
    def test_native_names_and_rotated_steel(self):
        with TemporaryDirectory() as directory:
            check_colorized_view(Path(directory))

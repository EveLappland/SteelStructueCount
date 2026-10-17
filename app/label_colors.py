"""Color PDF text glyphs in a frozen view image without changing the PDF.

Native text positions, including rotated text, select the pixels. Geometry,
dimensions, callout marks and the original image remain available unchanged.
"""
from __future__ import annotations

from io import BytesIO
import re

import pymupdf as fitz
from PIL import Image

from .axis_catalog import axis_name
from .discovery_inventory import PROFILE
from .drawing_input import page_clip


STEEL_COLOR = (192, 38, 211)
AXIS_COLOR = (0, 109, 192)
COLORIZER_VERSION = 'v2'
_TITLE_PREFIX = re.compile(r'^(?:ELEVATION\s+ON\s+ALIGNMENT\s+|PLAN\s+AT\s+)', re.I)


def _targets(line_text):
    """Return only character ranges belonging to steel or reference names."""
    steel = []
    for match in PROFILE.finditer(line_text):
        # Connection suffixes such as /EPW and /LT belong to the visible
        # specification label; they are not independent member names.
        suffix = re.match(r'(?:\s*/[A-Za-z0-9]+)*', line_text[match.end():])
        steel.append((match.start(), match.end() + len(suffix[0]), STEEL_COLOR))
    if steel:
        return steel
    if axis_name(line_text):
        prefix = _TITLE_PREFIX.match(line_text)
        start = prefix.end() if prefix else 0
        return [(start, len(line_text), AXIS_COLOR)]
    return []


def colorize_view(entry, original_png):
    """Return (colored PNG, audit) for one 300-DPI PDF crop.

    Each native character has its own rectangle, so the method also handles
    diagonal labels. Dark raster pixels inside the glyph rectangles are tinted
    with their existing antialiasing. A line crossing a glyph can share a few
    pixels; this is recorded as a limitation, never interpreted as a member.
    """
    with fitz.open(entry['source_path']) as document:
        page = document[entry['page'] - 1]
        clip = page_clip(page, entry['crop'])
        image = Image.open(BytesIO(original_png)).convert('RGB')
        sx, sy = image.width / clip.width, image.height / clip.height
        pixels = image.load()
        audit = []
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines', []):
                chars = [char for span in line['spans'] for char in span.get('chars', [])]
                raw = ''.join(char['c'] for char in chars)
                stripped = raw.strip()
                if not stripped or not clip.contains(fitz.Point(
                    (line['bbox'][0] + line['bbox'][2]) / 2,
                    (line['bbox'][1] + line['bbox'][3]) / 2)):
                    continue
                leading = len(raw) - len(raw.lstrip())
                for start, end, color in _targets(stripped):
                    touched = 0
                    for char in chars[leading + start:leading + end]:
                        if char['c'].isspace():
                            continue
                        x0, y0, x1, y1 = char['bbox']
                        left = max(0, int((x0 - clip.x0) * sx))
                        top = max(0, int((y0 - clip.y0) * sy))
                        right = min(image.width, int((x1 - clip.x0) * sx + 1))
                        bottom = min(image.height, int((y1 - clip.y0) * sy + 1))
                        for y in range(top, bottom):
                            for x in range(left, right):
                                old = pixels[x, y]
                                # White space and non-grey colored annotations
                                # must not become part of the highlighted text.
                                if max(old) - min(old) > 24:
                                    continue
                                darkness = 1 - sum(old) / (3 * 255)
                                if darkness < 0.12:
                                    continue
                                pixels[x, y] = tuple(round(255 - darkness * (255 - c)) for c in color)
                                touched += 1
                    audit.append({'text': stripped[start:end],
                                  'kind': 'steel' if color == STEEL_COLOR else 'axis',
                                  'direction': list(line['dir']), 'changed_pixels': touched})
        output = BytesIO()
        image.save(output, format='PNG', dpi=(300, 300))
        return output.getvalue(), audit

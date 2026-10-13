"""Native PDF evidence. Drawing paths are evidence primitives, never piece counts."""
import json
import re
from pathlib import Path

import pymupdf as fitz

from .domain import Evidence


def normal_box(box, width, height):
    return [box[0] / width, box[1] / height, box[2] / width, box[3] / height]


def discover_titles(texts):
    titles = []
    for item in texts:
        text = item['text'].strip()
        plan = re.fullmatch(r'PLAN AT EL\.\s*([+-]?\d+(?:\.\d+)?)\s*\(([^)]+)\)', text, re.I)
        elevation = re.fullmatch(r'ELEVATION ON ALIGNMENT\s+([^&]+)', text, re.I)
        if plan:
            kind, level, basis, alignment = 'PLAN', float(plan[1]), plan[2].upper(), None
        elif elevation and not re.search(r'\bTO\b|,', elevation[1], re.I):
            kind, level, basis, alignment = 'ELEVATION', None, '', elevation[1].strip()
        elif text.upper() == 'ISOMETRIC VIEW':
            kind, level, basis, alignment = 'ISOMETRIC', None, '', None
        else:
            continue
        titles.append({'title': text, 'kind': kind, 'title_bbox': item['bbox'],
                       'elevation_m': level, 'elevation_basis': basis,
                       'alignment': alignment, 'evidence_id': item['id']})
    return titles


def extract_page(entry):
    with fitz.open(entry['source_path']) as document:
        page = document[entry['page'] - 1]
        width, height = page.rect.width, page.rect.height
        texts, vectors = [], []
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                text = ''.join(span['text'] for span in line['spans']).strip()
                if not text:
                    continue
                texts.append(Evidence(id=f"{entry['id']}:t{len(texts)}", page_id=entry['id'], kind='TEXT',
                    bbox=normal_box(line['bbox'], width, height), text=text,
                    style={'direction': list(line['dir']), 'font_size': max(s['size'] for s in line['spans'])}
                ).model_dump())
        for path_index, path in enumerate(page.get_drawings()):
            for primitive_index, item in enumerate(path['items']):
                segments = []
                if item[0] == 'l':
                    segments = [(item[1], item[2])]
                elif item[0] == 're':
                    r = item[1]
                    segments = [(r.tl, r.tr), (r.tr, r.br), (r.br, r.bl), (r.bl, r.tl)]
                elif item[0] == 'qu':
                    q = item[1]
                    segments = [(q.ul, q.ur), (q.ur, q.lr), (q.lr, q.ll), (q.ll, q.ul)]
                for segment_index, (a, b) in enumerate(segments):
                    points = [[a.x / width, a.y / height], [b.x / width, b.y / height]]
                    vectors.append(Evidence(id=f"{entry['id']}:v{path_index}.{primitive_index}.{segment_index}",
                        page_id=entry['id'], kind='VECTOR', points=points,
                        bbox=[min(a.x,b.x)/width,min(a.y,b.y)/height,max(a.x,b.x)/width,max(a.y,b.y)/height],
                        style={'width': path.get('width'), 'dashes': path.get('dashes'),
                               'color': path.get('color'), 'path_id': path_index}
                    ).model_dump())
        if not texts or not vectors:
            raise ValueError(f"{entry['file']} 第 {entry['page']} 页不含可提取文字和矢量，首版不支持扫描件。")
        return {'id': entry['id'], 'file': entry['file'], 'page': entry['page'],
                'width_pt': width, 'height_pt': height, 'texts': texts, 'vectors': vectors,
                'titles': discover_titles(texts)}


def inside(item, bbox):
    box = item['bbox']
    return bbox[0] <= (box[0]+box[2])/2 <= bbox[2] and bbox[1] <= (box[1]+box[3])/2 <= bbox[3]


def view_context(page, bbox, max_vectors=1600):
    texts = [item for item in page['texts'] if inside(item, bbox)]
    vectors = [item for item in page['vectors'] if inside(item, bbox)]
    # Prefer meaningful long primitives, retain all originals in native/*.json.
    vectors.sort(key=lambda item: -sum((item['points'][1][k]-item['points'][0][k])**2 for k in (0,1)))
    # Compact model context; full precision/styles remain in the native evidence files.
    return {'texts': [{'id':t['id'],'text':t['text'],'bbox':[round(v,6) for v in t['bbox']]} for t in texts],
            'vector_columns':['id','x1','y1','x2','y2','width_pt','dashes'],
            'vectors': [[v['id'],*[round(n,6) for point in v['points'] for n in point],
                         v['style'].get('width'),v['style'].get('dashes')] for v in vectors[:max_vectors]],
            'vector_total': len(vectors), 'vector_context_truncated': len(vectors) > max_vectors}


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def evidence_index(pages, manual=()):
    result = {item['id']: item for page in pages for item in page['texts'] + page['vectors']}
    for item in manual:
        if item['id'] in result:
            raise ValueError('人工证据 ID 不能覆盖原始 PDF 证据。')
        result[item['id']] = item
    return result

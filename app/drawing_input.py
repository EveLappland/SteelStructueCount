import base64
import math
from collections import defaultdict
from pathlib import Path

import pymupdf as fitz


def pdf_manifest(files):
    entries = []
    for file_index, path in enumerate(files):
        try:
            with fitz.open(path) as document:
                if document.needs_pass or not len(document):
                    raise ValueError('PDF 已加密或为空')
                for page_index, page in enumerate(document):
                    entries.append({
                        'id': f'f{file_index:03d}-p{page_index + 1:03d}',
                        'file': path.name,
                        'page': page_index + 1,
                        'pages': len(document),
                        'source_path': str(path),
                        'text': page.get_text('text'),
                    })
        except Exception as exc:
            raise ValueError(f'无法读取 PDF {path.name}: {exc}') from exc
    return entries


def is_primary_sheet(entry):
    name = entry['file'].lower()
    text = entry['text'].upper()
    if entry['pages'] > 12 or 'ALBUM' in name:
        return False
    return any(token in text for token in ('PLAN', 'ELEVATION', 'STEEL', 'HEA', 'IPE', 'UB ')) or 'SA-' in name


def render_data_uri(entry, max_dimension=None):
    if entry.get('prepared_data_uri'):
        return entry['prepared_data_uri']
    if entry.get('image_path'):
        data=Path(entry['image_path']).read_bytes()
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('已确认视图图片必须是PNG')
        return 'data:image/png;base64,'+base64.b64encode(data).decode('ascii')
    if entry.get('image_dpi'):
        return 'data:image/png;base64,' + base64.b64encode(render_png(entry,dpi=entry['image_dpi'])).decode('ascii')
    image_bytes = render_jpeg(entry, max_dimension=max_dimension)
    encoded = base64.b64encode(image_bytes).decode('ascii')
    return f'data:image/jpeg;base64,{encoded}'


def page_clip(page,crop):
    if crop is None:return page.rect
    if not isinstance(crop,(list,tuple)) or len(crop)!=4:
        raise ValueError('裁切区域必须为四个归一化坐标。')
    try:x0,y0,x1,y1=map(float,crop)
    except (TypeError,ValueError):raise ValueError('裁切坐标必须是数字。')
    if not all(math.isfinite(v) for v in (x0,y0,x1,y1)) or not (0<=x0<x1<=1 and 0<=y0<y1<=1):
        raise ValueError('裁切区域必须位于页面内，且具有正宽度和高度。')
    return fitz.Rect(page.rect.x0+x0*page.rect.width,page.rect.y0+y0*page.rect.height,
                     page.rect.x0+x1*page.rect.width,page.rect.y0+y1*page.rect.height)


def render_png(entry,dpi=300):
    """Lossless PDF rendering at an explicit resolution, without preview caps."""
    if not isinstance(dpi,(int,float)) or not math.isfinite(dpi) or not 72<=dpi<=600:
        raise ValueError('渲染 DPI 必须在 72 到 600 之间。')
    with fitz.open(entry['source_path']) as document:
        page=document[entry['page']-1]
        pixmap=page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),
                               clip=page_clip(page,entry.get('crop')),alpha=False)
        pixmap.set_dpi(round(dpi),round(dpi))
        return pixmap.tobytes('png')


def render_jpeg(entry, max_dimension=None):
    with fitz.open(entry['source_path']) as document:
        page = document[entry['page'] - 1]
        crop = entry.get('crop')
        clip = page_clip(page,crop)
        area = clip or page.rect
        target = max_dimension or entry.get('max_dimension') or (2000 if crop else 1500)
        scale = min(4.0 if crop else 2.0, target / max(area.width, area.height))
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        return pixmap.tobytes('jpeg', jpg_quality=86)


def write_view_image(entry, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(render_jpeg(entry, max_dimension=2600))
    return target


def prepare_images(entries, max_dimension=None):
    prepared = []
    for entry in entries:
        item = dict(entry)
        item['data_uri'] = render_data_uri(item, max_dimension=max_dimension)
        prepared.append(item)
    return prepared


def _intersection_over_union(left, right):
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if not intersection:
        return 0.0
    union = ((left[2] - left[0]) * (left[3] - left[1]) +
             (right[2] - right[0]) * (right[3] - right[1]) - intersection)
    return intersection / union if union else 0.0


def crop_entries(pages, regions, *, prefix, max_per_page=4, max_total=24):
    """Keep only distinct, complete plan/elevation frames selected by the model."""
    pages_by_id = {entry['id']: entry for entry in pages}
    counts = defaultdict(int)
    result = []
    for region in regions if isinstance(regions, list) else []:
        if not isinstance(region, dict):
            continue
        page = pages_by_id.get(str(region.get('page_id', '')))
        bbox = region.get('bbox')
        title_bbox = region.get('title_bbox')
        view_type = str(region.get('view_type', '')).upper()
        if not page or view_type not in {'PLAN', 'ELEVATION', 'SUPPORTING_ISOMETRIC'} or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            x0, y0, x1, y1 = (max(0.0, min(1.0, float(value))) for value in bbox)
        except (TypeError, ValueError):
            continue
        if isinstance(title_bbox, list) and len(title_bbox) == 4:
            try:
                tx0, ty0, tx1, ty1 = (max(0.0, min(1.0, float(value))) for value in title_bbox)
                if tx1 > tx0 and ty1 > ty0:
                    x0, y0, x1, y1 = min(x0, tx0), min(y0, ty0), max(x1, tx1), max(y1, ty1)
            except (TypeError, ValueError):
                pass
        # A primary framing view cannot be a small local callout or node detail.
        if x1 - x0 < 0.14 or y1 - y0 < 0.14 or counts[page['id']] >= max_per_page:
            continue
        # title_bbox already expands the view to include its printed title. Keep only
        # a hairline safety border so neighbouring elevations are never merged.
        crop = [max(0.0, x0 - 0.005), max(0.0, y0 - 0.005),
                min(1.0, x1 + 0.005), min(1.0, y1 + 0.01)]
        if any(existing['parent_id'] == page['id'] and
               _intersection_over_union(crop, existing['crop']) >= 0.65 for existing in result):
            continue
        label = str(region.get('label') or '').strip()
        blocked_labels = ('DETAIL', 'SECTION', 'BASE PLATE')
        if (not label or any(token in label.upper() for token in blocked_labels) or
                (view_type != 'SUPPORTING_ISOMETRIC' and 'ISOMETRIC' in label.upper())):
            continue
        item = dict(page)
        item.update({
            'id': f"{page['id']}-{prefix}{counts[page['id']] + 1:02d}",
            'parent_id': page['id'],
            'crop': crop,
            'label': label[:120],
            'view_type': view_type,
            'reason': str(region.get('reason') or '')[:300],
            'max_dimension': 2000,
        })
        result.append(item)
        counts[page['id']] += 1
        if len(result) >= max_total:
            break
    return result


def fallback_crop_entries(pages, *, prefix, max_total=24):
    """Use overlapping quadrants when the model cannot produce a usable crop plan."""
    regions = []
    boxes = ([0.0, 0.0, 0.56, 0.56], [0.44, 0.0, 1.0, 0.56],
             [0.0, 0.44, 0.56, 1.0], [0.44, 0.44, 1.0, 1.0])
    for page in pages:
        for index, box in enumerate(boxes, 1):
            regions.append({'page_id': page['id'], 'bbox': box,
                            'label': f'coverage quadrant {index}',
                            'reason': 'automatic high-resolution coverage fallback'})
    return crop_entries(pages, regions, prefix=prefix, max_total=max(max_total, len(pages) * len(boxes)))


def compact_manifest(entries):
    result = []
    for entry in entries:
        if entry['pages'] > 12 and entry['page'] > 8:
            continue
        result.append({
            'id': entry['id'],
            'file': entry['file'],
            'page': entry['page'],
            'pages': entry['pages'],
            'text': entry['text'][:3200],
        })
    return result

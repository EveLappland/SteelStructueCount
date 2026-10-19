"""PDF manifests and exact PDF/PNG rendering."""
import base64
import math
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

"""Stage one freezes images; stage two interprets them with view-scoped evidence."""
import json
import uuid
import pymupdf as fitz
from pathlib import Path

from .drawing_input import render_png,write_view_image
from .label_colors import COLORIZER_VERSION,colorize_view
from .native_pdf import write_json
from .view_filter import role


def detail_images(directory,filename):
    """Lossless overlapping bands for long views; all belong to the same view."""
    pix=fitz.Pixmap(str(directory/'views'/filename));width,height=pix.width,pix.height
    if max(width,height)<=2200:return []
    horizontal=width>height;length=width if horizontal else height
    band=max(2000,(length+6*240+6)//7);step=band-240;files=[]
    for index,start in enumerate(range(0,length,step),1):
        end=min(start+band,length)
        if start and end-start<240:break
        box=fitz.IRect(start,0,end,height) if horizontal else fitz.IRect(0,start,width,end)
        clip=fitz.Pixmap(pix.colorspace,box,pix.alpha);clip.copy(pix,box);clip.set_origin(0,0);clip.set_dpi(300,300)
        name=filename.replace('_300dpi.png',f'_detail{index:02d}_300dpi.png')
        (directory/'views'/name).write_bytes(clip.tobytes('png'));files.append(name)
        if end==length:break
    return files


def freeze_images(directory,data):
    entries={e['id']:e for e in json.loads((directory/'pdf_manifest.json').read_text(encoding='utf-8'))}
    for view in data['views']:
        if role(view)!='MEMBERS':continue
        filename=view.get('image_file','')
        if Path(filename).name!=filename:view['image_file']='';filename=''
        current=directory/'views'/filename
        fresh=view.get('image_file') and view.get('image_bbox')==view['bbox'] and current.is_file()
        if fresh:
            if not view.get('detail_files') and view.get('kind')!='ISOMETRIC':view['detail_files']=detail_images(directory,filename)
        else:
            entry={**entries[view['page_id']],'crop':view['bbox']}
            filename=f"asset-{uuid.uuid4().hex}-{view['id']}_300dpi.png"
            image=render_png(entry,dpi=300)
            (directory/'views').mkdir(exist_ok=True)
            (directory/'views'/filename).write_bytes(image)
            view['image_file']=filename;view['image_bbox']=list(view['bbox'])
            if view.get('quantity_reference_image'):
                view['quantity_reference']=None;view['quantity_reference_image']=''
            view['detail_files']=detail_images(directory,filename) if view.get('kind')!='ISOMETRIC' else []
            write_view_image(entry,directory/'views'/f"{view['id']}.jpg")
        colored_name=view.get('colorized_image_file','')
        colored_path=directory/'views'/Path(colored_name).name
        if fresh and colored_name.endswith(f'_labels_{COLORIZER_VERSION}_300dpi.png') and colored_path.is_file():continue
        entry={**entries[view['page_id']],'crop':view['bbox']}
        colored,audit=colorize_view(entry,(directory/'views'/filename).read_bytes())
        stem=filename[:-len('_300dpi.png')] if filename.endswith('_300dpi.png') else Path(filename).stem
        colored_name=f'{stem}_labels_{COLORIZER_VERSION}_300dpi.png'
        (directory/'views'/colored_name).write_bytes(colored)
        view['colorized_image_file']=colored_name
        view['colorized_detail_files']=detail_images(directory,colored_name) if view.get('kind')!='ISOMETRIC' else []
        audit_name=colored_name.replace('.png','_audit.json')
        write_json(directory/'views'/audit_name,audit)
        view['colorized_label_audit_file']=audit_name


def prepare_catalog(directory,settings,emit,audit,*,reuse_saved=False):
    from .stage_one import prepare
    return prepare(directory,settings,emit,audit,reuse_saved=reuse_saved)


def recognize_views(directory,one,settings,emit,audit,*,reuse_saved=False):
    from .grid_workflow import recognize_views as recognize_grid_views
    return recognize_grid_views(directory,one,settings,emit,audit,reuse_saved=reuse_saved)

"""Stage one freezes images; stage two interprets them with view-scoped evidence."""
import hashlib
import json
import struct
import uuid
import pymupdf as fitz
from pathlib import Path
from collections import Counter

from .axis_catalog import build_axis_catalog
from .domain import StageTwo
from .drawing_input import render_png,write_view_image
from .llm_runner import _api_settings
from .member_recognition import make_input,recognition_prompt,MemberResponse,validate_rows,validate_ledger,allowed_model_tags
from .native_pdf import write_json,inside
from .discovery_inventory import labels
from .geometry import section


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
        filename=view.get('image_file','')
        if Path(filename).name!=filename:view['image_file']='';filename=''
        current=directory/'views'/filename
        if view.get('image_file') and view.get('image_bbox')==view['bbox'] and current.is_file():
            if not view.get('detail_files') and view.get('kind')!='ISOMETRIC':view['detail_files']=detail_images(directory,filename)
            continue
        entry={**entries[view['page_id']],'crop':view['bbox']}
        filename=f"asset-{uuid.uuid4().hex}-{view['id']}_300dpi.png"
        image=render_png(entry,dpi=300)
        (directory/'views').mkdir(exist_ok=True)
        (directory/'views'/filename).write_bytes(image)
        view['image_file']=filename;view['image_bbox']=list(view['bbox'])
        view['detail_files']=detail_images(directory,filename) if view.get('kind')!='ISOMETRIC' else []
        write_view_image(entry,directory/'views'/f"{view['id']}.jpg")


def prepare_catalog(directory,settings,emit,audit,*,reuse_saved=False):
    from .pipeline import catalog_only,discover
    seed=catalog_only(directory,emit,audit)
    # Archived coordinate drafts remain supported for legacy imports/tests.
    if seed.get('observations'):return seed
    emit('第一阶段：定位独立视图，准备高清裁切和逐视图轴线目录。')
    result=discover(directory,settings,emit,reuse_saved=reuse_saved,audit=audit,seed=seed,regions_only=True)
    freeze_images(directory,result)
    audit.event('VIEW_CATALOG_PREPARED',view_count=len(result['views']),axis_count=result['axis_catalog']['axis_count'],
                reason='第一阶段只定位视图和轴线，不调用构件识别；确认后冻结图片与目录供第二阶段使用。')
    return result


def recognize_views(directory,one,settings,emit,audit,*,reuse_saved=False):
    from .pipeline import load_native,model_call,OpenAI
    base,key,model=_api_settings(settings)
    client=OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1)
    pages={p['id']:p for p in load_native(directory)}
    entries={e['id']:e for e in json.loads((directory/'pdf_manifest.json').read_text(encoding='utf-8'))}
    folder=directory/'responses'/f'attempt-images-{uuid.uuid4().hex}'
    result=StageTwo(**{**one,'analysis_pending':False},recognition_mode='view_ledger').model_dump()
    result['warnings']=[]
    for view in one['views']:
        if view['kind']=='ISOMETRIC':continue
        status={'view_id':view['id'],'status':'pending','member_cnt':0}
        result['view_analysis'].append(status)
        if not view.get('region_confirmed'):
            status['status']='region_pending';result['warnings'].append(f"{view['title']} 第一阶段裁切尚未确认，未识别构件。")
            continue
        image_path=directory/'views'/Path(view.get('image_file','')).name
        if not view.get('image_file') or not image_path.is_file() or view.get('image_bbox')!=view['bbox']:
            raise ValueError(f"{view['title']} 缺少与已确认区域一致的高清图片，请返回第一阶段修正。")
        bundle=make_input(pages[view['page_id']],view,one)
        image=image_path.read_bytes();width,height=struct.unpack('>II',image[16:24])
        bundle['image']={'width_px':width,'height_px':height,'dpi':300,'format':'PNG'}
        input_path=folder/f"{view['id']}_input.json";write_json(input_path,bundle)
        context=bundle['native_evidence']
        emit(f"第二阶段识图：{view['title']}；使用确认图片、规格/视图目录和当前视图文字/矢量证据。")
        audit.event('IMAGE_MEMBER_REQUEST',view_id=view['id'],image_file=view['image_file'],
                    image_sha256=hashlib.sha256(image).hexdigest(),width_px=width,height_px=height,dpi=300,
                    detail_files=view.get('detail_files',[]),
                    input_file=str(input_path.relative_to(directory)),native_text_sent=bool(context['texts']),vectors_sent=bool(context['vectors']),
                    text_count=len(context['texts']),vector_count=len(context['vectors']),vector_total=context['vector_total'],
                    vector_context_truncated=context['vector_context_truncated'])
        if context['vector_context_truncated']:
            result['warnings'].append(f"{view['title']} 矢量辅助证据只发送了 {len(context['vectors'])}/{context['vector_total']} 条；完整证据保存在 native 目录，不能据此判断构件缺失。")
        try:
            payload=model_call(client,model,recognition_prompt(bundle),
                {**entries[view['page_id']],'id':view['id'],'crop':view['bbox'],'image_path':str(image_path),'image_dpi':300,
                 'detail_paths':[str(directory/'views'/Path(name).name) for name in view.get('detail_files',[])],
                 'allowed_view_tags':allowed_model_tags(bundle)},
                folder/f"{view['id']}_members.json",MemberResponse,reuse_saved=reuse_saved,emit=emit,audit=audit,
                reasoning_effort=settings.get('reasoning_effort','high'))
            rows=validate_rows(payload['members'],view,one);result['view_members'].extend(rows)
            count=sum(r['member_cnt'] for r in rows)
            # Native labels are only a local check; never part of the AI request.
            native_profiles=Counter(profile for t in pages[view['page_id']]['texts'] if inside(t,view['bbox']) for profile,_ in labels(t['text']))
            recognized_profiles=Counter()
            for row in rows:recognized_profiles[section(row['spec'])[0]]+=row['member_cnt']
            native_count=sum(native_profiles.values())
            coverage=[{'spec':spec,'label_count':native_profiles[spec],'member_cnt':recognized_profiles[spec]}
                      for spec in sorted(native_profiles.keys()|recognized_profiles.keys())
                      if native_profiles[spec]!=recognized_profiles[spec]]
            status.update(status='completed' if rows else 'empty',member_cnt=count,label_count=native_count,coverage_differences=coverage)
            audit.event('LOCAL_PROFILE_COVERAGE_CHECK',view_id=view['id'],differences=coverage,
                        reason='只在本地比较各规格标签与候选段数量；拼接可能造成差异，不强制补齐或删除。')
            if count!=native_count:result['warnings'].append(f"{view['title']}：{native_count}处规格标签与{count}个候选段不一致，需核对。")
            elif coverage:result['warnings'].append(f"{view['title']} 总数虽相同，但各规格数量覆盖不一致，请核对。")
            if not rows:result['warnings'].append(f"{view['title']} 未识别到构件，不能作为零根验收。")
            for row in rows:audit.event('VIEW_MEMBER_RECOGNIZED',**row)
            emit(f"{view['title']}：{len(rows)}行，{count}个候选构件段，等待核对。")
        except Exception as exc:
            status['status']='failed';status['error']=audit.scrub(str(exc))
            result['warnings'].append(f"{view['title']} 识别失败：{status['error']}")
            audit.event('IMAGE_MEMBER_FAILED',view_id=view['id'],error=status['error'])
        write_json(directory/'image_recognition_checkpoint.json',result)
    return validate_ledger(result)

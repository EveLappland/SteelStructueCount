"""Stage one freezes images; stage two interprets them with view-scoped evidence."""
import hashlib
import json
import struct
import uuid
import threading
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
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
from .ai_concurrency import view_concurrency


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
    emit('第一阶段：定位独立视图，准备高清裁切和逐视图轴线或面名称目录。')
    result=discover(directory,settings,emit,reuse_saved=reuse_saved,audit=audit,seed=seed,regions_only=True)
    freeze_images(directory,result)
    audit.event('VIEW_CATALOG_PREPARED',view_count=len(result['views']),axis_count=result['axis_catalog']['axis_count'],
                reason='第一阶段定位视图及Alignment/标高平面名称，不调用构件识别；确认后图片与目录供第二阶段使用。')
    return result


def recognize_views(directory,one,settings,emit,audit,*,reuse_saved=False):
    from .pipeline import load_native,model_call,OpenAI
    batch_started=time.monotonic()
    limit=view_concurrency(settings)
    base,key,model=_api_settings(settings)
    pages={p['id']:p for p in load_native(directory)}
    # Derived catalogs use the current reference-plane definition. Explicitly
    # reviewed per-view names remain authoritative through build_axis_catalog.
    one={**one,'axis_catalog':build_axis_catalog(list(pages.values()),one['views'])}
    entries={e['id']:e for e in json.loads((directory/'pdf_manifest.json').read_text(encoding='utf-8'))}
    folder=directory/'responses'/f'attempt-images-{uuid.uuid4().hex}'
    result=StageTwo(**{**one,'analysis_pending':False},recognition_mode='view_ledger').model_dump()
    result['warnings']=[]
    identities=[v['id'] for v in one['views']]
    if len(identities)!=len(set(identities)):raise ValueError('视图ID重复，不能安全保存并发识别响应。')
    prepared=[]
    # Prepare native context and validate frozen images on the coordinator.
    # Workers only read immutable PNGs/JSON and perform independent API calls.
    for view in one['views']:
        if view['kind']=='ISOMETRIC':continue
        item={'view':view,'status':{'view_id':view['id'],'status':'queued','member_cnt':0},
              'rows':[],'warnings':[]}
        prepared.append(item)
        if not view.get('region_confirmed'):
            item['status']['status']='region_pending'
            item['warnings'].append(f"{view['title']} 第一阶段裁切尚未确认，未识别构件。")
            continue
        image_path=directory/'views'/Path(view.get('image_file','')).name
        if not view.get('image_file') or not image_path.is_file() or view.get('image_bbox')!=view['bbox']:
            raise ValueError(f"{view['title']} 缺少与已确认区域一致的高清图片，请返回第一阶段修正。")
        bundle=make_input(pages[view['page_id']],view,one)
        image=image_path.read_bytes();width,height=struct.unpack('>II',image[16:24])
        bundle['image']={'width_px':width,'height_px':height,'dpi':300,'format':'PNG'}
        input_path=folder/f"{view['id']}_input.json";write_json(input_path,bundle)
        context=bundle['native_evidence']
        audit.event('IMAGE_MEMBER_REQUEST',view_id=view['id'],image_file=view['image_file'],
                    dispatch_state='prepared',
                    image_sha256=hashlib.sha256(image).hexdigest(),width_px=width,height_px=height,dpi=300,
                    detail_files=view.get('detail_files',[]),
                    input_file=str(input_path.relative_to(directory)),native_text_sent=bool(context['texts']),vectors_sent=bool(context['vectors']),
                    text_count=len(context['texts']),vector_count=len(context['vectors']),vector_total=context['vector_total'],
                    vector_context_truncated=context['vector_context_truncated'])
        if context['vector_context_truncated']:
            item['warnings'].append(f"{view['title']} 矢量辅助证据只发送了 {len(context['vectors'])}/{context['vector_total']} 条；完整证据保存在 native 目录，不能据此判断构件缺失。")
        item.update(bundle=bundle,image_path=image_path)

    def checkpoint():
        # Only this coordinator writes the shared ledger/checkpoint. Rebuild in
        # catalog order, regardless of the order API responses arrive in.
        result['view_members']=[row for item in prepared for row in item['rows']]
        result['view_analysis']=[dict(item['status']) for item in prepared]
        result['warnings']=[warning for item in prepared for warning in item['warnings']]
        target=directory/'image_recognition_checkpoint.json'
        temporary=target.with_suffix('.tmp')
        write_json(temporary,result);temporary.replace(target)

    eligible=[item for item in prepared if item['status']['status']=='queued']
    workers=min(limit,len(eligible))
    audit.event('VIEW_BATCH_STARTED',configured_concurrency=limit,worker_count=workers,
                eligible_views=len(eligible),skipped_views=len(prepared)-len(eligible),
                reason='各视图独立请求；共享台账和断点按目录顺序由主线程汇总，阶段确认顺序不变。')
    emit(f'第二阶段并发识图：{len(eligible)} 个视图，同时最多 {workers} 个请求。')
    checkpoint()
    active=0;active_lock=threading.Lock()

    def recognize(item):
        nonlocal active
        view=item['view'];bundle=item['bundle'];image_path=item['image_path']
        started=time.monotonic();status='failed'
        with active_lock:
            active+=1;running=active
        client=None
        try:
            audit.event('VIEW_RECOGNITION_STARTED',view_id=view['id'],title=view['title'],
                        active_views=running,concurrency_limit=limit)
            emit(f"开始识图：{view['title']}；当前同时处理 {running}/{workers} 个视图。")
            # Each worker owns its HTTP client; SDK retries remain inside the
            # same slot and retain the existing high/max reasoning settings.
            client=OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1)
            payload=model_call(client,model,recognition_prompt(bundle),
                {**entries[view['page_id']],'id':view['id'],'crop':view['bbox'],'image_path':str(image_path),'image_dpi':300,
                 'detail_paths':[str(directory/'views'/Path(name).name) for name in view.get('detail_files',[])],
                 'allowed_view_tags':allowed_model_tags(bundle)},
                folder/f"{view['id']}_members.json",MemberResponse,reuse_saved=reuse_saved,emit=emit,audit=audit,
                reasoning_effort=settings.get('reasoning_effort','high'))
            rows=validate_rows(payload['members'],view,one)
            count=sum(r['member_cnt'] for r in rows)
            # Native labels are only a local check; never part of the AI request.
            native_profiles=Counter(profile for t in pages[view['page_id']]['texts'] if inside(t,view['bbox']) for profile,_ in labels(t['text']))
            recognized_profiles=Counter()
            for row in rows:recognized_profiles[section(row['spec'])[0]]+=row['member_cnt']
            native_count=sum(native_profiles.values())
            coverage=[{'spec':spec,'label_count':native_profiles[spec],'member_cnt':recognized_profiles[spec]}
                      for spec in sorted(native_profiles.keys()|recognized_profiles.keys())
                      if native_profiles[spec]!=recognized_profiles[spec]]
            outcome={'rows':rows,'warnings':[], 'status':{'view_id':view['id'],
                'status':'completed' if rows else 'empty','member_cnt':count,
                'label_count':native_count,'coverage_differences':coverage}}
            audit.event('LOCAL_PROFILE_COVERAGE_CHECK',view_id=view['id'],differences=coverage,
                        reason='只在本地比较各规格标签与候选段数量；拼接可能造成差异，不强制补齐或删除。')
            if count!=native_count:outcome['warnings'].append(f"{view['title']}：{native_count}处规格标签与{count}个候选段不一致，需核对。")
            elif coverage:outcome['warnings'].append(f"{view['title']} 总数虽相同，但各规格数量覆盖不一致，请核对。")
            if not rows:outcome['warnings'].append(f"{view['title']} 未识别到构件，不能作为零根验收。")
            for row in rows:audit.event('VIEW_MEMBER_RECOGNIZED',**row)
            status=outcome['status']['status']
            return outcome
        finally:
            try:
                if client is not None:
                    try:client.close()
                    except Exception as error:
                        audit.event('VIEW_CLIENT_CLOSE_FAILED',view_id=view['id'],error=str(error))
            finally:
                with active_lock:
                    active-=1;running=active
                audit.event('VIEW_RECOGNITION_FINISHED',view_id=view['id'],status=status,
                    active_views=running,elapsed_seconds=round(time.monotonic()-started,3))

    if eligible:
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='steel-view') as executor:
            futures={executor.submit(recognize,item):item for item in eligible}
            finished=0
            for future in as_completed(futures):
                item=futures[future];view=item['view'];finished+=1
                try:
                    outcome=future.result()
                    item['rows']=outcome['rows'];item['status']=outcome['status']
                    item['warnings'].extend(outcome['warnings'])
                    emit(f"识图完成 {finished}/{len(eligible)}：{view['title']}，{len(item['rows'])} 行、{item['status']['member_cnt']} 个候选段。")
                except Exception as exc:
                    error=audit.scrub(str(exc))
                    item['status']={'view_id':view['id'],'status':'failed','member_cnt':0,'error':error}
                    item['warnings'].append(f"{view['title']} 识别失败：{error}")
                    audit.event('IMAGE_MEMBER_FAILED',view_id=view['id'],error=error)
                    emit(f"识图完成 {finished}/{len(eligible)}：{view['title']} 失败；其他视图继续。")
                checkpoint()
    audit.event('VIEW_BATCH_FINISHED',configured_concurrency=limit,worker_count=workers,
                completed_views=sum(i['status']['status']=='completed' for i in prepared),
                failed_views=sum(i['status']['status']=='failed' for i in prepared),
                elapsed_seconds=round(time.monotonic()-batch_started,3),
                reason='全部独立请求结束，按原视图目录生成阶段草稿；成功响应与失败记录均保留。')
    return validate_ledger(result)

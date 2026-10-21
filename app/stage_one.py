"""Page assessment -> construction-view catalog -> frozen crops. No member geometry."""
import json
import re
import uuid
import time
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED,as_completed
from pathlib import Path
from typing import Literal
from pydantic import Field,model_validator
from .domain import Contract,StageOne,View
from .axis_catalog import build_axis_catalog
from .profiles import section
from .member_recognition import normalize_view_tag,normalize_view_tags
from .native_pdf import write_json
from .quantity_reference import QuantityReference


class PageAssessment(Contract):
    decision: Literal['CONSTRUCTION','SKIP','REVIEW']
    reason: str = Field(min_length=1)


class ConstructionView(Contract):
    title: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal['PLAN','ELEVATION']
    bbox: list[float] = Field(min_length=4,max_length=4)
    plane_names: list[str]
    steel_types: list[str]

    @model_validator(mode='after')
    def valid_crop(self):
        x0,y0,x1,y1=self.bbox
        if not 0<=x0<x1<=1 or not 0<=y0<y1<=1:raise ValueError('裁切需为有效的整页归一化矩形')
        return self


class ConstructionCatalog(Contract):
    views: list[ConstructionView]


def _prompt(name):
    return (Path(__file__).parent/'prompts'/name).read_text(encoding='utf-8')


def _steel_types(values):
    return sorted({profile for value in values if (profile:=section(value.strip())[0])})


def _crop_check(proposal,page):
    key=lambda value:re.sub(r'\s+','',value).casefold()
    titles=[t for t in page['texts'] if key(t['text'])==key(proposal['title'])]
    box=proposal['bbox'];issues=[]
    if titles and not any(box[0]-.002<=t['bbox'][0] and box[1]-.002<=t['bbox'][1]
                         and box[2]+.002>=t['bbox'][2] and box[3]+.002>=t['bbox'][3] for t in titles):
        issues.append('裁切未完整包含对应的原生标题，请核对区域。')
    return issues


def refresh_catalog(data,pages):
    """Recompute names/counts from editable AI lists; never regex-count native labels."""
    source={p['id']:p for p in pages};decisions={p['page_id']:dict(p) for p in data['page_decisions']}
    if len(decisions)!=len(data['page_decisions']) or set(decisions)!=set(source):
        raise ValueError('页面用途记录需与输入PDF页一一对应')
    warnings=[];seen=set()
    for record in decisions.values():
        PageAssessment.model_validate({k:record[k] for k in ('decision','reason')})
        record['view_ids']=[]
        record.update(file=source[record['page_id']]['file'],page=source[record['page_id']]['page'])
    for view in data['views']:
        if view['id'] in seen:raise ValueError('视图ID重复')
        seen.add(view['id'])
        record=decisions.get(view['page_id'])
        if not record or record['decision']!='CONSTRUCTION':raise ValueError('只有已判定有效的页面可包含交付视图')
        if view['kind'] not in ('PLAN','ELEVATION') or view.get('takeoff_role','MEMBERS')!='MEMBERS':
            raise ValueError('第一阶段交付目录只包含平面/立面建造视图')
        if not view.get('plane_name','').strip():raise ValueError('视图面名称不能为空')
        view['plane_name']=normalize_view_tag(view['plane_name'])
        view['axis_labels']=normalize_view_tags([view['plane_name'],*view.get('axis_labels',[])])
        view['steel_types']=_steel_types(view.get('steel_types',[]))
        if view.get('quantity_reference') is not None:
            view['quantity_reference']=QuantityReference.model_validate(view['quantity_reference']).model_dump()
        if view['kind']=='ELEVATION':view['fixed_label']=view['plane_name']
        record['view_ids'].append(view['id'])
        if not view['region_confirmed']:warnings.append(f"裁切待确认：{view['title']}")
        if not view['steel_types']:warnings.append(f"规格待确认：{view['title']} 未提取到钢材种类，不能认定无钢材。")
    for record in decisions.values():
        if record['decision']=='SKIP':record['status']='skipped'
        elif record['decision']=='REVIEW' or not record['view_ids']:
            record['status']='needs_review'
            warnings.append(f"页面待确认：{record['file']} 第{record['page']}页；{record.get('issue') or record['reason']}")
        else:record['status']='located'
    data['page_decisions']=list(decisions.values())
    data['warnings']=warnings
    data['profile_catalog']=sorted({p for v in data['views'] for p in v['steel_types']})
    data['axis_catalog']=build_axis_catalog(pages,data['views'])
    data['catalog_summary']={
        'steel_type_count':len(data['profile_catalog']),'steel_types':data['profile_catalog'],
        'view_count':len(data['views']),
        'plan_names':sorted({v['plane_name'] for v in data['views'] if v['kind']=='PLAN'}),
        'elevation_names':sorted({v['plane_name'] for v in data['views'] if v['kind']=='ELEVATION'}),
        'plane_name_count':data['axis_catalog']['axis_count'],
        'plane_names':[a['name'] for a in data['axis_catalog']['axes']],
        'skipped_pages':sum(p['status']=='skipped' for p in data['page_decisions']),
        'pending_pages':sum(p['status']=='needs_review' for p in data['page_decisions'])}
    counted=[v for v in data['views'] if (v.get('quantity_reference') or {}).get('total_count') is not None]
    data['catalog_summary'].update(counted_views=len(counted),uncounted_views=len(data['views'])-len(counted),
        member_count_reference=sum(v['quantity_reference']['total_count'] for v in counted),
        member_count_reference_complete=len(counted)==len(data['views']))
    for view in data['views']:
        reference=view.get('quantity_reference')
        if not reference or reference.get('total_count') is None:
            warnings.append(f"数量标准待补充：{view['title']}；{(reference or {}).get('issue') or '没有独立数量标准，不能当作零根。'}")
        elif reference.get('issue'):
            warnings.append(f"数量标准说明：{view['title']}；{reference['issue']}")
    return data


def prepare_quantities(directory,result,pages,settings,emit,audit,*,reuse_saved=False):
    """After serial PDF rendering, independent per-view image/text counts."""
    from .pipeline import model_call,OpenAI,response_content
    from .settings import _api_settings
    from .ai_concurrency import view_concurrency
    from .native_pdf import inside
    source={p['id']:p for p in pages}
    views=[v for v in result['views'] if v.get('region_confirmed') and v.get('image_file')]
    if not views:return
    base,key,model=_api_settings(settings)
    folder=directory/'responses'/f'attempt-counts-{uuid.uuid4().hex}'
    prepared=[]
    for view in views:
        texts=[{'id':t['id'],'text':t['text'],'bbox':t['bbox']} for t in source[view['page_id']]['texts'] if inside(t,view['bbox'])]
        metadata={'view_id':view['id'],'title':view['title'],'current_view_tag':view['plane_name'],
                  'axis_names':view['axis_labels'],'profile_catalog':view['steel_types'],
                  'texts':texts,'coordinate_note':'文字 bbox 为PDF页归一化位置，仅用于定位，不能推算毫米。'}
        entry={'id':view['id']+'_quantity','source_path':str(directory/'views'/view['image_file']),
               'page':source[view['page_id']]['page'],'image_path':str(directory/'views'/view['image_file']),'image_dpi':300}
        prepared.append((view,entry,_prompt('view_quantity.md')+'\n输入：\n'+json.dumps(metadata,ensure_ascii=False)))

    def count(entry,prompt):
        with OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1) as client:
            for attempt in range(2):
                name=entry['id']+('' if not attempt else '_format01')
                path=folder/f'{name}.json'
                try:
                    return model_call(client,model,prompt,{**entry,'id':name},path,QuantityReference,
                        emit=emit,audit=audit,reuse_saved=reuse_saved,reasoning_effort=settings.get('reasoning_effort','high'))
                except Exception as error:
                    if attempt:raise
                    previous=None
                    if path.exists():
                        try:previous=response_content(json.loads(path.read_text(encoding='utf-8')))
                        except (ValueError,KeyError,TypeError):pass
                    prompt+='\n上一次请求未通过本地解析，按图重新返回符合schema的数量对象，不要猜数或增加附加字段。错误和旧回复仅供定位问题：\n'+json.dumps({'error':audit.scrub(str(error)),'previous_reply':previous},ensure_ascii=False)
                    audit.event('QUANTITY_FORMAT_RETRY',view_id=entry['id'],error=str(error))
    workers=min(view_concurrency(settings),len(prepared))
    emit(f'第一阶段独立统计数量：{len(views)} 个视图，请求并发上限 {workers}。')
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='steel-count') as executor:
        futures={executor.submit(count,entry,prompt):(view,entry,prompt) for view,entry,prompt in prepared}
        for future in as_completed(futures):
            view,entry,_=futures[future]
            try:
                view['quantity_reference']=QuantityReference.model_validate(future.result()).model_dump()
                view['quantity_reference_image']=view['image_file']
                audit.event('VIEW_QUANTITY_REFERENCE_READY',view_id=view['id'],**view['quantity_reference'])
                emit(f"数量标准：{view['title']}，{view['quantity_reference']['total_count']} 个构件段/组合。")
            except Exception as error:
                reason=audit.scrub(str(error))
                view['quantity_reference']={'total_count':None,'spec_counts':[],'issue':reason}
                audit.event('VIEW_QUANTITY_REFERENCE_FAILED',view_id=view['id'],error=reason)
                emit(f"数量标准未完成：{view['title']}；原因已记录，不能当作零根。")
            write_json(directory/'quantity_reference_checkpoint.json',{'views':result['views']})


def prepare(directory,settings,emit,audit,*,reuse_saved=False):
    from .pipeline import extract,model_call,OpenAI
    from .image_workflow import freeze_images
    from .settings import _api_settings
    from .drawing_input import render_data_uri
    from .ai_concurrency import view_concurrency
    limit=view_concurrency(settings)
    entries,pages=extract(directory,emit,audit,render_previews=False)
    base,key,model=_api_settings(settings)
    folder=directory/'responses'/f'attempt-pages-{uuid.uuid4().hex}'
    result=StageOne(views=[]).model_dump()
    records={};adopted={};finished=set();pending={};next_page=0
    started=time.monotonic();workers=min(limit,len(pages))
    audit.event('PAGE_BATCH_STARTED',configured_concurrency=limit,workers=workers,pages=len(pages))

    def request(entry,page,step):
        # Workers only call HTTP with already rendered image bytes. No PDF access,
        # shared ledgers, checkpoint writes or shared HTTP clients.
        contract=PageAssessment if step=='purpose' else ConstructionCatalog
        prompt=_prompt('page_purpose.md' if step=='purpose' else 'construction_catalog.md')
        text=json.dumps({'file':page['file'],'page':page['page'],
            'texts':[{'text':t['text'],'bbox':t['bbox']} for t in page['texts']]},ensure_ascii=False)
        client=OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1)
        try:
            return model_call(client,model,prompt+'\n页面文字：\n'+text,entry,
                folder/f"{page['id']}_{step}.json",contract,emit=emit,audit=audit,
                reuse_saved=reuse_saved,reasoning_effort=settings.get('reasoning_effort','high'))
        finally:client.close()

    def checkpoint():
        result['page_decisions']=[records[i] for i in sorted(finished)]
        result['views']=[v for i in sorted(finished) for v in adopted.get(i,[])]
        target=directory/'page_catalog_checkpoint.json'
        temporary=target.with_suffix('.tmp')
        write_json(temporary,result);temporary.replace(target)

    def finish(i,error=None):
        record=records[i];page=pages[i]
        if error is not None:
            record.setdefault('decision','REVIEW');record.setdefault('reason','页面用途判断未完成，需要重试。')
            record['issue']=audit.scrub(str(error));adopted.pop(i,None)
            emit(f"页面待确认：{page['file']} 第{page['page']}页；{record['issue']}")
            audit.event('PAGE_PREPARATION_FAILED',page_id=page['id'],error=record['issue'])
        finished.add(i);checkpoint()
        audit.event('PAGE_PREPARATION_FINISHED',page_id=page['id'],completed=len(finished),total=len(pages))
        emit(f"第一阶段页面处理：{len(finished)}/{len(pages)}。")

    def submit(executor,i,step):
        page=pages[i]
        if i not in records:records[i]=dict(page_id=page['id'],file=page['file'],page=page['page'],view_ids=[],issue='')
        try:
            size=1400 if step=='purpose' else 3600
            emit(f"{'AI判断页面用途' if step=='purpose' else '定位有效平面/立面并提取面名与钢材种类'}：{page['file']} 第{page['page']}页。")
            entry={**entries[i],'image_max_dimension':size}
            # All PyMuPDF rendering stays on the coordinator, including previews.
            entry['prepared_data_uri']=render_data_uri(entry,max_dimension=size)
            future=executor.submit(request,entry,page,step)
            pending[future]=(i,step)
            audit.event('PAGE_REQUEST_QUEUED',page_id=page['id'],step=step,in_flight=len(pending))
        except Exception as error:finish(i,error)

    if workers:
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='steel-page') as executor:
            while next_page<len(pages) or pending:
                while next_page<len(pages) and len(pending)<workers:
                    submit(executor,next_page,'purpose');next_page+=1
                if not pending:continue
                completed,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in sorted(completed,key=lambda f:pending[f][0]):
                    i,step=pending.pop(future);page=pages[i];record=records[i]
                    try:
                        response=future.result()
                        if step=='purpose':
                            record.update(PageAssessment.model_validate(response).model_dump())
                            audit.event('PAGE_PURPOSE_DECIDED',**record)
                            if record['decision']=='CONSTRUCTION':
                                submit(executor,i,'catalog');continue
                            emit(f"{'跳过' if record['decision']=='SKIP' else '用途待确认'}：{page['file']} 第{page['page']}页；{record['reason']}；不生成高清截图。")
                        else:
                            catalog=ConstructionCatalog.model_validate(response).model_dump()
                            if not catalog['views']:record['issue']='页面判为有效，但未定位到建造视图；请重试或核对。'
                            names=set();views=[]
                            for index,proposal in enumerate(catalog['views'],1):
                                name=normalize_view_tag(proposal['name'])
                                identity=(proposal['kind'],name,tuple(proposal['bbox']))
                                if identity in names:raise ValueError('同页视图区域被重复定位')
                                names.add(identity);issues=_crop_check(proposal,page)
                                views.append(View(id=f"{page['id']}-view{index:02d}",page_id=page['id'],title=proposal['title'],
                                    kind=proposal['kind'],bbox=proposal['bbox'],title_bbox=proposal['bbox'],
                                    plane_name=name,axis_labels=proposal['plane_names'],steel_types=proposal['steel_types'],
                                    fixed_label=name if proposal['kind']=='ELEVATION' else None,region_confirmed=not issues,
                                    localization={'status':'needs_review' if issues else 'checked','issues':issues,
                                        'reason':'AI定位完整视图；程序检查矩形边界与可匹配原生标题，不推断构件坐标或长度。'}).model_dump())
                            adopted[i]=views
                        finish(i)
                    except Exception as error:finish(i,error)
    refresh_catalog(result,pages)
    freeze_images(directory,result)
    prepare_quantities(directory,result,pages,settings,emit,audit,reuse_saved=reuse_saved)
    refresh_catalog(result,pages)
    audit.event('PAGE_BATCH_FINISHED',pages=len(pages),workers=workers,elapsed_seconds=round(time.monotonic()-started,3))
    audit.event('CONSTRUCTION_CATALOG_READY',**result['catalog_summary'],
                reason='仅有效建造视图生成300DPI裁切；钢材与面目录来自AI枚举，程序去重计数。')
    emit(f"第一阶段完成：{len(result['profile_catalog'])}种钢材，{len(result['catalog_summary']['plan_names'])}个平面，{len(result['catalog_summary']['elevation_names'])}个立面；{result['catalog_summary']['skipped_pages']}页未采用。")
    return result

"""Page assessment -> construction-view catalog -> frozen crops. No member geometry."""
import json
import re
import uuid
from pathlib import Path
from typing import Literal
from pydantic import Field,model_validator
from .domain import Contract,StageOne,View
from .axis_catalog import build_axis_catalog
from .geometry import section
from .member_recognition import normalize_view_tag,normalize_view_tags
from .native_pdf import write_json


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
    return data


def prepare(directory,settings,emit,audit,*,reuse_saved=False):
    from .pipeline import extract,model_call,OpenAI
    from .image_workflow import freeze_images
    from .llm_runner import _api_settings
    entries,pages=extract(directory,emit,audit,render_previews=False)
    base,key,model=_api_settings(settings)
    folder=directory/'responses'/f'attempt-pages-{uuid.uuid4().hex}'
    result=StageOne(workflow='image_first',catalog_mode='ai_pages',views=[],analysis_pending=True).model_dump()
    client=OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1)
    try:
        for entry,page in zip(entries,pages):
            record=dict(page_id=page['id'],file=page['file'],page=page['page'],view_ids=[],issue='')
            text=json.dumps({'file':page['file'],'page':page['page'],
                'texts':[{'text':t['text'],'bbox':t['bbox']} for t in page['texts']]},ensure_ascii=False)
            try:
                emit(f"AI判断页面用途：{page['file']} 第{page['page']}页。")
                assessment=model_call(client,model,_prompt('page_purpose.md')+'\n页面文字：\n'+text,
                    {**entry,'image_max_dimension':1400},folder/f"{page['id']}_purpose.json",PageAssessment,
                    emit=emit,audit=audit,reuse_saved=reuse_saved,reasoning_effort=settings.get('reasoning_effort','high'))
                record.update(assessment)
                audit.event('PAGE_PURPOSE_DECIDED',**record)
                if record['decision']!='CONSTRUCTION':
                    emit(f"{'跳过' if record['decision']=='SKIP' else '用途待确认'}：{page['file']} 第{page['page']}页；{record['reason']}；不生成高清截图。")
                    continue
                emit(f"定位有效平面/立面并提取面名与钢材种类：{page['file']} 第{page['page']}页。")
                catalog=model_call(client,model,_prompt('construction_catalog.md')+'\n页面文字：\n'+text,
                    {**entry,'image_max_dimension':3600},folder/f"{page['id']}_catalog.json",ConstructionCatalog,
                    emit=emit,audit=audit,reuse_saved=reuse_saved,reasoning_effort=settings.get('reasoning_effort','high'))
                if not catalog['views']:record['issue']='页面判为有效，但未定位到建造视图；请重试或核对。'
                names=set()
                for index,proposal in enumerate(catalog['views'],1):
                    proposal=ConstructionView.model_validate(proposal).model_dump()
                    name=normalize_view_tag(proposal['name'])
                    identity=(proposal['kind'],name,tuple(proposal['bbox']))
                    if identity in names:raise ValueError('同页视图区域被重复定位')
                    names.add(identity)
                    issues=_crop_check(proposal,page)
                    view=View(id=f"{page['id']}-view{index:02d}",page_id=page['id'],title=proposal['title'],
                        kind=proposal['kind'],bbox=proposal['bbox'],title_bbox=proposal['bbox'],
                        plane_name=name,axis_labels=proposal['plane_names'],steel_types=proposal['steel_types'],
                        fixed_label=name if proposal['kind']=='ELEVATION' else None,region_confirmed=not issues,
                        localization={'status':'needs_review' if issues else 'checked','issues':issues,
                            'reason':'AI定位完整视图；程序检查矩形边界与可匹配原生标题，不推断构件坐标或长度。'}).model_dump()
                    result['views'].append(view)
            except Exception as error:
                record.setdefault('decision','REVIEW');record.setdefault('reason','页面用途判断未完成，需要重试。')
                record['issue']=audit.scrub(str(error))
                # A malformed response cannot leave a partially adopted page.
                result['views']=[v for v in result['views'] if v['page_id']!=page['id']]
                emit(f"页面待确认：{page['file']} 第{page['page']}页；{record['issue']}")
                audit.event('PAGE_PREPARATION_FAILED',page_id=page['id'],error=record['issue'])
            finally:
                result['page_decisions'].append(record)
                write_json(directory/'page_catalog_checkpoint.json',result)
    finally:client.close()
    refresh_catalog(result,pages)
    freeze_images(directory,result)
    audit.event('CONSTRUCTION_CATALOG_READY',**result['catalog_summary'],
                reason='仅有效建造视图生成300DPI裁切；钢材与面目录来自AI枚举，程序去重计数。')
    emit(f"第一阶段完成：{len(result['profile_catalog'])}种钢材，{len(result['catalog_summary']['plan_names'])}个平面，{len(result['catalog_summary']['elevation_names'])}个立面；{result['catalog_summary']['skipped_pages']}页未采用。")
    return result

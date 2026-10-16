from __future__ import annotations

import logging
import json
import shutil
import threading
import traceback
import uuid
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .domain import StageAction, StageEdit
from .pipeline import execute, pending_reasons, validate_draft, render_draft, load_native
from .native_pdf import inside
from .store import JobStore, StoreError
from .audit import AuditLog
from .discovery_inventory import build_inventory
from .axis_catalog import build_axis_catalog
from .member_recognition import normalize_view_tags
from .ai_concurrency import view_concurrency

ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env')
logger=logging.getLogger('uvicorn.error')


def create_app(root=ROOT, start_workers=True):
    root=Path(root)
    jobs=root/'data'/'jobs'
    jobs.mkdir(parents=True,exist_ok=True)
    store=JobStore(root/'data'/'jobs_v2.sqlite3')
    serial=threading.Lock()
    credentials={}

    @asynccontextmanager
    async def lifespan(application):
        store.recover()
        yield

    application=FastAPI(title='钢材坐标实体统计',lifespan=lifespan)
    application.state.store=store
    application.state.jobs_directory=jobs

    @application.exception_handler(StoreError)
    async def store_error(request,exc):
        return JSONResponse(status_code=exc.status,content={'detail':str(exc)})

    def directory(job_id):
        store.get(job_id)
        return jobs/job_id

    def native_index(job_id):
        path=directory(job_id)/'native_index.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []

    def worker(job_id,stage,reuse_saved=False):
        with serial:
            try:
                policy=jobs/job_id/'workflow.json'
                automatic=json.loads(policy.read_text(encoding='utf-8')).get('auto_run',False) if policy.is_file() else False
                while store.claim(job_id,stage):
                    audit=AuditLog(jobs/job_id,stage=stage,secrets=(credentials.get(job_id,{}).get('api_key'),os.getenv('LLM_API_KEY')))
                    try:
                        execute(store,job_id,stage,jobs/job_id,credentials.get(job_id,{}),reuse_saved=reuse_saved,audit=audit)
                        if stage==4 or not automatic:break
                        draft=store.stage(job_id,stage)
                        if stage==1 and not draft['data'].get('views'):
                            store.progress(job_id,'没有有效建造视图，自动流程停止；请查看页面用途说明。');break
                        audit.event('STAGE_AUTO_ADVANCED',pending=pending_reasons(stage,draft['data']),
                            reason='自动模式继续处理；问题保留审计，不代表人工确认。')
                        stage=store.approve(job_id,stage,draft['revision'],True,reason='自动模式继续，非人工工程确认。')
                        credentials.setdefault(job_id,{})['reasoning_effort']='high'
                        reuse_saved=False
                        if stage is None:break
                    except Exception as exc:
                        audit.text(jobs/job_id/'error.txt',traceback.format_exc())
                        audit.event('STAGE_FAILED',error_type=type(exc).__name__,error=str(exc))
                        logger.error('job=%s stage=%s failed: %s',job_id,stage,audit.scrub(str(exc)))
                        store.fail(job_id,stage,audit.scrub(str(exc)));break
            finally:
                if stage in (3,4) or store.get(job_id)['state']=='failed':credentials.pop(job_id,None)

    def launch(job_id,stage,settings=None,*,reuse_saved=False):
        if settings and stage in (1,2,3):
            existing=credentials.get(job_id,{})
            credentials[job_id]={**existing,**{k:v for k,v in settings.items() if k in ('base_url','api_key','model','review_mode','max_concurrency','reasoning_effort') and v}}
        if start_workers:
            threading.Thread(target=worker,args=(job_id,stage,reuse_saved),daemon=True).start()

    application.state.worker=worker

    @application.post('/api/jobs')
    def create_job(files:list[UploadFile]=File(...),base_url:str=Form(''),
                   api_key:str=Form(''),model:str=Form(''),include_lengths:bool=Form(True),
                   max_concurrency:int|None=Form(None,ge=1),auto_run:bool=Form(True)):
        pdfs=[file for file in files if file.filename and file.filename.lower().endswith('.pdf')]
        if not pdfs:
            raise HTTPException(400,'请至少上传一个 PDF。')
        job_id=uuid.uuid4().hex
        uploads=jobs/job_id/'uploads'
        uploads.mkdir(parents=True)
        for index,upload in enumerate(pdfs,1):
            filename=Path(upload.filename.replace('\\','/')).name.replace(' ','_')
            try:
                with (uploads/f'{index:03d}_{filename}').open('wb') as target:
                    shutil.copyfileobj(upload.file,target)
            except OSError as exc:
                raise HTTPException(500,'无法保存上传文件。') from exc
        store.create(job_id)
        (jobs/job_id/'workflow.json').write_text(json.dumps({'auto_run':auto_run}),encoding='utf-8')
        launch(job_id,1,{'base_url':base_url,'api_key':api_key,'model':model,
                        'max_concurrency':str(max_concurrency) if max_concurrency is not None else ''})
        return {'id':job_id,'state':'queued','schema_version':2}

    @application.get('/api/jobs')
    def job_list():
        return store.list_jobs()

    @application.get('/api/jobs/{job_id}')
    def status(job_id:str):
        job=store.get(job_id)
        policy=jobs/job_id/'workflow.json'
        job['auto_run']=json.loads(policy.read_text(encoding='utf-8')).get('auto_run',False) if policy.is_file() else False
        job['download_url']=f'/api/jobs/{job_id}/download' if job.get('workbook') else None
        job['views_download_url']=f'/api/jobs/{job_id}/views-download' if job.get('views_archive') else None
        try:
            stage=store.stage(job_id,2 if any(s['stage']==2 and s['status']!='stale' for s in job['stages']) else 1)
            views=stage['data'].get('views',[])
        except StoreError:
            views=[]
        job['views']=[{**v,'filename':f"{v['id']}.jpg",'url':f"/api/jobs/{job_id}/views/{v['id']}.jpg"} for v in views]
        return job

    @application.get('/api/jobs/{job_id}/stages/{stage}')
    def get_stage(job_id:str,stage:int):
        result=store.stage(job_id,stage)
        if stage in (1,2) and 'views' in result['data'] and result['data'].get('catalog_mode')!='ai_pages':
            # Enrich existing v2 tasks without rewriting their approved history.
            inventory=build_inventory(result['data'],load_native(directory(job_id)))
            result['data']['discovery_inventory']=inventory
            result['data']['profile_catalog']=[p['profile'] for p in inventory['profiles']]
            if stage==2 or result['data'].get('workflow')=='image_first':
                result['data']['axis_catalog']=build_axis_catalog(load_native(directory(job_id)),result['data']['views'])
                if stage==1:
                    for view in result['data']['views']:
                        if not view.get('axes_reviewed'):
                            view['axis_labels']=result['data']['axis_catalog']['per_view'].get(view['id'],[])
        result['snapshots']=[s for s in store.snapshots(job_id) if s['stage']==stage]
        if stage==2 and result['data'].get('recognition_mode')=='view_ledger':
            for row in result['data'].get('view_members',[]):
                row['view_tags']=normalize_view_tags(row['view_tags'])
        return result

    @application.patch('/api/jobs/{job_id}/stages/{stage}')
    def edit_stage(job_id:str,stage:int,body:StageEdit):
        folder=directory(job_id)
        previous=store.stage(job_id,stage)
        if previous['revision']!=body.revision:
            raise StoreError('阶段版本已改变，请刷新后再操作。')
        try:
            data=validate_draft(folder,stage,body.data,store.stage(job_id,2)['data'] if stage==3 else None,
                                stage_one=store.stage(job_id,1)['data'] if stage==2 else None)
        except (ValidationError,ValueError,KeyError) as exc:
            raise HTTPException(422,str(exc)) from exc
        if stage==3 and data.get('reconciliation_mode')=='group_ledger':
            # Keep the server's original proposal, never a client-edited claim
            # of what the model returned. It remains useful for deferred groups.
            proposal=previous['data'].get('review',{}).get('ai_proposal')
            if proposal and previous['data'].get('source_hash')==data['source_hash']:
                data['review']['ai_proposal']=proposal
        if stage==1 and data.get('workflow')=='image_first':render_draft(folder,data)
        store.edit(job_id,stage,body.revision,data,body.reason)
        AuditLog(folder,stage).event('DRAFT_EDITED',revision=body.revision+1,reason=body.reason,
                                    downstream_invalidated=True)
        if stage<=2 and data.get('workflow')!='image_first':
            render_draft(folder,data)
        return store.stage(job_id,stage)

    @application.post('/api/jobs/{job_id}/stages/{stage}/approve')
    def approve_stage(job_id:str,stage:int,body:StageAction):
        draft=store.stage(job_id,stage)
        if draft['revision']!=body.revision:
            raise StoreError('阶段版本已改变，请刷新后再操作。')
        if draft['status'] not in ('awaiting_review','approved'):
            raise StoreError('只有等待确认的草稿可以确认。')
        if stage==1 and draft['data'].get('workflow')=='image_first' and draft['status']!='approved':
            try:view_concurrency({**credentials.get(job_id,{}),**{k:v for k,v in body.settings.items() if v}})
            except ValueError as error:raise HTTPException(422,str(error)) from error
        if draft['status']!='approved':
            if stage==1 and draft['data'].get('catalog_mode')=='ai_pages' and not draft['data'].get('views'):
                raise HTTPException(422,'没有有效平面或立面可交付第二阶段，请核对页面用途或重试。')
            pending=pending_reasons(stage,draft['data'])
            if pending and not body.acknowledge_pending:
                raise HTTPException(422,'存在未解决问题，请修正或勾选“允许带未解决问题继续”；此操作不会排除台账行。')
            if stage==3 and draft['data'].get('reconciliation_mode')=='group_ledger' and draft['data'].get('totals',{}).get('observation_units',0)>0 and not (draft['data'].get('resolutions') or draft['data'].get('exclusions')):
                raise HTTPException(422,'第三阶段尚未产生AI核验结果，不能直接导出空汇总；请重试第三阶段。')
        next_stage=store.approve(job_id,stage,body.revision,body.acknowledge_pending,reason=body.reason)
        if next_stage:
            if body.auto_run is not None:
                (directory(job_id)/'workflow.json').write_text(json.dumps({'auto_run':body.auto_run}),encoding='utf-8')
            AuditLog(directory(job_id),stage).event('STAGE_APPROVED',revision=body.revision,
                acknowledge_pending=body.acknowledge_pending,reason=body.reason or '用户确认。')
            launch(job_id,next_stage,{**body.settings,'reasoning_effort':'high'})
        return status(job_id)

    @application.post('/api/jobs/{job_id}/stages/{stage}/retry')
    def retry_stage(job_id:str,stage:int,body:StageAction):
        previous=store.stage(job_id,stage)
        if body.settings.get('reasoning_effort','high') not in ('low','high','max'):
            raise HTTPException(422,'思考强度只能是 low、high 或 max。')
        if stage==2 and store.stage(job_id,1)['data'].get('workflow')=='image_first':
            try:view_concurrency({**credentials.get(job_id,{}),**{k:v for k,v in body.settings.items() if v}})
            except ValueError as error:raise HTTPException(422,str(error)) from error
        resume=(stage in (1,2) and previous['status'] in ('failed','interrupted') and body.reuse_saved)
        if stage==3 and body.settings.get('review_mode')=='ai':resume=body.reuse_saved
        next_stage=store.retry(job_id,stage,body.revision)
        if body.auto_run is not None:
            (directory(job_id)/'workflow.json').write_text(json.dumps({'auto_run':body.auto_run}),encoding='utf-8')
        launch(job_id,next_stage,body.settings,reuse_saved=resume)
        return status(job_id)

    @application.get('/api/jobs/{job_id}/logs')
    def logs(job_id:str,after:int=Query(0,ge=0),limit:int=Query(200,ge=1,le=1000)):
        return AuditLog(directory(job_id)).read(after,limit)

    @application.get('/api/jobs/{job_id}/logs-download')
    def logs_download(job_id:str):
        path=AuditLog(directory(job_id)).path
        if not path.is_file():
            raise HTTPException(404,'任务日志尚未生成。')
        return FileResponse(path,filename=f'{job_id}_events.jsonl',media_type='application/x-ndjson')

    @application.get('/api/jobs/{job_id}/pages')
    def pages(job_id:str):
        return [{k:p[k] for k in ('id','file','page','width_pt','height_pt','titles')} |
                {'url':f"/api/jobs/{job_id}/views/{p['id']}.jpg"} for p in native_index(job_id)]

    @application.get('/api/jobs/{job_id}/evidence')
    def evidence(job_id:str,page_id:str,view_id:str='',q:str='',limit:int=Query(200,ge=1,le=1000)):
        if not any(p['id']==page_id for p in native_index(job_id)):
            raise HTTPException(404,'未找到 PDF 页。')
        native=json.loads((directory(job_id)/'native'/f'{page_id}.json').read_text(encoding='utf-8'))
        bbox=[0,0,1,1]
        if view_id:
            draft=store.stage(job_id,2 if store.get(job_id)['active_stage']>=2 else 1)['data']
            view=next((v for v in draft.get('views',[]) if v['id']==view_id and v['page_id']==page_id),None)
            if not view:
                raise HTTPException(404,'未找到视图。')
            bbox=view['bbox']
        rows=[item for item in native['texts']+native['vectors'] if inside(item,bbox)
              and (not q or q.lower() in (item['text']+' '+item['id']).lower())]
        return {'total':len(rows),'items':rows[:limit]}

    @application.get('/api/jobs/{job_id}/views/{filename}')
    def view_image(job_id:str,filename:str):
        folder=directory(job_id)
        allowed={p['id']+'.jpg' for p in native_index(job_id)}
        for n in (1,2):
            try:
                allowed.update(v['id']+'.jpg' for v in store.stage(job_id,n)['data'].get('views',[]))
                allowed.update(v['id']+'_300dpi.png' for v in store.stage(job_id,n)['data'].get('views',[])
                               if v.get('localization') or store.stage(job_id,n)['data'].get('recognition_mode')=='view_ledger')
                allowed.update(v['image_file'] for v in store.stage(job_id,n)['data'].get('views',[]) if v.get('image_file'))
                allowed.update(name for v in store.stage(job_id,n)['data'].get('views',[]) for name in v.get('detail_files',[]))
            except StoreError:
                pass
        path=folder/'views'/filename
        for snapshot in store.snapshots(job_id):
            if snapshot['stage'] not in (1,2):continue
            for view in snapshot.get('data',{}).get('views',[]):
                if view.get('image_file'):allowed.add(view['image_file'])
                allowed.update(view.get('detail_files',[]))
        if path.resolve().parent!=(folder/'views').resolve():raise HTTPException(404,'未找到图纸预览。')
        if filename in {p['id']+'.jpg' for p in native_index(job_id)} and not path.is_file():
            from .drawing_input import write_view_image
            entry=next((e for e in json.loads((folder/'pdf_manifest.json').read_text(encoding='utf-8')) if e['id']+'.jpg'==filename),None)
            if entry:write_view_image(entry,path)
        if filename not in allowed or not path.is_file():
            raise HTTPException(404,'未找到图纸预览。')
        return FileResponse(path,media_type='image/png' if path.suffix=='.png' else 'image/jpeg')

    def output(job_id,field,media):
        job=store.get(job_id)
        filename=job.get(field)
        path=jobs/job_id/'output'/filename if filename else None
        if job['state']!='completed' or not path or not path.is_file():
            raise HTTPException(404,'结果尚未确认并导出。')
        return FileResponse(path,filename=filename,media_type=media)

    @application.get('/api/jobs/{job_id}/download')
    def download(job_id:str):
        return output(job_id,'workbook','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @application.get('/api/jobs/{job_id}/views-download')
    def download_views(job_id:str):
        return output(job_id,'views_archive','application/zip')

    application.mount('/',StaticFiles(directory=ROOT/'static',html=True),name='static')
    return application


app=create_app()

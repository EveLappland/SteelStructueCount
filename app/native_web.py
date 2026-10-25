"""Web orchestration of the same fixed-label functions used by the CLI tools.

Separate storage keeps legacy tasks readable. Jobs queue; within one job,
view tasks and their child requests have independent limits.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .ai_concurrency import view_concurrency
from .task_slots import TaskSlots, task_concurrency
from .native_pdf import PDF_LOCK
from .audit import AuditLog
from .entity_inputs import read_inputs
from .entity_workflow import option, run as entities
from .label_length_workflow import input_manifest as length_manifest, run as lengths
from .settings import _api_settings
from . import native_stages


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')
    reasoning_effort: str = Field(default='high', pattern=r'^(low|high|max)$')
    max_concurrency: int | None = Field(default=None, ge=1)
    max_task_concurrency: int | None = Field(default=None, ge=1)
    max_calls: int | None = Field(default=None, ge=1)


class MatchRequest(RunRequest):
    source_ids: list[str] = Field(min_length=1)
    auto_run: bool = True


class StageControl(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    settings: dict = Field(default_factory=dict)
    acknowledge_pending: bool = False
    auto_run: bool | None = None
    reuse_saved: bool = True
    reason: str = ''


class NativeRuns:
    def __init__(self, root, start_workers=True):
        self.root = Path(root).resolve()
        self.folder = self.root/'data/native_runs'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.database = self.root/'data/native_runs.sqlite3'
        self.start_workers = start_workers
        self.queue = threading.Lock()
        self.credentials = {}
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, data TEXT NOT NULL)')

    def recover(self):
        """Only the server lifespan may recover jobs, never module import."""
        with self.connect() as db:
            for identity, raw in db.execute('SELECT id,data FROM runs').fetchall():
                value = json.loads(raw)
                if value['state'] in ('running', 'queued'):
                    value.update(state='interrupted', error='服务重启使任务中断；可重新执行，原记录保留。')
                    active=str(value.get('stage',1))
                    if active in value.get('stage_states',{}):value['stage_states'][active]['status']='interrupted'
                    db.execute('UPDATE runs SET data=? WHERE id=?', (json.dumps(value, ensure_ascii=False), identity))
                elif value['state'] == 'completed' and value.get('error'):
                    value['error'] = None
                    db.execute('UPDATE runs SET data=? WHERE id=?', (json.dumps(value, ensure_ascii=False), identity))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT data FROM runs WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise HTTPException(404, '未找到新版任务。')
        return json.loads(row[0])

    def update(self, identity, **fields):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            value = json.loads(db.execute('SELECT data FROM runs WHERE id=?', (identity,)).fetchone()[0])
            value.update(fields)
            value['updated_at'] = datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE runs SET data=? WHERE id=?', (json.dumps(value, ensure_ascii=False), identity))
        return value

    def list(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM runs ORDER BY rowid DESC')]

    def catalog(self):
        result = {}
        # Discovery is limited to completed deliveries in project-managed roots.
        for base in (self.root/'tmp/label_lengths', self.folder):
            if not base.exists():
                continue
            for path in sorted(base.rglob('stage_two.json')):
                if not path.resolve().is_relative_to(base.resolve()):
                    continue
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if data.get('format') != 'label-lengths/v1' or not data.get('views') or any(
                            v['state'] not in ('completed', 'empty') for v in data['views']):
                        continue
                    identity = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
                    snapshot = json.loads((path.parent/'stage_one_facts.snapshot.json').read_text(encoding='utf-8'))
                    result[identity] = {'id': identity, 'name': path.parent.relative_to(self.root).as_posix(),
                        'path': str(path.parent.resolve()), 'summary': data.get('summary', {}),
                        'source_sha256': snapshot['source_sha256'], 'updated_at': path.stat().st_mtime,
                        'view_ids': [v['view_id'] for v in data['views']],
                        'views': [v['title'] for v in data['views']]}
                except (ValueError, KeyError, OSError):
                    continue
        return result

    def create(self, request_id, mode, inputs, settings, *, auto_run=True, stop_after_stage=3):
        # Repeated browser submissions with the same request ID never pay twice.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT data FROM runs WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                old = json.loads(existing[0])
                if (old['mode'] != mode or old['inputs'] != inputs or old['settings'] != settings
                        or old.get('auto_run',True)!=auto_run
                        or old.get('stop_after_stage',3)!=min(stop_after_stage,3)):
                    raise HTTPException(409, '此请求编号已用于不同输入，请刷新后重试。')
                return old, False
            identity = uuid.uuid4().hex
            value = {'id': identity, 'mode': mode, 'state': 'queued', 'stage': 0, 'inputs': inputs,
                'settings': settings, 'created_at': datetime.now(timezone.utc).isoformat(),
                'stages': [], 'error': None, 'algorithm': 'cross-view-entities/v2-matching-only'}
            value.update(auto_run=auto_run, stop_after_stage=min(stop_after_stage,3),
                         next_stage=1 if mode=='PDF' else 3, stage_states={}, history=[])
            db.execute('INSERT INTO runs VALUES (?,?,?)', (identity, request_id, json.dumps(value, ensure_ascii=False)))
        (self.folder/identity).mkdir()
        return value, True

    def launch(self, identity):
        if self.start_workers:
            threading.Thread(target=self.worker, args=(identity,), daemon=True).start()

    def worker(self, identity):
        with self.queue:
            job = self.get(identity)
            if job['state'] != 'queued':
                return
            folder = self.folder/identity
            audit = AuditLog(folder,secrets=(os.getenv('LLM_API_KEY'),self.credentials.get(identity,{}).get('api_key')))
            emit = lambda message: audit.event('PROGRESS', message=str(message))
            self.update(identity, state='running', error=None)
            audit.event('WEB_RUN_STARTED', mode=job['mode'], settings=job['settings'])
            started = time.monotonic()
            stages = list(job['stages'])
            stage_states = dict(job.get('stage_states',{}))
            settings = {**job['settings'], **self.credentials.get(identity,{})}
            task_limit = task_concurrency(settings)
            view_slots = TaskSlots(task_limit)
            audit.event('CONCURRENCY_POLICY',task_limit=task_limit,requests_per_view=view_concurrency(settings),
                        max_stage_two_requests=task_limit*view_concurrency(settings))
            working_outputs = dict(job.get('current_outputs',{}))
            resume = job.get('resume_saved',False)
            def completed(number, directory, summary, index=None):
                stages.append({'stage':number, 'input_index':index,
                    'directory':directory.relative_to(folder).as_posix(), 'summary':summary})
                stages.sort(key=lambda e:(e['stage'],e.get('input_index') if e.get('input_index') is not None else -1))
                if number==2: working_outputs.pop(str(index),None)
                self.update(identity, stages=stages,current_output=None,current_outputs=working_outputs)
            def output(number, index=None):
                revision = stage_states[str(number)]['revision']
                suffix = f'/p{index+1:03d}' if index is not None else ''
                return folder/f's{number}v{revision}-{uuid.uuid4().hex[:6]}{suffix}'
            try:
                for number in range(job.get('next_stage') or (1 if job['mode']=='PDF' else 3),4):
                    previous = native_stages.metadata({**job,'stage_states':stage_states},number)
                    stage_states[str(number)] = {'revision':previous['revision'],'status':'running'}
                    self.update(identity,stage=number,next_stage=number,stage_states=stage_states)
                    emit(f'正在执行第 {number} 阶段。')
                    if number==1:
                        from app.label_catalog.run import run as catalog
                        existing = {entry.get('input_index',index):entry for index,entry in enumerate(native_stages.records({'stages':stages},1))}
                        failures=[]
                        with ThreadPoolExecutor(max_workers=task_limit,thread_name_prefix='catalog-file') as pool:
                            futures={}
                            for index, source in enumerate(job['inputs']):
                                if resume and index in existing:
                                    from .label_facts import load
                                    saved=load(folder/existing[index]['directory']/'stage_one_facts.json')
                                    if saved['source_sha256']!=hashlib.sha256(Path(source).read_bytes()).hexdigest():
                                        raise ValueError('PDF 与已保存的第一阶段来源指纹不符，不能复用。')
                                    emit(f'复用已校验第一阶段：{Path(source).name}。')
                                    continue
                                one=output(1,index)
                                futures[pool.submit(catalog,source,one,settings,
                                    lambda message,i=index:emit(f'文件{i+1} · {message}'))]=(index,one)
                            for future in as_completed(futures):
                                index,one=futures[future]
                                try:
                                    report=future.result()
                                    completed(1,one,report['summary'],index)
                                except Exception as error:
                                    failures.append(f'文件{index+1}：{audit.scrub(str(error))}')
                        if failures: raise ValueError('第一阶段部分文件失败；已完成结果保留。'+ '；'.join(failures))
                    elif number==2:
                        local_job = {**job,'stages':stages,'stage_states':stage_states}
                        prepared=[]
                        for source in native_stages.sources(self,local_job):
                            index = source['index']
                            emit(f'第二阶段 · 第 {index+1}/{len(job["inputs"])} 份文件。')
                            existing = next((e for i,e in enumerate(native_stages.records(local_job,2))
                                if e.get('input_index',i)==index),None)
                            if resume and existing:
                                read_inputs([folder/existing['directory']])
                                emit(f'复用已校验第二阶段：第 {index+1} 份文件。')
                                continue
                            facts = source['facts']
                            if source.get('snapshot'):
                                # A MATCH job also has a complete immutable facts
                                # snapshot. Materialize its original image names.
                                from .native_pdf import write_json
                                owned = output(1,index); owned.mkdir(parents=True)
                                bundle = json.loads(facts.read_text(encoding='utf-8'))
                                two = json.loads((source['two']/'stage_two.json').read_text(encoding='utf-8'))
                                images = {v['view_id']:v['image_file'] for v in two['views']}
                                for view in bundle['views']:
                                    if Path(view['image_file']).name!=view['image_file']:
                                        raise ValueError('第一阶段图片名称不能包含目录。')
                                    shutil.copy2(source['two']/images[view['view_id']],owned/view['image_file'])
                                facts = owned/'stage_one_facts.json'; write_json(facts,bundle)
                            legacy = folder/job.get('resume_outputs',{}).get(str(index),f'pdf{index+1:03d}/stage_two')
                            previous_manifest=native_stages.read(legacy/'input_manifest.json')
                            bundle=json.loads(facts.read_text(encoding='utf-8'))
                            reusable=resume and previous_manifest==length_manifest(bundle,bundle['views'],settings)
                            two = legacy if reusable else output(2,index)
                            prepared.append((index,facts,two,reusable))
                            working_outputs[str(index)]={'stage':2,'input_index':index,
                                'directory':two.relative_to(folder).as_posix()}
                        self.update(identity,current_output=None,current_outputs=working_outputs)
                        failures=[]
                        with ThreadPoolExecutor(max_workers=task_limit,thread_name_prefix='length-file') as pool:
                            futures={pool.submit(lengths,facts,two,settings,
                                lambda message,i=index:emit(f'文件{i+1} · {message}'),
                                call_api=True,resume=reusable,task_slots=view_slots):(index,two)
                                for index,facts,two,reusable in prepared}
                            for future in as_completed(futures):
                                index,two=futures[future]
                                try:
                                    report=future.result()
                                    completed(2,two,report['summary'],index)
                                except Exception as error:
                                    failures.append(f'文件{index+1}：{audit.scrub(str(error))}')
                        audit.event('STAGE_TWO_TASK_CONCURRENCY',**view_slots.counts(),requests_per_view=view_concurrency(settings))
                        if failures: raise ValueError('第二阶段部分文件失败；已完成结果保留。'+ '；'.join(failures))
                    else:
                        paths = ([folder/e['directory'] for e in native_stages.records({'stages':stages},2)]
                                 if native_stages.records({'stages':stages},2) else [Path(p) for p in job['inputs']])
                        directory = folder/'stage_three' if not (folder/'stage_three').exists() else output(3)
                        report = entities(paths,directory,settings,emit,call_api=True)
                        from tools.verify_entities import verify
                        verification = verify(directory)
                        (directory/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
                        completed(3,directory,report['summary'])
                        self.update(identity,summary=report['summary'],verification=verification)
                    advance = job.get('auto_run',True) and number < job.get('stop_after_stage',3)
                    finished = number==3 and job.get('auto_run',True)
                    stage_states[str(number)]['status'] = 'approved' if advance or finished else 'awaiting_review'
                    self.update(identity,stage_states=stage_states,stages=stages,
                        state='running' if advance else ('completed' if finished else 'awaiting_review'),
                        elapsed_seconds=round(time.monotonic()-started,3),error=None)
                    emit(f'第 {number} 阶段完成。'+('自动继续下一阶段。' if advance else ('已导出台账。' if finished else '等待阶段确认。')))
                    if not advance:
                        break
                audit.event('WEB_RUN_COMPLETED' if self.get(identity)['state']=='completed' else 'WEB_STAGE_PAUSED',
                            stage=self.get(identity)['stage'])
            except Exception as error:
                message = audit.scrub(str(error))
                if stage_states:
                    stage_states[str(self.get(identity)['stage'])]['status']='failed'
                self.update(identity,state='failed',error=message,stage_states=stage_states)
                audit.event('WEB_RUN_FAILED', error=message, error_type=type(error).__name__)

    def control(self, identity, number, body, action):
        if number not in (1,2,3):
            raise HTTPException(404,'阶段不存在。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM runs WHERE id=?',(identity,)).fetchone()
            if not row:raise HTTPException(404,'未找到任务。')
            job = json.loads(row[0]); previous=native_stages.metadata(job,number)
            if previous['revision'] != body.revision:
                raise HTTPException(409,'阶段版本已改变，请刷新后再操作。')
            if action=='approve' and previous['status']=='approved':return job,False
            if job['state'] in ('running','queued'):
                raise HTTPException(409,'任务仍在执行，不能修改阶段状态。')
            states=dict(job.get('stage_states',{})); history=list(job.get('history',[]))
            history.append({'at':datetime.now(timezone.utc).isoformat(),'stage':number,
                'revision':body.revision,'action':action,'reason':body.reason,
                'stages':job['stages']})
            if action=='approve':
                if previous['status']!='awaiting_review':raise HTTPException(409,'只有等待确认的阶段可以继续。')
                states[str(number)]={'revision':body.revision,'status':'approved'}
                next_stage=min(number+1,3)
                state='completed' if number==3 else 'queued'
                job['stop_after_stage']=max(job.get('stop_after_stage',3),next_stage)
            else:
                if job['mode']=='MATCH' and number==1:raise HTTPException(409,'此任务没有原始PDF，可从第二或第三阶段重新执行。')
                resume=body.reuse_saved and job['state'] in ('failed','interrupted')
                job['resume_saved']=resume
                working=job.get('current_output') or {}
                if resume:
                    for index,entry in job.get('current_outputs',{}).items():
                        job.setdefault('resume_outputs',{})[index]=entry['directory']
                if resume and working.get('stage')==2:
                    job.setdefault('resume_outputs',{})[str(working['input_index'])]=working['directory']
                states[str(number)]={'revision':body.revision+1,'status':'queued'}
                for later in range(number+1,4):
                    old=native_stages.metadata(job,later)
                    states[str(later)]={'revision':old['revision']+1,'status':'stale'}
                if not resume:job['stages']=[e for e in job['stages'] if e['stage']<number]
                next_stage=number; state='queued'
                if resume and job['mode']=='PDF' and len(native_stages.records(job,1))<len(job['inputs']):
                    next_stage=1
            for key in ('reasoning_effort','max_concurrency','max_task_concurrency','max_calls'):
                value=body.settings.get(key)
                if value not in (None,''):job['settings'][key]=value
            if job['settings'].get('reasoning_effort','high') not in ('low','high','max'):
                raise HTTPException(422,'思考强度只能是 low、high 或 max。')
            try:
                view_concurrency(job['settings']); task_concurrency(job['settings']); option(job['settings'],'max_calls','ENTITY_MAX_CALLS',24)
            except ValueError as error:raise HTTPException(422,str(error)) from error
            if body.auto_run is not None:job['auto_run']=body.auto_run
            job.update(state=state,next_stage=next_stage,error=None,history=history,stage_states=states,current_output=None,current_outputs={},
                       updated_at=datetime.now(timezone.utc).isoformat())
            db.execute('UPDATE runs SET data=? WHERE id=?',(json.dumps(job,ensure_ascii=False),identity))
        return job,state=='queued'


def router(root, start_workers=True):
    service = NativeRuns(root, start_workers)
    api = APIRouter()

    @api.get('/workflow', include_in_schema=False)
    @api.get('/workflow/', include_in_schema=False)
    def page(run: str = ''):
        from urllib.parse import urlencode
        return RedirectResponse('/?'+urlencode({'job':run}) if run else '/',status_code=307)

    @api.get('/api/native/sources')
    def sources():
        return [{k: v for k, v in row.items() if k != 'path'} for row in service.catalog().values()]

    @api.get('/api/native/capabilities')
    def capabilities():
        return {'stage_controls':True,'readonly_ledger':True,'ui':'original-stage-page',
                'concurrency_model':'per-view-v1','task_limit':task_concurrency(),
                'requests_per_view':view_concurrency()}

    @api.get('/api/native/runs')
    def runs():
        return service.list()

    @api.get('/api/native/runs/{identity}')
    def status(identity: str):
        return native_stages.status(service,identity)

    @api.get('/api/native/runs/{identity}/stages/{number}')
    def get_stage(identity: str, number: int):
        return native_stages.payload(service,identity,number)

    @api.post('/api/native/runs/{identity}/stages/{number}/{action}')
    def stage_action(identity: str, number: int, action: str, body: StageControl):
        if action not in ('approve','retry'):
            raise HTTPException(404,'阶段操作不存在。')
        private={k:v for k,v in body.settings.items() if k in ('base_url','api_key','model') and v}
        if private:
            try:_api_settings({**service.credentials.get(identity,{}),**private})
            except RuntimeError as error:raise HTTPException(422,str(error)) from error
        value, launch=service.control(identity,number,body,action)
        if private:service.credentials[identity]={**service.credentials.get(identity,{}),**private}
        if launch:service.launch(identity)
        return native_stages.status(service,identity)

    def settings(effort, concurrency, budget, private=None, tasks=None):
        value = {'reasoning_effort': effort}
        if concurrency is not None:
            value['max_concurrency'] = concurrency
        if tasks is not None:
            value['max_task_concurrency'] = tasks
        if budget is not None:
            value['max_calls'] = budget
        try:
            view_concurrency(value)
            task_concurrency(value)
            option(value, 'max_calls', 'ENTITY_MAX_CALLS', 24)
            _api_settings({**value,**(private or {})})
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error
        return value

    @api.post('/api/native/runs/{identity}/rerun')
    def rerun(identity: str, body: RunRequest):
        previous = service.get(identity)
        if previous['state'] not in ('completed','failed','interrupted'):
            raise HTTPException(409, '任务尚在运行，不能重复启动。')
        if previous['mode'] == 'MATCH':
            try:
                read_inputs(previous['inputs'])
            except (ValueError, OSError, KeyError) as error:
                raise HTTPException(422, str(error)) from error
        value, created = service.create(body.request_id, previous['mode'], previous['inputs'],
            settings(body.reasoning_effort,body.max_concurrency,body.max_calls,tasks=body.max_task_concurrency),
            auto_run=previous.get('auto_run',True),stop_after_stage=previous.get('stop_after_stage',3))
        if created:
            service.launch(value['id'])
        return value

    @api.post('/api/native/runs/match')
    def match(body: MatchRequest):
        available = service.catalog()
        if len(set(body.source_ids)) != len(body.source_ids) or any(i not in available for i in body.source_ids):
            raise HTTPException(422, '第二阶段来源不存在、未完成或重复。')
        paths = [available[i]['path'] for i in body.source_ids]
        try:
            read_inputs(paths)  # Full contract/hash verification before any request.
        except (ValueError, OSError, KeyError) as error:
            raise HTTPException(422, str(error)) from error
        value, created = service.create(body.request_id, 'MATCH', paths,
            settings(body.reasoning_effort, body.max_concurrency, body.max_calls,tasks=body.max_task_concurrency),auto_run=body.auto_run)
        if created:
            service.launch(value['id'])
        return value

    @api.post('/api/native/runs/pdf')
    def upload(files: list[UploadFile] = File(...), reasoning_effort: str = Form('high', pattern=r'^(low|high|max)$'),
               max_concurrency: int | None = Form(None, ge=1), max_calls: int | None = Form(None, ge=1),
               max_task_concurrency: int | None = Form(None, ge=1),
               auto_run: bool = Form(True), stop_after_stage: int = Form(3,ge=1,le=4),
               base_url: str = Form(''), api_key: str = Form(''), model: str = Form('')):
        private={k:v for k,v in {'base_url':base_url,'api_key':api_key,'model':model}.items() if v}
        options = settings(reasoning_effort,max_concurrency,max_calls,private,tasks=max_task_concurrency)
        if not files or any(not (f.filename or '').lower().endswith('.pdf') for f in files):
            raise HTTPException(422, '请上传 PDF 文件。')
        # Never accept a client filesystem path. Uploaded names cannot escape.
        staging = service.folder/('upload-'+uuid.uuid4().hex)
        staging.mkdir()
        try:
            paths = []
            import fitz
            for index, file in enumerate(files):
                name = Path(file.filename.replace('\\', '/')).name
                path = staging/f'{index+1:03d}_{name}'
                with path.open('wb') as target:
                    shutil.copyfileobj(file.file, target)
                with PDF_LOCK, fitz.open(stream=path.read_bytes(), filetype='pdf') as pdf:
                    if not pdf.is_pdf or pdf.needs_pass or pdf.page_count == 0:
                        raise ValueError('PDF 为空或需要密码。')
                paths.append(str(path.resolve()))
            if len({hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}) != len(paths):
                raise ValueError('重复上传同一 PDF。')
        except Exception as error:
            if not staging.resolve().is_relative_to(service.folder.resolve()):
                raise RuntimeError('上传临时目录超出任务目录') from error
            shutil.rmtree(staging)
            raise HTTPException(422, '无法读取 PDF：'+str(error)) from error
        value, _ = service.create(uuid.uuid4().hex,'PDF',paths,options,
                                  auto_run=auto_run,stop_after_stage=stop_after_stage)
        service.credentials[value['id']]=private
        service.launch(value['id'])
        return value

    @api.get('/api/native/runs/{identity}/logs')
    def logs(identity: str):
        service.get(identity)
        return AuditLog(service.folder/identity).read(0, 10000)

    @api.get('/api/native/runs/{identity}/sources/{index}/{filename:path}')
    def source_file(identity: str,index: int,filename: str):
        job=service.get(identity)
        if job['mode']!='MATCH' or not 0<=index<len(job['inputs']):raise HTTPException(404,'来源不存在。')
        folder=Path(job['inputs'][index]).resolve()
        if folder.is_file():folder=folder.parent
        path=(folder/filename).resolve()
        if not path.is_relative_to(folder) or not path.is_file() or path.suffix.lower() not in ('.png','.json','.html','.xlsx','.csv'):
            raise HTTPException(404,'来源文件不存在。')
        return FileResponse(path,headers={'Cache-Control':'no-store'})

    @api.get('/api/native/runs/{identity}/evidence-download')
    def evidence(identity: str):
        job=service.get(identity)
        if job['state']!='completed':raise HTTPException(409,'任务尚未导出完成。')
        import zipfile
        from starlette.background import BackgroundTask
        folder=service.folder/identity
        archive=folder/('evidence-'+uuid.uuid4().hex+'.zip')
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as package:
            for path in folder.rglob('*'):
                if path.is_file() and path!=archive and path.resolve().is_relative_to(folder.resolve()):
                    package.write(path,path.relative_to(folder))
        return FileResponse(archive,filename=identity+'_evidence.zip',media_type='application/zip',
                            background=BackgroundTask(archive.unlink))

    @api.get('/api/native/runs/{identity}/files/{filename:path}')
    def artifact(identity: str, filename: str):
        service.get(identity)
        folder = (service.folder/identity).resolve()
        path = (folder/filename).resolve()
        allowed = {'.html', '.png', '.json', '.csv', '.xlsx', '.txt', '.jsonl', '.zip'}
        if not path.is_relative_to(folder) or not path.is_file() or path.suffix.lower() not in allowed:
            raise HTTPException(404, '未找到此任务的文件。')
        return FileResponse(path, headers={'Cache-Control': 'no-store'})

    return api, service

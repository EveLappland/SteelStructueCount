"""Web orchestration of the same fixed-label functions used by the CLI tools.

Separate storage keeps legacy tasks readable. A single job queue prevents API
concurrency from multiplying across jobs; no scoring references enter workers.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
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
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .ai_concurrency import view_concurrency
from .audit import AuditLog
from .entity_inputs import read_inputs
from .entity_workflow import option, run as entities
from .label_length_workflow import run as lengths
from .settings import _api_settings


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')
    reasoning_effort: str = Field(default='high', pattern=r'^(low|high|max)$')
    max_concurrency: int | None = Field(default=None, ge=1)
    max_calls: int | None = Field(default=None, ge=1)


class MatchRequest(RunRequest):
    source_ids: list[str] = Field(min_length=1)


class NativeRuns:
    def __init__(self, root, start_workers=True):
        self.root = Path(root).resolve()
        self.folder = self.root/'data/native_runs'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.database = self.root/'data/native_runs.sqlite3'
        self.start_workers = start_workers
        self.queue = threading.Lock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, data TEXT NOT NULL)')

    def recover(self):
        """Only the server lifespan may recover jobs, never module import."""
        with self.connect() as db:
            for identity, raw in db.execute('SELECT id,data FROM runs').fetchall():
                value = json.loads(raw)
                if value['state'] in ('running', 'queued'):
                    value.update(state='interrupted', error='服务重启使任务中断；可重新执行，原记录保留。')
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

    def create(self, request_id, mode, inputs, settings):
        # Repeated browser submissions with the same request ID never pay twice.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT data FROM runs WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                old = json.loads(existing[0])
                if old['mode'] != mode or old['inputs'] != inputs or old['settings'] != settings:
                    raise HTTPException(409, '此请求编号已用于不同输入，请刷新后重试。')
                return old, False
            identity = uuid.uuid4().hex
            value = {'id': identity, 'mode': mode, 'state': 'queued', 'stage': 0, 'inputs': inputs,
                'settings': settings, 'created_at': datetime.now(timezone.utc).isoformat(),
                'stages': [], 'error': None, 'algorithm': 'cross-view-entities/v2-matching-only'}
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
            audit = AuditLog(folder, secrets=(os.getenv('LLM_API_KEY'),))
            emit = lambda message: audit.event('PROGRESS', message=str(message))
            self.update(identity, state='running')
            audit.event('WEB_RUN_STARTED', mode=job['mode'], settings=job['settings'])
            started = time.monotonic()
            stages = []
            def completed(number, directory, summary):
                stages.append({'stage': number, 'directory': directory.relative_to(folder).as_posix(), 'summary': summary})
                self.update(identity, stages=stages)
            try:
                if job['mode'] == 'PDF':
                    from experiments.pdf_label_tags.run import run as catalog
                    paths = []
                    # Each PDF has its own local IDs; stage 3 namespaces by hash.
                    for index, source in enumerate(job['inputs']):
                        self.update(identity, stage=1)
                        one = folder/f'pdf{index+1:03d}/stage_one'
                        report = catalog(source, one, job['settings'], emit)
                        completed(1, one, report['summary'])
                        self.update(identity, stage=2)
                        two = folder/f'pdf{index+1:03d}/stage_two'
                        report = lengths(one/'stage_one_facts.json', two, job['settings'], emit, call_api=True)
                        completed(2, two, report['summary'])
                        paths.append(two)
                else:
                    paths = [Path(p) for p in job['inputs']]
                self.update(identity, stage=3)
                directory = folder/'stage_three'
                report = entities(paths, directory, job['settings'], emit, call_api=True)
                # Verify delivered sources, assignment and exported measurements.
                from tools.verify_entities import verify
                verification = verify(directory)
                (directory/'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding='utf-8')
                completed(3, directory, report['summary'])
                self.update(identity, state='completed', error=None, summary=report['summary'],
                            elapsed_seconds=round(time.monotonic()-started, 3), verification=verification)
                audit.event('WEB_RUN_COMPLETED', summary=report['summary'])
            except Exception as error:
                message = audit.scrub(str(error))
                self.update(identity, state='failed', error=message)
                audit.event('WEB_RUN_FAILED', error=message, error_type=type(error).__name__)


def router(root, static, start_workers=True):
    service = NativeRuns(root, start_workers)
    api = APIRouter()

    @api.get('/workflow', include_in_schema=False)
    @api.get('/workflow/', include_in_schema=False)
    def page():
        version = hashlib.sha256((static/'native_workflow.js').read_bytes()).hexdigest()[:12]
        html = (static/'native_workflow.html').read_text(encoding='utf-8').replace(
            '"/native_workflow.js"', f'"/native_workflow.js?v={version}"')
        return HTMLResponse(html, headers={'Cache-Control': 'no-store'})

    @api.get('/api/native/sources')
    def sources():
        return [{k: v for k, v in row.items() if k != 'path'} for row in service.catalog().values()]

    @api.get('/api/native/runs')
    def runs():
        return service.list()

    @api.get('/api/native/runs/{identity}')
    def status(identity: str):
        value = service.get(identity)
        if (service.folder/identity/'stage_three/evaluation/index.html').is_file():
            value['evaluation_url'] = f'/api/native/runs/{identity}/files/stage_three/evaluation/index.html'
        return value

    def settings(effort, concurrency, budget):
        value = {'reasoning_effort': effort}
        if concurrency is not None:
            value['max_concurrency'] = concurrency
        if budget is not None:
            value['max_calls'] = budget
        try:
            view_concurrency(value)
            option(value, 'max_calls', 'ENTITY_MAX_CALLS', 24)
            _api_settings(value)
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
            settings(body.reasoning_effort, body.max_concurrency, body.max_calls))
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
            settings(body.reasoning_effort, body.max_concurrency, body.max_calls))
        if created:
            service.launch(value['id'])
        return value

    @api.post('/api/native/runs/pdf')
    def upload(files: list[UploadFile] = File(...), reasoning_effort: str = Form('high', pattern=r'^(low|high|max)$'),
               max_concurrency: int | None = Form(None, ge=1), max_calls: int | None = Form(None, ge=1)):
        options = settings(reasoning_effort, max_concurrency, max_calls)
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
                with fitz.open(stream=path.read_bytes(), filetype='pdf') as pdf:
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
        value, _ = service.create(uuid.uuid4().hex, 'PDF', paths, options)
        service.launch(value['id'])
        return value

    @api.get('/api/native/runs/{identity}/logs')
    def logs(identity: str):
        service.get(identity)
        return AuditLog(service.folder/identity).read(0, 10000)

    @api.get('/api/native/runs/{identity}/files/{filename:path}')
    def artifact(identity: str, filename: str):
        service.get(identity)
        folder = (service.folder/identity).resolve()
        path = (folder/filename).resolve()
        allowed = {'.html', '.png', '.json', '.csv', '.xlsx', '.txt', '.jsonl'}
        if not path.is_relative_to(folder) or not path.is_file() or path.suffix.lower() not in allowed:
            raise HTTPException(404, '未找到此任务的文件。')
        return FileResponse(path, headers={'Cache-Control': 'no-store'})

    return api, service

"""Transactional stage gates, versioned drafts, and restart recovery."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class StoreError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, state TEXT, active_stage INTEGER,
                    created_at TEXT, updated_at TEXT, result TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS stages (
                    job_id TEXT, stage INTEGER, revision INTEGER, status TEXT,
                    data TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(job_id, stage));
                CREATE TABLE IF NOT EXISTS history (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, stage INTEGER,
                    revision INTEGER, at TEXT, action TEXT, reason TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, at TEXT,
                    state TEXT, message TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    def _event(self, db, job_id, state, message):
        db.execute('UPDATE jobs SET state=?, updated_at=? WHERE id=?', (state, now(), job_id))
        db.execute('INSERT INTO events(job_id,at,state,message) VALUES(?,?,?,?)',
                   (job_id, now(), state, message))

    def _history(self, db, job_id, stage, revision, action, reason, data):
        db.execute('INSERT INTO history(job_id,stage,revision,at,action,reason,data) VALUES(?,?,?,?,?,?,?)',
                   (job_id, stage, revision, now(), action, reason, json.dumps(data, ensure_ascii=False)))

    def create(self, job_id):
        with self.transaction() as db:
            db.execute('INSERT INTO jobs(id,state,active_stage,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (job_id, 'queued', 1, now(), now()))
            db.execute('INSERT INTO stages(job_id,stage,revision,status) VALUES(?,1,1,?)', (job_id, 'queued'))
            self._event(db, job_id, 'queued', '第一阶段已加入队列。')

    def list_jobs(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT id,state,active_stage,created_at,updated_at FROM jobs ORDER BY created_at DESC LIMIT 100')]

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise StoreError('未找到任务。', 404)
            job = dict(row)
            job.update(json.loads(job.pop('result')))
            job['schema_version'] = 2
            job['events'] = [dict(r) for r in db.execute(
                'SELECT at,state,message FROM events WHERE job_id=? ORDER BY seq', (job_id,))]
            job['stages'] = [dict(r) for r in db.execute(
                'SELECT stage,revision,status FROM stages WHERE job_id=? ORDER BY stage', (job_id,))]
            return job

    def stage(self, job_id, stage):
        self.get(job_id)
        with self.connect() as db:
            row = db.execute('SELECT * FROM stages WHERE job_id=? AND stage=?', (job_id, stage)).fetchone()
            if not row:
                raise StoreError('该阶段尚未生成。', 404)
            result = dict(row)
            result['data'] = json.loads(result['data'])
            result['history'] = [dict(r) for r in db.execute(
                'SELECT revision,at,action,reason FROM history WHERE job_id=? AND stage=? ORDER BY seq',
                (job_id, stage))]
            return result

    def _checked(self, db, job_id, stage, revision):
        row = db.execute('SELECT * FROM stages WHERE job_id=? AND stage=?', (job_id, stage)).fetchone()
        if not row:
            raise StoreError('未找到阶段。', 404)
        if row['revision'] != revision:
            raise StoreError('阶段版本已改变，请刷新后再操作。')
        return row

    def claim(self, job_id, stage):
        with self.transaction() as db:
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job['state'] != 'queued' or job['active_stage'] != stage:
                return False
            if stage <= 3:
                db.execute('UPDATE stages SET status=? WHERE job_id=? AND stage=?', ('running', job_id, stage))
            self._event(db, job_id, 'running' if stage <= 3 else 'exporting',
                        f'正在执行第 {stage} 阶段。' if stage <= 3 else '正在导出已确认结果。')
            return True

    def progress(self, job_id, message):
        with self.transaction() as db:
            state = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()['state']
            self._event(db, job_id, state, message)

    def complete_stage(self, job_id, stage, data):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM stages WHERE job_id=? AND stage=?', (job_id, stage)).fetchone()
            # A retried draft with manual edits is never overwritten by new model output.
            if row['status'] != 'running':
                raise StoreError('阶段未处于执行状态。')
            saved = json.loads(row['data'])
            edited = db.execute("SELECT 1 FROM history WHERE job_id=? AND stage=? AND action='edit' LIMIT 1",
                                (job_id, stage)).fetchone()
            if edited and saved:
                self._history(db, job_id, stage, row['revision'], 'retry_proposal',
                              '重试结果保存为历史提案，人工草稿保持不变。', data)
                data = saved
            else:
                self._history(db, job_id, stage, row['revision'], 'generated', '生成阶段草稿。', data)
            db.execute('UPDATE stages SET status=?,data=? WHERE job_id=? AND stage=?',
                       ('awaiting_review', json.dumps(data, ensure_ascii=False), job_id, stage))
            self._event(db, job_id, 'awaiting_review', f'第 {stage} 阶段完成，等待修改或确认。')

    def edit(self, job_id, stage, revision, data, reason):
        with self.transaction() as db:
            row = self._checked(db, job_id, stage, revision)
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if job['state'] in ('queued', 'running', 'exporting') or row['status'] == 'stale':
                raise StoreError('执行中或已失效的阶段不能修改。')
            new_revision = revision + 1
            self._history(db, job_id, stage, revision, 'before_edit', reason, json.loads(row['data']))
            self._history(db, job_id, stage, new_revision, 'edit', reason, data)
            db.execute('UPDATE stages SET revision=?,status=?,data=? WHERE job_id=? AND stage=?',
                       (new_revision, 'awaiting_review', json.dumps(data, ensure_ascii=False), job_id, stage))
            db.execute('UPDATE stages SET status=?,revision=revision+1 WHERE job_id=? AND stage>?',
                       ('stale', job_id, stage))
            db.execute("UPDATE jobs SET active_stage=?,result='{}' WHERE id=?", (stage, job_id))
            self._event(db, job_id, 'awaiting_review', '修改已保存；下游结果已失效，确认后重新生成。')

    def approve(self, job_id, stage, revision, pending=False,reason=''):
        with self.transaction() as db:
            row = self._checked(db, job_id, stage, revision)
            if row['status'] == 'approved':
                return None  # Idempotent; never launch another thread or export twice.
            if row['status'] != 'awaiting_review':
                raise StoreError('只有等待确认的草稿可以确认。')
            self._history(db, job_id, stage, revision, 'approved',
                          ('允许待确认项继续。' if pending else '用户确认。')+reason, json.loads(row['data']))
            db.execute('UPDATE stages SET status=? WHERE job_id=? AND stage=?', ('approved', job_id, stage))
            next_stage = stage + 1
            if next_stage <= 3:
                # Preserve invalidated draft and edits as history, start a clean new revision.
                db.execute('''INSERT INTO stages(job_id,stage,revision,status) VALUES(?,?,1,'queued')
                    ON CONFLICT(job_id,stage) DO UPDATE SET status='queued',data='{}' ''', (job_id, next_stage))
            db.execute("UPDATE jobs SET active_stage=?,result='{}' WHERE id=?", (next_stage, job_id))
            self._event(db, job_id, 'queued', f'第 {stage} 阶段已确认。')
            return next_stage

    def retry(self, job_id, stage, revision):
        with self.transaction() as db:
            row = self._checked(db, job_id, stage, revision)
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            export_retry = stage == 3 and row['status'] == 'approved' and job['active_stage'] == 4
            if export_retry and job['state']=='completed':
                self._history(db,job_id,stage,row['revision'],'retry','重新执行第三阶段AI核验；保留旧结果。',json.loads(row['data']))
                db.execute("UPDATE stages SET revision=revision+1,status='queued' WHERE job_id=? AND stage=3",(job_id,))
                db.execute("UPDATE jobs SET active_stage=3,result='{}' WHERE id=?",(job_id,))
                self._event(db,job_id,'queued','重新执行第三阶段AI核验。')
                return 3
            if job['state'] not in ('failed', 'interrupted', 'awaiting_review'):
                raise StoreError('任务当前不能重试。')
            if row['status'] not in ('failed', 'interrupted', 'awaiting_review') and not export_retry:
                raise StoreError('该阶段不能重试。')
            if job['active_stage'] != (4 if export_retry else stage):
                raise StoreError('只能重试当前阶段。')
            if not export_retry:
                self._history(db,job_id,stage,row['revision'],'retry',
                              '请求重新计算；AI 阶段使用 max 思考强度。',json.loads(row['data']))
                db.execute('UPDATE stages SET revision=revision+1,status=? WHERE job_id=? AND stage=?',
                           ('queued', job_id, stage))
            db.execute("UPDATE jobs SET result='{}' WHERE id=?", (job_id,))
            self._event(db, job_id, 'queued', '已加入重试队列；保留人工草稿和修改历史。')
            return 4 if export_retry else stage

    def finish(self, job_id, result):
        with self.transaction() as db:
            db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), job_id))
            self._event(db, job_id, 'completed', '三阶段确认完成，统计结果已导出。')

    def fail(self, job_id, stage, message):
        with self.transaction() as db:
            if stage <= 3:
                db.execute('UPDATE stages SET status=? WHERE job_id=? AND stage=?', ('failed', job_id, stage))
            db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps({'error_detail': message}), job_id))
            self._event(db, job_id, 'failed', message)

    def recover(self):
        with self.transaction() as db:
            for row in db.execute("SELECT id FROM jobs WHERE state IN ('queued','running','exporting')").fetchall():
                db.execute("UPDATE stages SET status='interrupted' WHERE job_id=? AND status IN ('queued','running')",
                           (row['id'],))
                self._event(db, row['id'], 'interrupted', '服务已重启；请显式重试中断阶段。')

    def snapshots(self, job_id):
        with self.connect() as db:
            return [{**dict(row), 'data': json.loads(row['data'])} for row in db.execute(
                'SELECT stage,revision,at,action,reason,data FROM history WHERE job_id=? ORDER BY seq', (job_id,))]

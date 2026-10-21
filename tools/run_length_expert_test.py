"""Replay recorded children/master; real paid length requests, immutable source job.

Uses the production scheduler and saves a separate stage-two job. Reference
answers, if supplied, are read only AFTER all inference is complete.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from app.audit import AuditLog
from app.grid_workflow import recognize_views
from app.native_pdf import write_json
from app.store import JobStore


def score(rows,baseline):
    expected=Counter()
    for row in baseline:expected[(row['spec'],float(row['length_mm']))]+=row['member_cnt']
    actual=Counter()
    for row in rows:actual[(row['spec'],row['length_mm'])]+=row['member_cnt']
    matched=sum(min(count,actual.get(key,0)) for key,count in expected.items())
    total=sum(actual.values());standard=sum(expected.values())
    precision=matched/total if total else 0;recall=matched/standard if standard else 0
    return {'expected_count':standard,'actual_count':total,'matched':matched,'precision':precision,'recall':recall,
            'f1':2*precision*recall/(precision+recall) if precision+recall else 0,
            'criterion':'规格和两位小数名义长度完全匹配，UNKNOWN计未匹配；不校验逐根位置，不能代替工程验收'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--source-job',required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--max-concurrency',type=int,default=3)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--fresh',action='store_true',help='Fresh children/master too, instead of freezing recorded real master output')
    args=parser.parse_args();load_dotenv(ROOT/'.env')
    if not (len(args.source_job)==32 and all(c in '0123456789abcdef' for c in args.source_job)):parser.error('源任务ID无效')
    if args.baseline and not args.baseline.is_file():parser.error('找不到基准文件；不会调用API')
    args.source_root=args.source_root.resolve();args.root=args.root.resolve()
    source_store=JobStore(args.source_root/'data/jobs_v2.sqlite3')
    one=source_store.stage(args.source_job,1)['data'];before=source_store.stage(args.source_job,2)['data']
    source=args.source_root/'data/jobs'/args.source_job
    identity=uuid.uuid4().hex;destination=args.root/'data/jobs'/identity;destination.mkdir(parents=True)
    source_fingerprint=hashlib.sha256(json.dumps([one,before],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    masters=None
    if not args.fresh:
        masters={}
        for analysis in before['view_analysis']:
            path=source/analysis['trace_file']
            masters[analysis['view_id']]={'payload':json.loads((path/'master_result.json').read_text(encoding='utf-8')),
                'located':json.loads((path/'located_candidates.json').read_text(encoding='utf-8')),
                'image_sha256':json.loads((path/'input.json').read_text(encoding='utf-8'))['image_sha256'],
                'source':{'job_id':args.source_job,'master_file':str(path/'master_result.json'),
                          'master_sha256':hashlib.sha256((path/'master_result.json').read_bytes()).hexdigest()}}
    for name in ('uploads','views','native','responses','grids'):
        if (source/name).is_dir():shutil.copytree(source/name,destination/name)
    for name in ('pdf_manifest.json','native_index.json'):
        if (source/name).is_file():shutil.copy2(source/name,destination/name)
    write_json(destination/'workflow.json',{'auto_run':True,'stop_after_stage':2})
    store=JobStore(args.root/'data/jobs_v2.sqlite3');store.create(identity);store.claim(identity,1);store.complete_stage(identity,1,one)
    store.approve(identity,1,1,True,reason='实验复用已保存的第一阶段，不重新发送PDF；源任务不修改。');store.claim(identity,2)
    audit=AuditLog(destination,stage=2,secrets=(os.getenv('LLM_API_KEY'),))
    started=time.monotonic();audit.event('STAGE_STARTED',test_mode='fresh' if args.fresh else 'frozen_real_master')
    print('JOB_ID='+identity,flush=True)
    output=destination/'acceptance';output.mkdir()
    write_json(output/'before.json',before)
    try:
        after=recognize_views(destination,one,{'max_concurrency':str(args.max_concurrency),'reasoning_effort':'high'},
            lambda message:(print(message,flush=True),store.progress(identity,message)),audit,reuse_saved=False,recorded_masters=masters)
        store.complete_stage(identity,2,after)
    except Exception as error:
        store.fail(identity,2,audit.scrub(str(error)));raise
    audit.event('STAGE_AWAITING_REVIEW');write_json(output/'after.json',after)
    events=audit.read(limit=100000)['items'];active=set();peak=0;calls=Counter();reused=0
    for event in events:
        if event['event']=='MODEL_REQUEST_STARTED':active.add(event['response_file']);peak=max(peak,len(active));calls[event.get('task_kind') or 'other']+=1
        if event['event'] in ('MODEL_RESPONSE_RECEIVED','MODEL_REQUEST_FAILED'):active.discard(event['response_file'])
        if event['event']=='MODEL_RESPONSE_REUSED':reused+=1
    def identities(data):return [(r['id'],r['spec'],r['member_cnt'],r['view_tags'],r['source_view']) for r in data['view_members']]
    unchanged=source_fingerprint==hashlib.sha256(json.dumps([source_store.stage(args.source_job,1)['data'],source_store.stage(args.source_job,2)['data']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    report={'job_id':identity,'source_job':args.source_job,'mode':'fresh' if args.fresh else 'frozen_real_master_then_live_expert',
        'elapsed_seconds':round(time.monotonic()-started,2),'http_calls':dict(calls),'reused_responses':reused,'peak_http_requests':peak,
        'row_count_before':len(before['view_members']),'row_count_after':len(after['view_members']),
        'unknown_before':sum(r['length_mm'] is None for r in before['view_members']),
        'unknown_after':sum(r['length_mm'] is None for r in after['view_members']),
        'source_unchanged':unchanged,'identities_unchanged':identities(before)==identities(after),
        'views':[{k:v.get(k) for k in ('view_id','status','length_expert')} for v in after['view_analysis']]}
    if args.baseline:
        baseline=json.loads(args.baseline.read_text(encoding='utf-8'))
        report.update(before_score=score(before['view_members'],baseline),after_score=score(after['view_members'],baseline),
                      baseline_read_after_inference=True)
    report['checks']={'source_unchanged':unchanged,'concurrency_respected':peak<=args.max_concurrency,
        'length_expert_requested':calls['length_expert']>0,
        'length_expert_completed':all((v.get('length_expert') or {}).get('status') in ('completed','completed_with_warnings','skipped') for v in after['view_analysis']),
        'no_third_stage':len(store.get(identity)['stages'])==2}
    if not args.fresh:report['checks']['identities_unchanged']=report['identities_unchanged']
    write_json(output/'report.json',report)
    (output/'report.md').write_text('# 长度专家真实 API 测试\n\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    if not all(report['checks'].values()):raise RuntimeError('检查未全部通过，查看独立任务 acceptance/report.json')


if __name__=='__main__':main()

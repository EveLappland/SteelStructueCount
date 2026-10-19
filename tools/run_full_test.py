"""Fresh full PDF job through the public API, then the supplied judge.py."""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import httpx

ROOT=Path(__file__).resolve().parents[1]
JUDGE=ROOT/'tools/judge.py'


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def assess(baseline,candidate,output):
    """Read reference answers only after every inference and export has finished."""
    output.mkdir(exist_ok=True)
    args=[sys.executable,str(JUDGE),'--baseline',str(baseline),'--candidate',str(candidate),
          '--tol','1.0','--csv',str(output/'diff.csv')]
    result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',
                          env={**os.environ,'PYTHONUTF8':'1'})
    (output/'judge.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode not in (0,1) or '总体结果' not in result.stdout:
        raise RuntimeError('judge.py 未完成评分，查看 '+str(output/'judge.txt'))
    # Serialize the unmodified judge's own functions, without a second metric.
    spec=importlib.util.spec_from_file_location('acceptance_judge',JUDGE)
    judge=importlib.util.module_from_spec(spec);spec.loader.exec_module(judge)
    expected=judge.load_baseline(str(baseline))
    actual=judge.load_candidate(str(candidate),'Quantity Length Summary')
    matched,missing,extra=judge.match_multiset(expected,actual,1.0)
    summary={'baseline_count':len(expected),'candidate_count':len(actual),'matched':len(matched),
             'missing':len(missing),'extra_or_wrong_length':len(extra),
             'precision':len(matched)/len(actual) if actual else None,
             'recall':len(matched)/len(expected) if expected else None,
             'tolerance_mm':1.0,'include_plate':False,'judge_exit_code':result.returncode,
             'by_spec':judge.summarize_by_spec(expected,actual,matched)}
    write(output/'score.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-job',required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--base',default='http://127.0.0.1:8120')
    parser.add_argument('--resume-job',help='Only monitor/score an already created test job')
    args=parser.parse_args()
    for identity in (args.source_job,args.resume_job):
        if identity and not re.fullmatch('[a-f0-9]{32}',identity):parser.error('无效任务 ID')
    pdfs=sorted((ROOT/'data/jobs'/args.source_job/'uploads').glob('*.pdf'))
    if not pdfs or not args.baseline.is_file():parser.error('找不到源PDF或基准文件')
    started=time.monotonic()
    with httpx.Client(base_url=args.base,timeout=60) as client:
        def get(path):
            result=client.get(path);result.raise_for_status();return result.json()
        if args.resume_job:
            identity=args.resume_job
        else:
            with ExitStack() as stack:
                files=[('files',(re.sub(r'^\d{3}_','',p.name),stack.enter_context(p.open('rb')),'application/pdf')) for p in pdfs]
                result=client.post('/api/jobs',files=files,data={'auto_run':'true','label_color_mode':'original'})
                result.raise_for_status();identity=result.json()['id']
        folder=ROOT/'data/jobs'/identity
        output=folder/'acceptance';output.mkdir(exist_ok=True)
        report={'job_id':identity,'source_job':args.source_job,'input_files':[p.name for p in pdfs],
                'started_at':datetime.now(timezone.utc).isoformat(),'reference_sent_to_api':False,
                'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                'input_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pdfs}}
        write(output/'run.json',report)
        print('JOB_ID='+identity,flush=True)
        previous=None
        while True:
            job=get('/api/jobs/'+identity)
            state=(job['state'],job['active_stage'])
            if state!=previous:
                print(f'state={state[0]} stage={state[1]} elapsed={time.monotonic()-started:.1f}s',flush=True);previous=state
            if job['state']=='completed':break
            if job['state'] in ('failed','interrupted','awaiting_review'):
                report.update(state=job['state'],error=job.get('error_detail'))
                write(output/'run.json',report)
                raise RuntimeError('完整流程未完成，保留任务 '+identity+'：'+str(job.get('error_detail')))
            if time.monotonic()-started>3600:raise TimeoutError('任务仍在运行，使用 --resume-job 继续监控')
            time.sleep(5)
        events=get('/api/jobs/'+identity+'/logs?limit=1000')['items']
    durations={}
    for stage in range(1,5):
        scoped=[e for e in events if e['stage']==stage]
        begin=next((e for e in scoped if e['event']=='STAGE_STARTED'),None)
        end=next((e for e in reversed(scoped) if e['event'] in ('STAGE_AWAITING_REVIEW','EXPORT_COMPLETED')),None)
        if begin and end:durations[str(stage)]=(datetime.fromisoformat(end['at'])-datetime.fromisoformat(begin['at'])).total_seconds()
    report.update(state='completed',elapsed_seconds=round(time.monotonic()-started,3),stage_seconds=durations,
                  requests_by_stage=dict(Counter(str(e['stage']) for e in events if e['event']=='MODEL_REQUEST_STARTED')),
                  baseline=str(args.baseline.resolve()),baseline_sha256=hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
                  judge_sha256=hashlib.sha256(JUDGE.read_bytes()).hexdigest(),
                  score=assess(args.baseline,folder/'output/steel_takeoff.xlsx',output))
    old=ROOT/'data/jobs'/args.source_job/'output/steel_takeoff.xlsx'
    if old.is_file():report['previous_score']=assess(args.baseline,old,output/'previous')
    write(output/'run.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()

"""Explicit real-input FLOW verification, never engineering certification."""
import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.native_pdf import write_json


def request(base,path,body=None):
    payload=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(base+path,data=payload,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response:
        return json.loads(response.read())


def verify(base,identity,allow_pending):
    directory=Path(__file__).resolve().parents[1]/'data'/'jobs'/identity
    last=None
    while True:
        job=request(base,f'/api/jobs/{identity}')
        key=(job['state'],job['active_stage'])
        if key!=last:
            print(f"state={job['state']} stage={job['active_stage']}",flush=True)
            last=key
        if job['state'] in ('failed','interrupted'):
            write_json(directory/'flow_verification.json',{'flow_passed':False,'state':job['state'],
                       'error':job.get('error_detail'),'engineering_confirmed':False})
            return 1
        if job['state']=='awaiting_review':
            stage=request(base,f"/api/jobs/{identity}/stages/{job['active_stage']}")
            if not allow_pending:
                print('Paused: use --allow-pending to explicitly test stage approval.',flush=True)
                return 2
            request(base,f"/api/jobs/{identity}/stages/{job['active_stage']}/approve",
                    {'revision':stage['revision'],'acknowledge_pending':True,
                     'reason':'[FLOW_VERIFICATION] 真实输入的程序流程验证，待确认项仍排除；不是工程确认。'})
        elif job['state']=='completed':
            counts={}
            for number in (1,2,3):
                stage=request(base,f'/api/jobs/{identity}/stages/{number}')
                data=stage['data']
                counts[str(number)]={name:len(data.get(name,[])) for name in
                                     ('views','observations','candidates','members','exceptions','warnings')}
            report={'flow_passed':True,'engineering_confirmed':False,'at':datetime.now(timezone.utc).isoformat(),
                    'job_id':identity,'stages':counts,'confirmed_piece_count':job.get('total'),
                    'unresolved':job.get('unresolved'),'workbook_exists':(directory/'output'/'steel_takeoff.xlsx').is_file(),
                    'archive_exists':(directory/'output'/'high_resolution_views.zip').is_file()}
            report['takeoff_nonempty']=bool(job.get('total'))
            write_json(directory/'flow_verification.json',report)
            print(json.dumps(report,ensure_ascii=False),flush=True)
            return 0 if report['workbook_exists'] and report['archive_exists'] else 1
        time.sleep(5)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--base',default='http://127.0.0.1:8120')
    parser.add_argument('--allow-pending',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{32}',args.job_id):parser.error('job-id must be a task UUID hex string')
    raise SystemExit(verify(args.base.rstrip('/'),args.job_id,args.allow_pending))

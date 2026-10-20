"""Test a max-effort grouping proposal without mutating the source job."""
import argparse,json,sqlite3,sys,uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.audit import AuditLog
from app.group_proposal import propose_groups
from app.settings import _api_settings
from app.native_pdf import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id',required=True)
    args=parser.parse_args()
    if len(args.job_id)!=32 or any(c not in '0123456789abcdef' for c in args.job_id):parser.error('无效任务ID')
    with sqlite3.connect((ROOT/'data/jobs_v2.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        record=db.execute('SELECT revision,data FROM stages WHERE job_id=? AND stage=2',(args.job_id,)).fetchone()
    if not record:parser.error('没有第二阶段结果')
    two=json.loads(record[1])
    if two.get('recognition_mode')!='view_ledger':parser.error('只支持逐视图台账')
    output=ROOT/'tmp/group_reconciliation'/args.job_id/uuid.uuid4().hex[:8]
    output.mkdir(parents=True)
    load_dotenv(ROOT/'.env');_,key,_=_api_settings({})
    result=propose_groups(two,output,{},lambda message:print(message,flush=True),AuditLog(output,3,secrets=(key,)))
    result.update(job_id=args.job_id,stage_two_revision=record[0],mutates_job=False)
    write_json(output/'report.json',result)
    print(json.dumps({'output':str(output),'validation':result['validation']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""Run one view against the real API using a saved stage-one catalog; no task mutation."""
import argparse
import json
import sqlite3
import struct
import sys
import uuid
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.pipeline import load_native,model_call
from app.member_recognition import MemberResponse,make_input,recognition_prompt,validate_rows
from app.drawing_input import render_png
from app.native_pdf import write_json
from app.llm_runner import _api_settings
from app.audit import AuditLog


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--view-title',required=True)
    parser.add_argument('--regions',type=Path,help='已核对的视图定位 report.json，按标题读取 final_bbox')
    args=parser.parse_args()
    if len(args.job_id)!=32 or any(c not in '0123456789abcdef' for c in args.job_id):
        parser.error('job-id 必须是32位十六进制任务ID')
    with sqlite3.connect((ROOT/'data/jobs_v2.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        record=db.execute('SELECT data,revision FROM stages WHERE job_id=? AND stage=1',(args.job_id,)).fetchone()
    if not record:parser.error('任务不存在第一阶段目录')
    one=json.loads(record[0])
    found=[v for v in one['views'] if v['title']==args.view_title]
    if len(found)!=1:parser.error('标题必须唯一对应第一阶段中的非概览视图')
    view=dict(found[0])
    if view['kind']=='ISOMETRIC':parser.error('概览不统计构件')
    if args.regions:
        regions=json.loads(args.regions.read_text(encoding='utf-8'))
        matched=[r for r in regions['views'] if r['title']==view['title']]
        if len(matched)!=1:parser.error('定位报告中没有唯一匹配的视图')
        view['bbox']=matched[0]['final_bbox']
    elif not view.get('region_confirmed',True):
        parser.error('第一阶段区域尚未定位，需要 --regions 指定已验收报告')
    folder=ROOT/'data/jobs'/args.job_id
    entry=next(e for e in json.loads((folder/'pdf_manifest.json').read_text(encoding='utf-8')) if e['id']==view['page_id'])
    page=next(p for p in load_native(folder) if p['id']==view['page_id'])
    bundle=make_input(page,view,one)
    bundle['stage_one_revision']=record[1]
    image=render_png({**entry,'crop':view['bbox']},dpi=300)
    width,height=struct.unpack('>II',image[16:24])
    bundle['image']={'width_px':width,'height_px':height,'dpi':300,'format':'PNG'}
    output=ROOT/'tmp/member_recognition'/f"{view['id']}-{uuid.uuid4().hex[:8]}"
    output.mkdir(parents=True)
    (output/'view_300dpi.png').write_bytes(image)
    write_json(output/'input.json',bundle)
    load_dotenv(ROOT/'.env')
    base,key,model=_api_settings({})
    audit=AuditLog(output,stage=2,secrets=(key,))
    payload=model_call(OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1),model,
                       recognition_prompt(bundle),{**entry,'id':view['id'],'crop':view['bbox'],'image_dpi':300},
                       output/'responses/attempt-live/members.json',MemberResponse,
                       emit=lambda text:print(text,flush=True),audit=audit)
    rows=validate_rows(payload['members'],view,one)
    report={'job_id':args.job_id,'stage_one_revision':record[1],'view_id':view['id'],'title':view['title'],
            'members':rows,'row_count':len(rows),'member_cnt':sum(r['member_cnt'] for r in rows),
            'label_count':len(bundle['profile_labels']),'model':model,'deduplicated':False,
            'note':'结构校验与API连通测试，尚未人工确认识别准确性；不修改原任务。'}
    write_json(output/'report.json',report)
    print(json.dumps({k:report[k] for k in ('title','row_count','member_cnt','label_count','model')},ensure_ascii=False))
    print(f'结果目录：{output}')


if __name__=='__main__':main()

"""Paid public-API smoke test: PDF -> stage one -> grid stage two -> pause.

No artificial model replies, manual row fixes, stage three or reference answers.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
import httpx
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from app.ai_concurrency import view_concurrency


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://127.0.0.1:8122')
    parser.add_argument('--pdf',type=Path,action='append',default=[])
    parser.add_argument('--max-concurrency',type=int,default=None,help='留空使用.env的LLM_MAX_CONCURRENCY，默认5')
    parser.add_argument('--resume-job')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    args.max_concurrency=view_concurrency({'max_concurrency':args.max_concurrency})
    if not args.resume_job and (not args.pdf or not all(p.is_file() for p in args.pdf)):
        parser.error('指定存在的 --pdf 文件，或者 --resume-job')
    # Local test traffic must not be routed through the user's API proxy.
    with httpx.Client(base_url=args.base,timeout=60,trust_env=False) as client:
        if args.resume_job:identity=args.resume_job
        else:
            with ExitStack() as stack:
                files=[('files',(p.name,stack.enter_context(p.open('rb')),'application/pdf')) for p in args.pdf]
                response=client.post('/api/jobs',files=files,data={'auto_run':'true','stop_after_stage':'2',
                    'max_concurrency':str(args.max_concurrency),'label_color_mode':'original'})
                response.raise_for_status();identity=response.json()['id']
        write(args.output/'input.json',{'job_id':identity,'url':args.base+'/?job='+identity,
              'files':[{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in args.pdf],
              'stop_after_stage':2,'max_concurrency':args.max_concurrency})
        print('JOB_ID='+identity,flush=True)
        previous=None;began=time.monotonic()
        while True:
            response=client.get('/api/jobs/'+identity);response.raise_for_status();job=response.json()
            state=(job['state'],job['active_stage'],job['events'][-1]['message'] if job['events'] else '')
            if state!=previous:print(json.dumps(state,ensure_ascii=False),flush=True);previous=state
            if job['active_stage']>2:raise RuntimeError('测试不应进入第三阶段')
            if job['state'] in ('failed','interrupted'):raise RuntimeError('任务失败；'+str(job.get('error_detail')))
            if job['state']=='awaiting_review' and job['active_stage']==2:break
            if time.monotonic()-began>7200:raise TimeoutError('等待超过两小时；可用 --resume-job 继续监控')
            time.sleep(3)
        one=client.get(f'/api/jobs/{identity}/stages/1').json()['data']
        two=client.get(f'/api/jobs/{identity}/stages/2').json()['data']
        write(args.output/'stage_one.json',one);write(args.output/'stage_two.json',two)
        events=[];cursor=0
        while True:
            response=client.get(f'/api/jobs/{identity}/logs',params={'after':cursor,'limit':1000});response.raise_for_status()
            page=response.json();events.extend(page['items']);cursor=page['next_cursor']
            if cursor>=page['total']:break
    write(args.output/'events.json',events)
    active=set();peak=0;calls={};durations={}
    for event in sorted(events,key=lambda e:e['at']):
        stage=str(event['stage'])
        if event['event']=='MODEL_REQUEST_STARTED':
            calls[stage]=calls.get(stage,0)+1
            active.add(event['response_file']);peak=max(peak,len(active))
        if event['event'] in ('MODEL_RESPONSE_RECEIVED','MODEL_REQUEST_FAILED'):
            active.discard(event['response_file'])
    for stage in (1,2):
        start=next(e for e in events if e['stage']==stage and e['event']=='STAGE_STARTED')
        finish=next(e for e in reversed(events) if e['stage']==stage and e['event']=='STAGE_AWAITING_REVIEW')
        durations[str(stage)]=(datetime.fromisoformat(finish['at'])-datetime.fromisoformat(start['at'])).total_seconds()
    report={'job_id':identity,'url':args.base+'/?job='+identity,'status':'completed_stage12',
            'stop_after_stage':job['stop_after_stage'],'stages_created':[s['stage'] for s in job['stages']],
            'stage_seconds':durations,'requests_by_stage':calls,'peak_http_requests':peak,
            'configured_concurrency':args.max_concurrency,'first_stage':one['catalog_summary'],
            'views':two['view_analysis'],'member_records':len(two['view_members']),
            'member_count':sum(r['member_cnt'] for r in two['view_members']),
            'unknown_length_records':sum(r['length_mm'] is None for r in two['view_members']),
            'unknown_specialist_called':any(e.get('task_kind')=='length_expert' for e in events if e['event']=='MODEL_REQUEST_STARTED'),
            'warnings':two['warnings']}
    report['checks']={'stopped_after_two':report['stages_created']==[1,2],
        'all_views_processed':bool(two['view_analysis']) and all(v['status'] in ('completed','completed_with_warnings','empty') for v in two['view_analysis']),
        'counts_available':all((v.get('quantity_reference') or {}).get('total_count') is not None for v in one['views']),
        'request_limit_respected':peak<=args.max_concurrency,
        'source_planes_preserved':all(r['source_plane_tag'] in r['view_tags'] for r in two['view_members']),
        'grid_mode_used':two.get('recognition_strategy')=='grid_agents',
        'length_expert_finished':all((a.get('length_expert') or {}).get('status') in ('completed','completed_with_warnings','failed','skipped') for a in two['view_analysis']) if two.get('grid_configuration',{}).get('length_expert') else not report['unknown_specialist_called'],
        'automatic_arbitration_finished':all((a.get('length_arbitration') or {}).get('status') in ('completed','completed_with_warnings') for a in two['view_analysis'] if (a.get('length_expert') or {}).get('target_count',0)>0) if two.get('grid_configuration',{}).get('automatic_length_arbitration') else True}
    write(args.output/'report.json',report)
    lines=['# 第一、二阶段真实 API 联调','',f"任务：{report['url']}",'',
        f"第一阶段 {durations['1']:.2f} 秒；第二阶段 {durations['2']:.2f} 秒。",
        f"请求数：{calls}；实际同时请求峰值 {peak} / 上限 {args.max_concurrency}。",
        f"逐根/组合记录 {report['member_records']} 条；未知长度 {report['unknown_length_records']} 条；长度专家调用 {report['unknown_specialist_called']}。",'',
        '| 检查 | 结果 |','|---|---|',*[f'| {key} | {value} |' for key,value in report['checks'].items()],
        '', '| 视图 | 网格 | 第一阶段数量标准 | 第二阶段数量 | 数量匹配 | 模型请求数 | 状态 |',
        '|---|---:|---:|---:|---|---:|---|']
    names={v['id']:v['title'] for v in one['views']}
    for view in two['view_analysis']:
        lines.append(f"| {names[view['view_id']]} | {view.get('grid_count')} | {view.get('label_count')} | {view['member_cnt']} | {view.get('quantity_reference_match')} | {view.get('model_calls')} | {view['status']} |")
    lines+=['','数量差异与 UNKNOWN 保留；没有读取人工答案或增删模型记录。此测试检查流程，不宣称图纸识别准确率。']
    (args.output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('url','requests_by_stage','peak_http_requests','member_records','unknown_length_records','checks')},ensure_ascii=False,indent=2),flush=True)
    if not all(report['checks'].values()):raise RuntimeError('联调检查存在未通过项，请查看 report.json')


if __name__=='__main__':main()

"""Local HTTP benchmark using frozen task images; never calls a paid AI API."""
import argparse,copy,hashlib,json,shutil,sqlite3,sys,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import httpx
from openai import OpenAI

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.ai_concurrency import view_concurrency
from app.audit import AuditLog
from app.image_workflow import recognize_views
from app.native_pdf import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--views',type=int,default=3)
    parser.add_argument('--limits',default='1,3')
    parser.add_argument('--delay',type=float,default=1,help='本地模拟每个请求等待秒数，默认1')
    args=parser.parse_args()
    if len(args.job_id)!=32 or any(c not in '0123456789abcdef' for c in args.job_id):parser.error('无效任务ID')
    if args.views<1 or not 0<args.delay<=10:parser.error('views必须为正数，delay需在0至10秒之间')
    try:limits=list(dict.fromkeys(view_concurrency({'max_concurrency':v}) for v in args.limits.split(',')))
    except ValueError as error:parser.error(str(error))
    with sqlite3.connect((ROOT/'data/jobs_v2.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        record=db.execute('SELECT revision,data FROM stages WHERE job_id=? AND stage=1',(args.job_id,)).fetchone()
    if not record:parser.error('找不到第一阶段')
    one=json.loads(record[1]);one['views']=[v for v in one['views'] if v['kind']!='ISOMETRIC' and v.get('region_confirmed')][:args.views]
    if not one['views']:parser.error('没有已确认的视图图片')
    source=ROOT/'data/jobs'/args.job_id
    output=ROOT/'tmp/concurrency_benchmark'/args.job_id/uuid.uuid4().hex[:8]
    output.mkdir(parents=True)
    lock=threading.Lock();state={'active':0,'peak':0,'requests':[]}
    spec=one['profile_catalog'][0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']))
            payload=json.loads(body)
            text='\n'.join(c['text'] for m in payload['messages'] if m['role']=='user' for c in m['content'] if c['type']=='text')
            bundle=json.loads(text.rsplit('输入包：\n',1)[1].split('\nJSON schema:\n',1)[0])
            tag=bundle['current_view_tag']
            with lock:
                state['active']+=1;state['peak']=max(state['peak'],state['active'])
            try:
                time.sleep(args.delay)
                answer={'members':[{'spec':spec,'view_tags':[tag],'member_cnt':1,'length_mm':1000,
                    'location':'本地模拟接口固定行','issue':'仅测试并发与传输，不是AI识图结果'}]}
                response={'id':'local-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),
                    'model':payload['model'],'choices':[{'index':0,'finish_reason':'stop',
                    'message':{'role':'assistant','content':json.dumps(answer,ensure_ascii=False)}}],
                    'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}
                raw=json.dumps(response,ensure_ascii=False).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                with lock:state['requests'].append({'view_tag':tag,'body_sha256':hashlib.sha256(body).hexdigest()})
            finally:
                with lock:state['active']-=1

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    results=[];initial=None;model_inputs=None
    try:
        for limit in limits:
            run=output/f'workers-{limit}';run.mkdir()
            shutil.copytree(source/'native',run/'native')
            shutil.copyfile(source/'pdf_manifest.json',run/'pdf_manifest.json')
            (run/'views').mkdir()
            for view in one['views']:
                for name in [view['image_file'],*view.get('detail_files',[])]:
                    if Path(name).name!=name:raise ValueError('非法视图图片路径')
                    shutil.copyfile(source/'views'/name,run/'views'/name)
            with lock:state.update(active=0,peak=0,requests=[])
            started=time.monotonic()
            # Loopback benchmark traffic must not use a configured HTTP proxy.
            with patch('app.image_workflow._api_settings',return_value=(f'http://127.0.0.1:{server.server_port}/v1','local-test-token','deepseek-flash')),patch('app.pipeline.OpenAI',side_effect=lambda **kwargs:OpenAI(**kwargs,http_client=httpx.Client(trust_env=False))):
                data=recognize_views(run,copy.deepcopy(one),{'max_concurrency':str(limit),'reasoning_effort':'high'},
                    lambda _:None,AuditLog(run,2,secrets=('local-test-token',)))
            elapsed=time.monotonic()-started
            with lock:stats=copy.deepcopy(state)
            if stats['active'] or len(stats['requests'])!=len(one['views']):raise ValueError('模拟请求未完整结束')
            if stats['peak']>limit:raise ValueError('实际HTTP请求数超过上限')
            inputs=sorted(stats['requests'],key=lambda r:r['view_tag'])
            if initial is None:initial=data;model_inputs=inputs
            if data!=initial or inputs!=model_inputs:raise ValueError('并发数改变了模型请求内容或台账结果')
            results.append({'concurrency':limit,'elapsed_seconds':round(elapsed,3),
                            'actual_peak_http_requests':stats['peak'],'request_count':len(stats['requests'])})
            print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    with sqlite3.connect((ROOT/'data/jobs_v2.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        unchanged=record==db.execute('SELECT revision,data FROM stages WHERE job_id=? AND stage=1',(args.job_id,)).fetchone()
    report={'mock_api':True,'paid_api_calls':0,'mutates_job':False,'source_unchanged':unchanged,
        'source_stage_one_revision':record[0],'view_count':len(one['views']),'delay_seconds':args.delay,
        'identical_request_bodies_and_results':True,'runs':results,
        'note':'本地模拟等待的真实HTTP/SDK并发测试，不是实际模型耗时或识图准确率测试。'}
    write_json(output/'report.json',report)
    print('报告：'+str(output/'report.json'))
    if not unchanged:raise SystemExit('源目录在测试期间发生变化')


if __name__=='__main__':main()

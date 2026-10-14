"""Review missing boundaries in an existing paused task; retain revision provenance."""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from openai import OpenAI
from app.audit import AuditLog
from app.llm_runner import _api_settings
from app.native_pdf import evidence_index, view_context, write_json
from app.pipeline import (load_native, model_call, BoundaryResponse, boundary_review_prompt,
                          apply_boundary_reviews, evidence_aliases, AnalysisResponse, repair_model_evidence, merge_frame)
from verify_task import request


def review(base,identity,view_id=None,repair_empty=False,number=2):
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env')
    stage=request(base,f'/api/jobs/{identity}/stages/{number}')
    if stage['status']!='awaiting_review':raise ValueError('所选阶段须处于 awaiting_review。')
    if stage['data'].get('analysis_pending'):raise ValueError('第一阶段仅输出目录；请确认后进入第二阶段进行端部复核。')
    directory=root/'data'/'jobs'/identity
    data=stage['data']
    pages={p['id']:p for p in load_native(directory)}
    manifest={p['id']:p for p in json.loads((directory/'pdf_manifest.json').read_text(encoding='utf-8'))}
    evidence=evidence_index(list(pages.values()),data.get('manual_evidence',[]))
    aliases=evidence_aliases(evidence)
    base_url,key,model=_api_settings({})
    audit=AuditLog(directory,stage=number,secrets=(key,))
    client=OpenAI(base_url=base_url,api_key=key,timeout=600,max_retries=1)
    folder=directory/'responses'/f'attempt-boundary-{uuid.uuid4().hex}'
    for view in data['views']:
        if view_id and view['id']!=view_id:continue
        was_empty=not any(o['view_id']==view['id'] for o in data['observations'])
        if repair_empty and not view_id and not was_empty:continue
        for title in pages[view['page_id']]['titles']:
            if title['title']==view['title']:
                old=list(view['bbox']);box=title['title_bbox']
                view['bbox']=[min(old[0],box[0]),min(old[1],box[1]),max(old[2],box[2]),max(old[3],box[3])]
                if old!=view['bbox']:audit.event('VIEW_TITLE_INCLUDED',view_id=view['id'],before=old,after=view['bbox'],
                    evidence_id=title['evidence_id'],reason='区域扩展到原生标题位置，使标题和视图覆盖可核对。')
        if repair_empty and view['kind']!='ISOMETRIC' and not any(o['view_id']==view['id'] for o in data['observations']):
            prompts=sorted((directory/'responses').glob(f"attempt*/{view['id']}_observations.prompt.txt"),key=lambda p:p.stat().st_mtime)
            if not prompts:raise ValueError('找不到待恢复视图的原识别提示词。')
            prompt=prompts[-1].read_text(encoding='utf-8').split('\nJSON schema:\n',1)[0]
            print('repair missing observations',view['id'],flush=True)
            analysis=model_call(client,model,prompt,{**manifest[view['page_id']],'id':view['id'],'crop':view['bbox']},
                folder/f"{view['id']}_observations.json",AnalysisResponse,audit=audit,
                emit=lambda message:print(message,flush=True),
                reasoning_effort='max')
            for o in analysis['observations']:
                missing=repair_model_evidence(o,evidence,aliases,audit)
                if missing:o['deferred']=True;o['notes']+='; 证据待确认：'+', '.join(missing)
                for name in ('image_start','image_end'):
                    point=o[name];box=view['bbox']
                    if point is not None and not (box[0]<=point[0]<=box[2] and box[1]<=point[1]<=box[3]):
                        o[name]=None;o['deferred']=True
                data['observations'].append(o)
            repair_model_evidence(analysis,evidence,aliases,audit)
            merge_frame(data['frame'],analysis['frame'],data['warnings'],set())
            data['warnings'].extend(analysis['warnings'])
            data['warnings']=[('[已恢复候选，原失败记录] '+w) if w.startswith(view['title']+' 识别失败：') else w for w in data['warnings']]
            audit.event('VIEW_OBSERVATIONS_RECOVERED',view_id=view['id'],observation_count=len(analysis['observations']),
                        reason='仅重试无候选的视图；保留原截断响应，不改写其他视图。')
        observations=[o for o in data['observations'] if o['view_id']==view['id'] and any(
            o[name]['kind']=='UNKNOWN' or not o[name]['evidence_ids'] for name in ('start_boundary','end_boundary'))]
        if not observations:continue
        print(f"review {view['title']} observations={len(observations)}",flush=True)
        payload=model_call(client,model,boundary_review_prompt(view,observations,view_context(pages[view['page_id']],view['bbox'])),
            {**manifest[view['page_id']],'id':view['id'],'crop':view['bbox']},
            folder/f"{view['id']}_boundaries.json",BoundaryResponse,reuse_saved=True,
            emit=lambda message:print(message,flush=True),audit=audit,
            reasoning_effort='max')
        absent=apply_boundary_reviews(observations,payload,evidence,aliases,audit)
        data['warnings'].extend(payload['warnings'])
        if absent:data['warnings'].append('端部复核缺少观察：'+', '.join(absent))
        write_json(directory/'boundary_review_proposal.json',data)
    result=request(base,f'/api/jobs/{identity}/stages/{number}',body=None)  # detect intervening changes
    if result['revision']!=stage['revision']:raise ValueError('阶段版本已变化，复核提案已保存，未覆盖草稿。')
    import urllib.request
    body={'revision':stage['revision'],'data':data,'reason':'[API_BOUNDARY_REVIEW] 基于相同任务原图复核缺失端部；原响应及理由已保存。'}
    req=urllib.request.Request(base+f'/api/jobs/{identity}/stages/{number}',data=json.dumps(body).encode(),
                               headers={'Content-Type':'application/json'},method='PATCH')
    with urllib.request.urlopen(req,timeout=60) as response:print('saved revision',json.load(response)['revision'],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--base',default='http://127.0.0.1:8120')
    parser.add_argument('--view-id')
    parser.add_argument('--repair-empty-views',action='store_true')
    parser.add_argument('--stage',type=int,choices=(1,2),default=2,help='默认第二阶段；1 仅用于旧版已识别草稿')
    args=parser.parse_args()
    import re
    if not re.fullmatch(r'[0-9a-f]{32}',args.job_id):parser.error('job-id must be UUID hex')
    review(args.base.rstrip('/'),args.job_id,args.view_id,args.repair_empty_views,args.stage)

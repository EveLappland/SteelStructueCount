"""Automatic third stage for fixed-label length ledgers; bounded, resumable calls."""
from collections import deque, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from shutil import copy2
import time
import uuid

from .ai_concurrency import view_concurrency
from .audit import AuditLog
from .entity_inputs import read_inputs
from .entity_reduction import (EntityReply, compatible, conservation, exact_groups,
    find_cases, independent, validate_reply)
from .label_facts import digest
from .label_length_context import details
from .native_pdf import write_json
from .settings import _api_settings


def option(settings, key, env, default, maximum=None):
    value=settings.get(key,os.getenv(env,str(default)))
    if isinstance(value,bool) or not str(value).isdigit() or int(value)<1 or (maximum and int(value)>maximum):
        raise ValueError(f'{env}需要正整数'+(f'，最大{maximum}' if maximum else ''))
    return int(value)


def length_batches(ids, observations, size):
    groups=defaultdict(list)
    for identity in ids:
        r=observations[identity]
        # Unrelated horizontal/vertical strips are never put in the same task.
        dx,dy=r['direction']; direction='H' if abs(dx)>.98 else ('V' if abs(dy)>.98 else 'D')
        groups[(r['view_uid'],direction)].append(identity)
    result=[]
    for (_,direction),local in sorted(groups.items()):
        local.sort(key=lambda i:(observations[i]['bbox_view'][1 if direction=='H' else 0],i))
        for start in range(0,len(local),size):
            targets=local[start:start+size]
            result.append({'id':'length-'+digest(targets)[:16],'kind':'LENGTH','observation_ids':targets})
    return result


def compact_observation(row):
    value={k:deepcopy(row[k]) for k in ('observation_id','label_id','view_uid','source_plane','spec',
        'view_tags','bbox_view','direction','axis_context','length_mm','kind','endpoints','status')}
    value.update(issue=row.get('issue','')[:700],basis=row.get('basis','')[:900])
    candidate=row.get('measurement')
    if candidate:
        value['current_measurement']=candidate['answer']
    return value


def run(paths, output, settings=None, emit=print, *, call_api=False, resume=False):
    from .pipeline import model_call, OpenAI
    settings=settings or {}; output=Path(output).resolve()
    observations,views,sources=read_inputs(paths)
    if any(output==Path(s['path']).parent or output in Path(s['path']).parents for s in sources):
        raise ValueError('第三阶段输出目录不能覆盖输入目录或其父目录')
    size=option(settings,'batch_size','ENTITY_REVIEW_BATCH_SIZE',6,12)
    budget=option(settings,'max_calls','ENTITY_MAX_CALLS',24)
    limit=view_concurrency(settings); effort=settings.get('reasoning_effort','high')
    prompt_path=Path(__file__).parent/'prompts/entity_reconciliation.md'
    base_prompt=prompt_path.read_text(encoding='utf-8')
    manifest={'algorithm':'cross-view-entities/v1','sources':sources,'batch_size':size,
              'max_calls':budget,'reasoning_effort':effort,'prompt_sha256':hashlib.sha256(base_prompt.encode()).hexdigest(),
              'schema':EntityReply.model_json_schema()}
    output.mkdir(parents=True,exist_ok=True); manifest_path=output/'input_manifest.json'
    if manifest_path.exists() and (not resume or json.loads(manifest_path.read_text(encoding='utf-8'))!=manifest):
        raise ValueError('已有输出仅允许相同输入/参数/prompt使用--resume，或选择新目录')
    write_json(manifest_path,manifest)
    base,key,model=_api_settings(settings) if call_api else ('','','')
    audit=AuditLog(output,stage=3,secrets=(key,)); started=time.monotonic()
    attempt='attempt-entities-'+uuid.uuid4().hex
    responses=output/'responses'/attempt; trace=output/'runs'/attempt
    (output/'views').mkdir(exist_ok=True)
    for view in views.values():
        image=output/'views'/('source-'+digest(view['uid'])[:20]+'.png')
        if image.exists() and hashlib.sha256(image.read_bytes()).hexdigest()!=view['image_sha256']:
            raise ValueError('第三阶段原图已被修改')
        if not image.exists(): copy2(view['image_path'],image)
        view['image_path']=str(image); view['image_file']=image.relative_to(output).as_posix()
    write_json(output/'observations.json',list(observations.values()))
    write_json(output/'source_views.json',list(views.values()))
    cases,orphans=find_cases(observations); results={}; states={}; ready=deque(); pending={}
    requests=0; peak=0
    for case in cases:
        exact=exact_groups(case,observations)
        if exact is not None:
            results[case['id']]=exact; states[case['id']]={'state':'RULE_MATCH','kind':case['kind']}
        else:
            ready.append((case,0,None))
    review=[]
    for identity in orphans:
        r=observations[identity]
        if (r['length_mm'] is None or r.get('issue') or r.get('local_checks') or r.get('review_notes')
                or r['status']!='MODEL_DERIVED' or (r['kind']=='BRACE' and r['selected_role']=='master')):
            review.append(identity)
        else:
            results['single-'+digest(identity)[:16]]=independent([identity],observations,
                '无跨视图所属面对应；保留第二阶段独立观察。',method='INDEPENDENT')
    for case in length_batches(review,observations,size):
        cases.append(case); ready.append((case,0,None))
    def make_request(case,retry,error):
        targets=case['observation_ids']; local_views=sorted({observations[i]['view_uid'] for i in targets})
        image_map=[]; image_paths=[]; facts=[]
        for uid in local_views:
            view=views[uid]
            image_paths.append(view['image_path'])
            image_map.append({'image_number':len(image_paths),'view_uid':uid,'kind':'ORIGINAL','bbox_view':[0,0,1,1]})
            local_targets={observations[i]['label_id'] for i in targets if observations[i]['view_uid']==uid}
            for detail in details(output,view['image_path'],view,local_targets,limit=2):
                image_paths.append(detail['path']); image_map.append({'image_number':len(image_paths),
                    'view_uid':uid,'kind':'DETAIL','bbox_view':detail['bbox_view']})
            keep={a['text_id'] for a in view['axes']}
            keep.update(l['text_id'] for l in view['labels'] if l['label_id'] in local_targets)
            texts=[{k:t[k] for k in ('id','text','bbox_view','values_mm')} for t in view['text_facts']
                   if t['values_mm'] or t['id'] in keep or re.search(r'SPLICE|^CS$|^PSC|^PSTC',t['text'],re.I)]
            facts.append({k:view[k] for k in ('uid','view_id','title','plane_name','kind','axis_order')}|{
                'axes':[{k:a[k] for k in ('name','text_id','orientation','coordinate_view','span_view')} for a in view['axes']],
                'text_facts':texts})
        payload={'task':case,'observations':[compact_observation(observations[i]) for i in targets],
            'allowed_pairs':[[a,b] for a,b in combinations_sorted(targets) if compatible(observations[a],observations[b])],
            'views':facts,'image_map':image_map,
            'local_validation_error':error[:2500] if error else None}
        prompt=base_prompt+'\n输入：\n'+json.dumps(payload,ensure_ascii=False,sort_keys=True)
        entry={'id':f"{case['id']}_try{retry}",'page':1,'view_id':case['id'],'task_kind':'entity_'+case['kind'].lower(),
               'source_path':image_paths[0],'image_path':image_paths[0],'detail_paths':image_paths[1:],
               'image_dpi':300,'image_context':'图片可能来自不同视图。严格按image_map中的图片序号、view_uid、ORIGINAL/DETAIL及bbox_view对应；局部图不是独立构件或新视图。'}
        audit.text(trace/f"{entry['id']}.prompt.txt",prompt)
        return entry,prompt

    def request(case,entry,prompt):
        try:
            with OpenAI(base_url=base,api_key=key,timeout=600,max_retries=0) as client:
                payload=model_call(client,model,prompt,entry,responses/f"{entry['id']}.json",EntityReply,
                    reuse_saved=resume,emit=emit,audit=audit,reasoning_effort=effort)
            entities=validate_reply(payload,case,observations,views)
            return entities,None
        except Exception as exc:
            return None,audit.scrub(str(exc))

    def snapshot():
        entities=[e for group in results.values() for e in group]
        assigned={i for e in entities for i in e['observation_ids']}
        # Pending/failed tasks still yield a complete ledger; retries never mutate sources.
        entities+=independent(set(observations)-assigned,observations,'本任务尚未取得可采用的自动裁决，暂保留来源观察。')
        entities.sort(key=lambda e:e['id'])
        check=conservation(entities,observations)
        mappings=[]
        for e in entities:
            for identity in e['observation_ids']:
                r=observations[identity]
                mappings.append({'observation_id':identity,'label_id':r['label_id'],'view_uid':r['view_uid'],
                    'entity_id':e['id'],'original_length_mm':r['length_mm'],'entity_length_mm':e['length_mm'],
                    'changed':r['length_mm']!=e['length_mm']})
        quantity=sum(e['member_cnt'] for e in entities)
        summary={'input_observations':len(observations),'entity_groups':len(entities),'entity_quantity':quantity,
            'removed_duplicate_observations':len(observations)-quantity,
            'unknown_entity_quantity':sum(e['member_cnt'] for e in entities if e['length_mm'] is None),
            'length_changed_observations':sum(m['changed'] for m in mappings),
            'fallback_entity_quantity':sum(e['member_cnt'] for e in entities if e['method']=='FALLBACK'),
            'api_request_attempts':requests,'request_concurrency':limit,'observed_peak_requests':peak,
            'elapsed_seconds':round(time.monotonic()-started,3)}
        result={'format':'cross-view-entities/v1','sources':sources,'views':[{k:v[k] for k in
            ('uid','title','plane_name','image_file')} for v in views.values()],
            'entities':entities,'observation_entity_map':mappings,'cases':[
                {**c,**states.get(c['id'],{'state':'PENDING'})} for c in cases],
            'summary':summary,'conservation':check,
            'limits':'Automatic model/rule conclusions; arithmetic and observation conservation checked, not an engineering accuracy certificate. Combinations expanded once only in component export.'}
        write_json(output/'stage_three.json',result)
        return result

    audit.event('ENTITY_RUN_STARTED',call_api=call_api,manifest=manifest,concurrency=limit)
    snapshot()
    if call_api:
        with ThreadPoolExecutor(max_workers=limit,thread_name_prefix='entity') as executor:
            while ready or pending:
                while ready and len(pending)<limit:
                    case,retry,error=ready.popleft()
                    if len(case['observation_ids'])>32 or requests>=budget:
                        reason='关联组超过32条，保留独立观察，未擅自切断匹配组。' if len(case['observation_ids'])>32 else '已达本次API请求预算，保留独立观察。'
                        states[case['id']]={'state':'FALLBACK','error':reason}
                        results[case['id']]=independent(case['observation_ids'],observations,reason)
                        continue
                    requests+=1
                    # Prepare shared crop files on the coordinator, never race
                    # writes to identical context images in request workers.
                    entry,prompt=make_request(case,retry,error)
                    pending[executor.submit(request,case,entry,prompt)]=(case,retry)
                    peak=max(peak,len(pending)); states[case['id']]={'state':'RUNNING','kind':case['kind']}
                    emit(f"第三阶段 {case['kind']} {case['id']}：{len(case['observation_ids'])}条观察，并发{len(pending)}/{limit}，预算{requests}/{budget}。")
                if not pending:
                    continue
                done,_=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
                for future in done:
                    case,retry=pending.pop(future); entities,error=future.result()
                    write_json(trace/f"{case['id']}_try{retry}.validated.json",{'entities':entities,'error':error})
                    if error and retry<1:
                        ready.append((case,retry+1,error))
                        audit.event('ENTITY_RETRY',case_id=case['id'],error=error)
                    else:
                        states[case['id']]={'state':'FALLBACK' if error else 'AI_RESOLVED','error':error}
                        results[case['id']]=entities if entities is not None else independent(case['observation_ids'],observations,error)
                        emit(f"第三阶段 {case['id']}：{'回退保留全部观察：'+error if error else '自动裁决已采用'}。")
                    snapshot()
    else:
        for case,_,_ in ready:
            make_request(case,0,None); states[case['id']]={'state':'PREPARED'}
        emit('第三阶段输入和提示词已生成；未调用API，待处理项保留为独立观察。')
    result=snapshot()
    from .entity_report import export
    export(output,result,observations)
    audit.event('ENTITY_RUN_FINISHED',summary=result['summary'])
    return result


def combinations_sorted(ids):
    from itertools import combinations
    return combinations(sorted(ids),2)

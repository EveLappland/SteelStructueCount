"""Fixed-label grid -> master -> targeted expert -> conflict-only arbitration.

One global queue and request pool. Only the coordinator writes ledgers/traces.
Failures deliver one row per source label; no manual or reference-answer input.
"""
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
from shutil import copy2
import time
import uuid

from .ai_concurrency import view_concurrency
from .audit import AuditLog
from .label_facts import load
from .label_length_context import prepare, frame_input, label_input, details
from .label_length_contracts import LengthReply, DecisionReply, check_ids, evaluate, different, unknown
from .native_pdf import write_json
from .settings import _api_settings


@dataclass(frozen=True)
class Request:
    view_id: str
    role: str
    targets: tuple[str, ...]
    entry: dict
    prompt: str
    tile_id: str = ''
    retry: int = 0

    @property
    def name(self):
        return f'{self.view_id}_{self.role}_{self.tile_id or "all"}_format{self.retry:02d}'


def prompt_file(name):
    return (Path(__file__).parent/'prompts'/name).read_text(encoding='utf-8')


def review_batches(view, targets, size):
    """Keep direction/locality together without mixing independent label IDs."""
    groups = {}
    for label in view['labels']:
        if label['label_id'] not in targets:
            continue
        dx,dy = label['direction']
        direction = 'H' if abs(dx)>.98 else ('V' if abs(dy)>.98 else 'D')
        groups.setdefault(direction,[]).append(label)
    batches = []
    for direction,labels in sorted(groups.items()):
        labels.sort(key=lambda l:(l['bbox_view'][1 if direction=='H' else 0],l['label_id']))
        for start in range(0,len(labels),size):
            batches.append([l['label_id'] for l in labels[start:start+size]])
    return batches


def run(facts_path, output, settings, emit=print, *, call_api=False, resume=False, view_ids=()):
    from .pipeline import model_call, OpenAI
    facts_path, output = Path(facts_path).resolve(), Path(output).resolve()
    if output == facts_path.parent:
        raise ValueError('第二阶段输出目录不能覆盖第一阶段交付目录')
    bundle = load(facts_path)
    selected = [v for v in bundle['views'] if not view_ids or v['view_id'] in view_ids]
    if set(view_ids)-{v['view_id'] for v in selected}:
        raise ValueError('请求的view_id不在第一阶段交付包中')
    output.mkdir(parents=True, exist_ok=True)
    limit = view_concurrency(settings)
    effort = settings.get('reasoning_effort', 'high')
    batch_size = settings.get('review_batch_size',os.getenv('LABEL_LENGTH_REVIEW_BATCH_SIZE','6'))
    if isinstance(batch_size,bool) or not str(batch_size).isdigit() or not 1<=int(batch_size)<=12:
        raise ValueError('LABEL_LENGTH_REVIEW_BATCH_SIZE需要1至12的整数')
    batch_size = int(batch_size)
    manifest = {'stage_one_sha256': bundle['bundle_sha256'], 'view_ids': [v['view_id'] for v in selected],
                'reasoning_effort': effort, 'review_batch_size':batch_size,
                'algorithm': 'label-lengths/v2-batched-reviews'}
    path = output/'input_manifest.json'
    if path.exists():
        if not resume or json.loads(path.read_text(encoding='utf-8')) != manifest:
            raise ValueError('输出目录已存在；只能以相同交付包/视图/强度显式--resume，或使用新目录')
    write_json(path, manifest)
    write_json(output/'stage_one_facts.snapshot.json', bundle)
    (output/'views').mkdir(exist_ok=True)
    base,key,model = _api_settings(settings) if call_api else ('','','')
    audit = AuditLog(output, stage=2, secrets=(key,))
    started = time.monotonic()
    attempt = 'attempt-label-lengths-'+uuid.uuid4().hex
    responses, trace = output/'responses'/attempt, output/'runs'/attempt
    items, ready, pending = {}, deque(), {}

    def make_request(item, role, targets, *, tile=None, payload=None, batch_id=''):
        view = item['view']
        labels = [l for l in view['labels'] if l['label_id'] in targets]
        metadata = {'stage_one': frame_input(view,tile,item['plan']), 'target_labels': [label_input(l,tile,item['plan']) for l in labels],
                    'coordinates': 'bbox_view/points_view are original-view normalized; bbox_tile is crop normalized; never mm',
                    **(payload or {})}
        entry = {'id': view['view_id'], 'page': 1, 'source_path': str(item['image']),
                 'image_path': str(item['image']), 'image_dpi': 300}
        if role == 'child':
            entry['image_path'] = str(output/'views'/tile['image_file'])
            metadata['tile'] = tile
            crop = tile['crop_bbox_px']; w,h = view['image_size_px']
            metadata['support_labels'] = [label_input(l,tile,item['plan']) for l in view['labels']
                if l['label_id'] not in targets and crop[0] <= (l['bbox_view'][0]+l['bbox_view'][2])/2*w <= crop[2]
                and crop[1] <= (l['bbox_view'][1]+l['bbox_view'][3])/2*h <= crop[3]]
        elif role in ('expert','arbiter'):
            context = details(output, item['image'], view, set(targets))
            entry['detail_paths'] = [c['path'] for c in context]
            metadata['context_images'] = [{'image_number': i+2,'bbox_view':c['bbox_view']} for i,c in enumerate(context)]
        prompt = prompt_file('label_length_common.md')+'\n'+prompt_file(f'label_length_{role}.md')
        prompt += '\n输入：\n'+json.dumps(metadata,ensure_ascii=False,sort_keys=True)
        task = Request(view['view_id'],role,tuple(l['label_id'] for l in labels),entry,prompt,tile['id'] if tile else batch_id)
        audit.text(trace/view['view_id']/f'{role}_{task.tile_id or "all"}.prompt.txt', prompt)
        return task

    for view in selected:
        image = output/'views'/f"original-{view['view_id']}.png"
        if image.exists() and hashlib.sha256(image.read_bytes()).hexdigest() != view['image_sha256']:
            raise ValueError('第二阶段目录中的原图被修改')
        if not image.exists():
            copy2(facts_path.parent/view['image_file'],image)
        plan, owners = prepare(output,image,view)
        item = {'view': view,'image':image,'plan':plan,'owners':owners,'children':{},'candidates':{},'decisions':{},
                'chosen':{},'warnings':[],'state':'children','requests':0,'finished':False,'child_tasks':[]}
        for label in view['labels']:
            identity = label['label_id']
            item['candidates'][identity] = {}
            item['chosen'][identity] = ('unresolved', evaluate(unknown(identity,'尚未取得尺寸回答'),view['text_facts']))
        items[view['view_id']] = item
        for tile in plan['tiles']:
            targets = [identity for identity,owner in owners.items() if owner == tile['id']]
            if targets:
                task = make_request(item,'child',targets,tile=tile)
                ready.append(task); item['child_tasks'].append(task.tile_id)
        if not item['child_tasks']:
            item.update(finished=True,state='empty')
        write_json(trace/view['view_id']/'input.json', {'view':view,'grid':plan,'label_owners':owners})

    def rows():
        ledger = []
        for item in items.values():
            view = item['view']
            for label in view['labels']:
                role,candidate = item['chosen'][label['label_id']]
                answer = candidate['answer']
                all_candidates = item['candidates'][label['label_id']]
                expert = all_candidates.get('expert')
                review_notes = ([expert['answer']['issue'] or '长度专家未取得有效尺寸依据']+expert['checks']
                                if expert and not expert['usable'] and candidate['usable'] else [])
                ledger.append({'id':label['label_id'],'label_id':label['label_id'],'view_id':view['view_id'],
                    'view_title':view['title'],'spec':label['spec'],'member_cnt':1,
                    'component_multiplier':label['component_multiplier'],'view_tags':list(label['view_tags']),
                    'bbox_view':label['bbox_view'],'length_mm':candidate['length_mm'],
                    'dimension_values_mm':candidate['dimension_values_mm'],'formulas':candidate['formulas'],
                    'kind':answer['kind'],'endpoints':answer['endpoints'],'basis':answer['basis'],
                    'issue':answer['issue'],'review_notes':review_notes,'local_checks':candidate['checks'],'selected_role':role,
                    'arbitration':item.get('decisions',{}).get(label['label_id']),
                    'status':'UNKNOWN' if not candidate['usable'] else ('CONFLICT_FALLBACK' if role.startswith('unresolved_conflict')
                        else ('WITH_ISSUE' if answer['issue'] or review_notes else 'MODEL_DERIVED')),
                    'candidates':deepcopy(all_candidates)})
        return ledger

    def snapshot():
        ledger = rows()
        result = {'format':'label-lengths/v1','stage_one_sha256':bundle['bundle_sha256'],
                  'views':[{'view_id':i['view']['view_id'],'title':i['view']['title'],
                      'image_file':str(i['image'].relative_to(output)).replace('\\','/'),
                      'expected_count':len(i['view']['labels']),'state':i['state'],'requests':i['requests'],
                      'warnings':i['warnings']} for i in items.values()], 'members':ledger,
                  'summary':{'member_count':len(ledger),'known_length_count':sum(r['length_mm'] is not None for r in ledger),
                      'unknown_length_count':sum(r['length_mm'] is None for r in ledger),
                      'fixed_id_coverage':len(ledger)==sum(len(v['labels']) for v in selected),
                      'request_concurrency':limit,'elapsed_seconds':round(time.monotonic()-started,3)},
                  'limits':'No cross-view deduplication. Native numeric operands and local arithmetic verified; graphical endpoint/dimension interpretation remains model-derived.'}
        write_json(output/'stage_two.json',result)
        return result

    def store_candidates(item,role,payload):
        for answer in payload['members']:
            identity = answer['label_id']
            item['candidates'][identity][role] = evaluate(answer,item['view']['text_facts'])

    def finish(item):
        item.update(finished=True,state='completed')
        emit(f"完成 {item['view']['title']}：{sum(c['usable'] for _,c in item['chosen'].values())}/{len(item['chosen'])} 个有长度；固定标签全部保留。")

    def after_master(item):
        targets,questions = [],{}
        for identity,candidates in item['candidates'].items():
            candidate = candidates.get('master')
            if candidate is None:
                candidate = next((c for key,c in candidates.items() if key.startswith('child:') and c['usable']),
                                 item['chosen'][identity][1])
                item['chosen'][identity] = ('child_fallback' if candidate['usable'] else 'unresolved',candidate)
            else:
                item['chosen'][identity] = ('master',candidate)
            reasons = list(candidate['checks'])
            if not candidate['usable']: reasons.append('UNKNOWN或缺少可计算的原始尺寸引用')
            if candidate['answer']['kind'] == 'BRACE': reasons.append('核验斜撑两条直角边的节点/轴线对应，不仅是重算斜边')
            if candidate['answer']['issue']: reasons.append(candidate['answer']['issue'])
            if any(different(candidate,c) for key,c in candidates.items() if key.startswith('child:')):
                reasons.append('网格与总代理尺寸解释不一致')
            if reasons:
                targets.append(identity); questions[identity] = reasons
        if targets:
            item['state'] = 'expert'
            batches = review_batches(item['view'],set(targets),batch_size)
            item['expert_remaining'] = len(batches)
            for index,batch in enumerate(batches):
                ready.append(make_request(item,'expert',batch,batch_id=f'batch{index:03d}',payload={
                    'target_questions':{i:questions[i] for i in batch},
                    'previous_candidates':{i:item['candidates'][i] for i in batch}}))
        else:
            finish(item)

    def after_expert(item):
        conflicts = []
        for identity,candidates in item['candidates'].items():
            expert = candidates.get('expert')
            if not expert:
                continue
            previous = item['chosen'][identity][1]
            if different(previous,expert):
                # Name the actual pre-expert candidate explicitly for arbitration.
                candidates['master'] = candidates.get('master',previous)
                conflicts.append(identity)
            elif expert['usable']:
                item['chosen'][identity] = ('expert',expert)
        if conflicts:
            item['state'] = 'arbiter'
            batches = review_batches(item['view'],set(conflicts),batch_size)
            item['arbiter_remaining'] = len(batches)
            for index,batch in enumerate(batches):
                ready.append(make_request(item,'arbiter',batch,batch_id=f'batch{index:03d}',payload={
                    'candidates':{i:{'MASTER':item['candidates'][i]['master'],
                                     'EXPERT':item['candidates'][i]['expert']} for i in batch}}))
        else:
            finish(item)

    def accept(task, payload, error=None):
        item = items[task.view_id]
        role = task.role
        write_json(trace/task.view_id/f'{task.name}.validated.json',{'payload':payload,'error':error})
        if error:
            item['warnings'].append(f'{role}: {error}')
        if role == 'child':
            item['children'][task.tile_id] = {'targets':list(task.targets),'reply':payload,'error':error}
            if payload: store_candidates(item,'child:'+task.tile_id,payload)
            if len(item['children']) == len(item['child_tasks']):
                item['state'] = 'master'
                ready.append(make_request(item,'master',list(item['chosen']),payload={'child_results':item['children']}))
        elif role == 'master':
            if payload: store_candidates(item,'master',payload)
            after_master(item)
        elif role == 'expert':
            if payload: store_candidates(item,'expert',payload)
            item['expert_remaining'] -= 1
            if item['expert_remaining'] == 0:
                after_expert(item)
        else:
            if payload:
                for decision in payload['decisions']:
                    identity = decision['label_id']; choices = item['candidates'][identity]
                    item['decisions'][identity] = decision
                    if decision['choice'] == 'REVISE' and decision['revised']:
                        candidate = evaluate(decision['revised'],item['view']['text_facts'])
                    elif decision['choice'] in ('MASTER','EXPERT'):
                        candidate = choices.get(decision['choice'].lower())
                    else:
                        candidate = evaluate(unknown(identity,decision['reason']),item['view']['text_facts'])
                    if candidate is None:
                        candidate = evaluate(unknown(identity,'仲裁选择缺少有效候选'),item['view']['text_facts'])
                    choices['arbiter'] = candidate
                    item['chosen'][identity] = ('arbiter:'+decision['choice'],candidate)
            else:
                for identity in task.targets:
                    role,candidate = item['chosen'][identity]
                    item['chosen'][identity] = ('unresolved_conflict_fallback:'+role,candidate)
            item['arbiter_remaining'] -= 1
            if item['arbiter_remaining'] == 0:
                finish(item)

    def request(task):
        contract = DecisionReply if task.role == 'arbiter' else LengthReply
        path = responses/f'{task.name}.json'
        entry = {**task.entry,'id':task.name,'view_id':task.view_id,'task_kind':'label_length_'+task.role}
        try:
            with OpenAI(base_url=base,api_key=key,timeout=600,max_retries=0) as client:
                payload = model_call(client,model,task.prompt,entry,path,contract,reuse_saved=resume,
                                     emit=emit,audit=audit,reasoning_effort=effort)
            check_ids(payload,task.targets,task.role)
            return payload,None
        except Exception as error:
            return None,audit.scrub(str(error))

    audit.event('LABEL_LENGTH_RUN_STARTED',call_api=call_api,concurrency=limit,manifest=manifest)
    if call_api:
        with ThreadPoolExecutor(max_workers=limit,thread_name_prefix='label-length') as executor:
            while ready or pending:
                while ready and len(pending)<limit:
                    task = ready.popleft(); item = items[task.view_id]
                    item['requests'] += 1
                    pending[executor.submit(request,task)] = task
                    emit(f"调度 {task.role} {task.view_id}：{len(task.targets)} 个标签；请求并发 {len(pending)}/{limit}。")
                done,_ = wait(pending,timeout=1,return_when=FIRST_COMPLETED)
                for future in done:
                    task = pending.pop(future)
                    payload,error = future.result()
                    if error and task.retry < 1:
                        audit.event('LABEL_LENGTH_FORMAT_OR_TRANSPORT_RETRY',role=task.role,error=error,view_id=task.view_id)
                        ready.append(replace(task,retry=task.retry+1,prompt=task.prompt+
                            '\n本地格式/ID/传输检查错误（不是新增图纸事实）：'+error[:3000]+
                            '\n请按原图和原始输入返回符合schema且目标ID一一对应的完整对象，不改变标签清单。'))
                    else:
                        accept(task,payload,error)
                    snapshot()
    else:
        for item in items.values(): item['state'] = 'prepared'
        emit('已生成网格、固定标签分配和子代理输入；未调用API。')
    result = snapshot()
    from .label_length_report import export
    export(output,result)
    audit.event('LABEL_LENGTH_RUN_FINISHED',summary=result['summary'])
    return result

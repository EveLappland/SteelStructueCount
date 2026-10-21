"""One ready queue for all grid workers, coordinators and bounded repairs.

Only HTTP requests run on workers. PNG preparation, validation, trace writes and
shared-ledger checkpoints belong to the coordinator. Length experts use the same queue.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from dataclasses import dataclass,replace
import json
from pathlib import Path
import time
import uuid

from .ai_concurrency import view_concurrency
from .axis_catalog import build_axis_catalog
from .domain import StageTwo
from .grid_contracts import GridChildResponse,GridMasterResponse
from .grid_layout import freeze_grid
from .grid_validation import assess_master
from .length_expert import LengthExpertResponse,target_rows,assess_reply,apply_reply
from .length_context import work_package,freeze_context
from .member_recognition import validate_rows,validate_ledger
from .native_pdf import write_json
from .quantity_reference import assess_quantity
from .settings import _api_settings
from .view_filter import role

MAX_FORMAT_RETRIES=2
MAX_MASTER_REPAIRS=2


@dataclass(frozen=True)
class Task:
    view_id: str
    kind: str
    entry: dict
    prompt: str
    tile_id: str = ''
    round: int = 0
    format_retry: int = 0

    @property
    def name(self):
        phase=self.tile_id if self.kind=='child' else f'{self.kind}{self.round:02d}'
        return f'{self.view_id}_{phase}_format{self.format_retry:02d}'

    @property
    def contract(self):return {'child':GridChildResponse,'master':GridMasterResponse,'length_expert':LengthExpertResponse}[self.kind]


def prompt_file(name):return (Path(__file__).parent/'prompts'/name).read_text(encoding='utf-8')


def locate_children(plan,responses):
    located=[]
    for tile in plan['tiles']:
        observations=[];crop=tile['crop_bbox_px']
        for index,candidate in enumerate(responses[tile['id']]['candidates'],1):
            px=min(crop[0]+candidate['label_point'][0]*(crop[2]-crop[0]),plan['width_px']-1e-6)
            py=min(crop[1]+candidate['label_point'][1]*(crop[3]-crop[1]),plan['height_px']-1e-6)
            owner=next(t['id'] for t in plan['tiles'] if t['core_bbox_px'][0]<=px<t['core_bbox_px'][2] and t['core_bbox_px'][1]<=py<t['core_bbox_px'][3])
            observations.append({**candidate,'candidate_id':f"{tile['id']}-label{index:03d}",
                'source_tile':tile['id'],'owner_tile':owner,'in_source_core':owner==tile['id'],
                'label_point_original_px':[round(px,3),round(py,3)]})
        located.append({'tile_id':tile['id'],'observations':observations})
    return located


def frame_context(view,one):
    return {'view_id':view['id'],'title':view['title'],'kind':view['kind'],
            'current_view_tag':view['plane_name'] or view['title'],
            'axis_names':one['axis_catalog'].get('per_view',{}).get(view['id'],[]),
            'profile_catalog':view.get('steel_types') or one['profile_catalog'],
            'quantity_reference':view.get('quantity_reference'),
            'count_unit':'当前独立视图的钢材段；2LF 等组合计一个，未做跨视图去重'}


def ledger_rows(payload,view,one,feedback):
    """Retain schema-valid candidates even when provenance still needs review."""
    raw=[]
    for index,member in enumerate(payload['members']):
        checks=[item['message'] for item in feedback
                if item.get('check')!='stage_one_quantity_reference' and
                (item.get('row_index') is None or item.get('row_index')==index)]
        raw.append({**member,'id':f"{view['id']}-m{index+1:03d}",'grid_checks':list(dict.fromkeys(checks))})
    return validate_rows(raw,view,one)


def master_task(item,*,previous=None,feedback=None,round=0):
    plan=item['plan'];context=item['context']
    # Keep master reconciliation bounded. Length work happens after adoption.
    related={t for f in (feedback or []) for t in f.get('tile_ids',[])}
    if not related:related={t['id'] for t in plan['tiles']}
    related.add(plan['tiles'][0]['id'])
    selected=[t for t in plan['tiles'] if t['id'] in related and
              t['crop_bbox_px']!=[0,0,plan['width_px'],plan['height_px']]]
    payload={'stage_one_context':context,'grid_tiles':plan['tiles'],
             'original_size_px':[plan['width_px'],plan['height_px']],
             'image_map':[{'image_number':1,'role':'original'},
                *[{'image_number':index+2,'role':'grid_detail','tile_id':tile['id'],'crop_bbox_px':tile['crop_bbox_px']}
                  for index,tile in enumerate(selected)]], 'child_results':item['located']}
    text=prompt_file('grid_master.md')
    if previous is not None:
        text+='\n'+prompt_file('grid_repair.md')
        payload.update(previous_reply=previous,local_validation_feedback=feedback,repair_round=round)
    entry={**item['entry'],'detail_paths':[str(item['directory']/'views'/t['image_file']) for t in selected]}
    return Task(item['view']['id'],'master',entry,text+'\n输入：\n'+json.dumps(payload,ensure_ascii=False),round=round)


def recognize_views(directory,one,settings,emit,audit,*,reuse_saved=False,recorded_masters=None):
    from .pipeline import load_native,model_call,OpenAI,response_content
    started=time.monotonic();directory=Path(directory);limit=view_concurrency(settings)
    base,key,model=_api_settings(settings)
    pages={page['id']:page for page in load_native(directory)}
    one={**one,'axis_catalog':build_axis_catalog(list(pages.values()),one['views'])}
    result=StageTwo(**one,recognition_strategy='grid_agents',grid_configuration={
        'request_concurrency':limit,'trigger_px':2200,'target_core_px':1800,'overlap_px':240,
        'max_master_repairs':MAX_MASTER_REPAIRS,'max_format_retries':MAX_FORMAT_RETRIES,
        'unknown_length_specialist':True,'length_expert':True,'max_length_rounds':2,
        'scale_mode':'auxiliary_only','master_input_mode':'recorded_real_output' if recorded_masters is not None else 'grid_requests'}).model_dump()
    result['warnings']=[]
    folder=directory/'responses'/f'attempt-grid-{uuid.uuid4().hex}'
    trace=directory/'grid_runs'/folder.name;prepared=[];queue=deque();pending={}
    ids=[view['id'] for view in one['views']]
    if len(ids)!=len(set(ids)):raise ValueError('视图ID重复，不能保存网格响应')

    for view in one['views']:
        item={'view':view,'directory':directory,'rows':[],'children':{},'child_errors':{},'warnings':[],
              'calls':0,'status':{'view_id':view['id'],'status':'queued','member_cnt':0,'grid_count':0}}
        prepared.append(item)
        if role(view)!='MEMBERS' or view['kind']=='ISOMETRIC':
            item['status'].update(status='filtered',reason=view.get('filter_reason',''));continue
        if not view.get('region_confirmed'):
            item['status']['status']='region_pending';item['warnings'].append(f"{view['title']} 第一阶段裁切尚未确认。")
            continue
        colored=settings.get('label_color_mode','original')=='colored'
        filename=view.get('colorized_image_file') if colored else view.get('image_file')
        if not filename or Path(filename).name!=filename:raise ValueError('第一阶段高清图片路径无效')
        image=directory/'views'/filename
        if not image.is_file() or view.get('image_bbox')!=view['bbox']:
            raise ValueError(f"{view['title']} 缺少与第一阶段区域一致的高清图，请重试第一阶段")
        plan=freeze_grid(directory,view['id'],image)
        context=frame_context(view,one)
        entry={'id':view['id'],'page':pages[view['page_id']]['page'],'source_path':str(image),
               'image_path':str(image),'image_dpi':300,'allowed_view_tags':context['axis_names']}
        item.update(plan=plan,entry=entry,context=context)
        item['status'].update(grid_count=len(plan['tiles']),children_completed=0,model_calls=0,
                              quantity_reference=view.get('quantity_reference'),grid_tiles=plan['tiles'])
        write_json(trace/view['id']/'input.json',{'stage_one_context':context,'grid':plan,
                   'stage_one_views':len(one['views']),'image_sha256':plan['input_sha256']})
        if recorded_masters is not None:
            replay=recorded_masters[view['id']]
            if replay['image_sha256']!=plan['input_sha256']:raise ValueError('保存的总代理结果与第一阶段图片指纹不一致，不能用于长度实验')
            item['located']=replay['located'];item['replay']=replay
            item['status']['children_completed']=len(plan['tiles'])
            continue
        for tile in plan['tiles']:
            metadata={'stage_one_context':context,'tile':tile,
                      'original_size_px':[plan['width_px'],plan['height_px']],
                      'coordinate_note':'label_point 相对当前子图，像素不是毫米；整视图数量标准不是本裁片应凑足的数量。'}
            child_entry={**entry,'image_path':str(directory/'views'/tile['image_file'])}
            queue.append(Task(view['id'],'child',child_entry,prompt_file('grid_child.md')+'\n输入：\n'+json.dumps(metadata,ensure_ascii=False),tile['id']))
    by_id={item['view']['id']:item for item in prepared}

    def checkpoint():
        result['view_members']=[row for item in prepared for row in item['rows']]
        result['view_analysis']=[dict(item['status']) for item in prepared]
        result['warnings']=[w for w in one.get('warnings',[]) if w.startswith('页面待确认：')]+[
            warning for item in prepared for warning in item['warnings']]
        path=directory/'image_recognition_checkpoint.json';temporary=path.with_suffix('.tmp')
        write_json(temporary,result);temporary.replace(path)

    def request(task):
        entry={**task.entry,'id':task.name,'view_id':task.view_id,'task_kind':task.kind}
        path=folder/f'{task.name}.json'
        try:
            with OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1) as client:
                payload=model_call(client,model,task.prompt,entry,path,task.contract,emit=emit,audit=audit,
                    reuse_saved=reuse_saved,reasoning_effort=settings.get('reasoning_effort','high'))
            return {'payload':payload,'path':path}
        except Exception as error:
            return {'error':audit.scrub(str(error)),'path':path}

    def fail(item,message):
        item['status'].update(status='failed',error=message,model_calls=item['calls'])
        item['warnings'].append(f"{item['view']['title']}：网格识图未完成；{message}")
        audit.event('GRID_VIEW_FAILED',view_id=item['view']['id'],error=message)
        emit(f"视图识图失败：{item['view']['title']}；有效回复已保留，其他视图继续。")

    def adopt(item,payload,assessment,quantity,round,feedback):
        rows=ledger_rows(payload,item['view'],one,feedback)
        item['rows']=rows
        item['status'].update(status=('completed_with_warnings' if feedback else 'completed') if rows else 'empty',member_cnt=sum(r['member_cnt'] for r in rows),
            label_count=quantity['expected_total'],coverage_differences=quantity['differences'],
            quantity_reference_match=quantity['matches'],quantity_reference_available=quantity['reference_available'],
            model_calls=item['calls'],master_rounds=round+1,structural_checks_passed=assessment['valid'],
            remaining_checks=feedback,trace_file=str((trace/item['view']['id']).relative_to(directory)))
        if not assessment['valid']:
            item['warnings'].append(f"{item['view']['title']}：来源/结构检查仍有 {len(assessment['feedback'])} 项矛盾，候选台账完整保留，未声明工程确认。")
        if quantity['matches'] is False:
            item['warnings'].append(f"{item['view']['title']}：第一阶段标准 {quantity['expected_total']} 个，识图 {quantity['actual_total']} 个，保留数量差异；未强行增删。")
        if not quantity['reference_available']:
            item['warnings'].append(f"{item['view']['title']}：缺少第一阶段独立数量标准，未作数量通过声明。")
        write_json(trace/item['view']['id']/'master_result.json',payload)
        write_json(trace/item['view']['id']/'assessment.json',{'structure':assessment,'quantity':quantity})
        for row in rows:audit.event('VIEW_MEMBER_RECOGNIZED',**row)
        emit(f"总代理完成：{item['view']['title']}，{len(rows)} 条记录；第一阶段数量标准 {quantity['expected_total']}。")
        if target_rows(rows):
            item['status']['length_expert']={'status':'queued','target_count':len(target_rows(rows))}
            entry,prompt=work_package(item)
            queue.appendleft(Task(item['view']['id'],'length_expert',entry,prompt))
        else:item['status']['length_expert']={'status':'skipped','target_count':0}

    def expert_failed(item,message):
        item['status'].update(status='completed_with_warnings',model_calls=item['calls'])
        item['status']['length_expert'].update(status='failed',error=message)
        item['warnings'].append(f"{item['view']['title']}：长度专家失败，保留总代理台账；{message}")
        for row in target_rows(item['rows']):row['length_expert_checks']=['长度专家调用失败，原长度与问题保留。']
        audit.event('LENGTH_EXPERT_FAILED',view_id=item['view']['id'],error=message)

    def expert_completed(item,payload,task):
        size=[item['plan']['width_px'],item['plan']['height_px']]
        assessment=assess_reply(payload,item['rows'],size)
        expert_path=trace/item['view']['id']/'length_expert'
        write_json(expert_path/f'round{task.round:02d}_assessment.json',assessment)
        need_context=payload.get('context_request')
        if task.round<1 and (need_context or assessment['feedback']):
            if need_context:
                crop=freeze_context(directory,item['view']['id'],Path(item['entry']['image_path']),need_context)
                item.setdefault('expert_contexts',[]).append(crop)
                item['status']['length_expert_context_images']=item['expert_contexts']
                audit.event('LENGTH_CONTEXT_EXPANDED',view_id=item['view']['id'],**crop)
            entry,prompt=work_package(item,previous=payload,feedback=assessment['feedback'],round=task.round+1)
            queue.appendleft(Task(task.view_id,'length_expert',entry,prompt,round=task.round+1))
            return
        if need_context:
            assessment['feedback'].append({'check':'context_budget','message':'扩展上下文已达一次上限；未解决记录及补图请求保留，不继续循环。'})
        revised,changes=apply_reply(item['rows'],assessment)
        item['rows']=validate_rows(revised,item['view'],one)
        expert_status=item['status']['length_expert']
        expert_status.update(status='completed_with_warnings' if assessment['feedback'] or any(not c['applied'] for c in changes) else 'completed',
            rounds=task.round+1,applied_count=sum(c['applied'] for c in changes),changed_count=sum(c['applied'] and c['before_mm']!=c['after_mm'] for c in changes),
            unknown_remaining=sum(r['length_mm'] is None for r in item['rows']),calibration=assessment['calibration'],
            feedback=assessment['feedback'],changes=changes)
        item['status'].update(status='completed_with_warnings' if expert_status['status']=='completed_with_warnings' or item['status'].get('remaining_checks') else 'completed',
                             model_calls=item['calls'])
        if assessment['feedback']:item['warnings'].append(f"{item['view']['title']}：长度专家仍有 {len(assessment['feedback'])} 项本地检查问题；原台账和有效修订保留。")
        write_json(expert_path/'result.json',payload);write_json(expert_path/'changes.json',changes)
        for change in changes:audit.event('LENGTH_RESULT_APPLIED' if change['applied'] else 'LENGTH_RESULT_RETAINED',view_id=task.view_id,**change)
        emit(f"长度专家完成：{item['view']['title']}，合入 {expert_status['applied_count']} 条，未知长度剩余 {expert_status['unknown_remaining']} 条。")

    audit.event('GRID_BATCH_STARTED',configured_concurrency=limit,views=len(prepared),grid_requests=len(queue),
                reason='网格、总代理、长度专家和重试共用一个就绪队列与线程池，无嵌套线程池。')
    for item in prepared:
        if 'replay' not in item:continue
        replay=item['replay'];payload=GridMasterResponse.model_validate(replay['payload']).model_dump(mode='json')
        assessment=assess_master({'view':{'plane_name':item['context']['current_view_tag']},**payload},item['located'],item['plan']['tiles'])
        quantity=assess_quantity(payload['members'],item['view'].get('quantity_reference'))
        audit.event('GRID_MASTER_REPLAYED',view_id=item['view']['id'],image_sha256=replay['image_sha256'],source=replay['source'],
                    reason='显式实验模式：冻结真实 master 输出，只对长度专家调用API，不模拟回答或修改旧记录。')
        write_json(trace/item['view']['id']/'recorded_master_source.json',replay)
        adopt(item,payload,assessment,quantity,0,assessment['feedback']+quantity['feedback'])
    emit(f"第二阶段网格识图：{len(prepared)} 个视图，{sum(len(i['plan']['tiles']) for i in prepared if 'plan' in i)} 个网格，共享请求并发上限 {limit}。")
    checkpoint()
    with ThreadPoolExecutor(max_workers=min(limit,len(queue)) or 1,thread_name_prefix='steel-grid') as executor:
        while queue or pending:
            dispatched=False
            while queue and len(pending)<limit:
                task=queue.popleft();item=by_id[task.view_id]
                item['calls']+=1;item['status']['model_calls']=item['calls']
                item['status']['status']={'child':'recognizing_grids','master':'coordinating','length_expert':'checking_lengths'}[task.kind]
                if task.kind=='length_expert':item['status']['length_expert'].update(status='running',round=task.round+1)
                future=executor.submit(request,task);pending[future]=task
                dispatched=True
                audit.event('GRID_REQUEST_QUEUED',view_id=task.view_id,kind=task.kind,tile_id=task.tile_id,
                    round=task.round,format_retry=task.format_retry,active_requests=len(pending),concurrency_limit=limit)
            if dispatched:checkpoint()
            completed,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in sorted(completed,key=lambda f:pending[f].name):
                task=pending.pop(future);item=by_id[task.view_id];reply=future.result()
                path=reply['path'];answer=None
                if path.exists():
                    try:answer=response_content(json.loads(path.read_text(encoding='utf-8')))
                    except (ValueError,KeyError,TypeError):pass
                if answer is not None:audit.text(trace/task.view_id/'answers'/f'{task.name}.reply.txt',answer)
                if 'error' in reply:
                    if task.format_retry<MAX_FORMAT_RETRIES:
                        feedback={'error':reply['error'],'previous_reply':answer}
                        prompt=task.prompt+'\n本次请求未通过解析或传输，以下是本地错误和不可信的旧回复：\n'+json.dumps(feedback,ensure_ascii=False)
                        prompt+='\n请结合原图片重新返回完整JSON，严格遵循schema，不增加额外字段（null也不允许），不能为修复格式改变图中数量或猜长度。'
                        queue.appendleft(replace(task,prompt=prompt,format_retry=task.format_retry+1))
                        audit.event('GRID_FORMAT_RETRY',view_id=task.view_id,task=task.name,error=reply['error'])
                        label=task.tile_id if task.kind=='child' else '长度专家' if task.kind=='length_expert' else '总代理'
                        emit(f"格式/请求重试：{item['view']['title']} {label}；其他有效回复保留。")
                        continue
                    if task.kind=='child':item['child_errors'][task.tile_id]=reply['error']
                    elif task.kind=='length_expert':expert_failed(item,reply['error'])
                    else:fail(item,reply['error'])
                else:
                    payload=task.contract.model_validate(reply['payload']).model_dump(mode='json')
                    write_json(trace/task.view_id/f'{task.name}.json',payload)
                    if task.kind=='child':item['children'][task.tile_id]=payload
                    elif task.kind=='length_expert':
                        try:expert_completed(item,payload,task)
                        except Exception as error:expert_failed(item,audit.scrub(str(error)))
                    else:
                        assessment=assess_master({'view':{'plane_name':item['context']['current_view_tag']},**payload},item['located'],item['plan']['tiles'])
                        quantity=assess_quantity(payload['members'],item['view'].get('quantity_reference'))
                        feedback=assessment['feedback']+quantity['feedback']
                        allowed=set(item['context']['axis_names'])
                        outside=sorted({tag for member in payload['members'] for tag in member['view_tags'] if tag not in allowed})
                        if outside:
                            feedback.append({'check':'stage_one_plane_names','message':'返回了第一阶段目录以外的面名称，请按原图核对并使用第一阶段完整命名，不能缩写或合成轴名。','outside':outside,'tile_ids':[]})
                        write_json(trace/task.view_id/f'{task.name}_checks.json',{'structure':assessment,'quantity':quantity,'feedback':feedback})
                        if feedback and task.round<MAX_MASTER_REPAIRS:
                            queue.appendleft(master_task(item,previous=payload,feedback=feedback,round=task.round+1))
                            audit.event('GRID_MASTER_REPAIR',view_id=task.view_id,round=task.round+1,feedback=feedback)
                        else:adopt(item,payload,assessment,quantity,task.round,feedback)
                if task.kind=='child':
                    done=len(item['children'])+len(item['child_errors'])
                    item['status']['children_completed']=done
                    emit(f"网格进度：{item['view']['title']} {done}/{len(item['plan']['tiles'])}。")
                    if done==len(item['plan']['tiles']):
                        write_json(trace/task.view_id/'child_results.json',item['children'])
                        if item['child_errors']:fail(item,'部分网格重试后仍失败，未用不完整网格结果生成台账')
                        else:
                            item['located']=locate_children(item['plan'],item['children'])
                            write_json(trace/task.view_id/'located_candidates.json',item['located'])
                            queue.appendleft(master_task(item))
                checkpoint()
    result=validate_ledger(result)
    audit.event('GRID_BATCH_FINISHED',configured_concurrency=limit,elapsed_seconds=round(time.monotonic()-started,3),
        completed_views=sum(i['status']['status'] in ('completed','completed_with_warnings','empty') for i in prepared),
        failed_views=sum(i['status']['status']=='failed' for i in prepared),requests=sum(i['calls'] for i in prepared))
    return result

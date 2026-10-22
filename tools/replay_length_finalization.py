"""Reapply current deterministic finalization to immutable real replies, no API.

All views are processed by the same policy. No reference answers or hand-edited
values are accepted. A separate job preserves the original full-run outcome.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.audit import AuditLog
from app.length_arbitration import LengthArbitration
from app.member_recognition import validate_rows,validate_ledger
from app.native_pdf import write_json
from app.pipeline import execute
from app.store import JobStore


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--source-job',required=True)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    if len(args.source_job)!=32 or any(c not in '0123456789abcdef' for c in args.source_job):
        parser.error('Invalid source job ID')
    source_store=JobStore(args.source_root/'data/jobs_v2.sqlite3')
    if source_store.get(args.source_job)['state']!='completed':
        parser.error('Source must have completed inference/export before replay')
    source=args.source_root/'data/jobs'/args.source_job
    one=source_store.stage(args.source_job,1)['data']
    before=source_store.stage(args.source_job,2)['data']
    digest=lambda data:hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    original_hash=digest([one,before])
    two=deepcopy(before)
    if two['grid_configuration']['length_review_policy']['mode']!='economy':
        parser.error('This bounded replay supports economy-mode source jobs')
    identity=uuid.uuid4().hex
    directory=args.root/'data/jobs'/identity
    directory.mkdir(parents=True)
    for name in ('uploads','views','native','responses','grids','grid_runs'):
        if (source/name).is_dir():shutil.copytree(source/name,directory/name)
    for name in ('pdf_manifest.json','native_index.json'):
        if (source/name).is_file():shutil.copy2(source/name,directory/name)
    write_json(directory/'workflow.json',{'auto_run':True,'stop_after_stage':4})
    store=JobStore(args.root/'data/jobs_v2.sqlite3')
    store.create(identity);store.claim(identity,1);store.complete_stage(identity,1,one)
    store.approve(identity,1,1,True,reason='自动复用源任务已完成的第一阶段，未重新推理。')
    store.claim(identity,2)
    audit=AuditLog(directory,stage=2)
    audit.event('DETERMINISTIC_REPLAY_STARTED',source_job=args.source_job,source_hash=original_hash,
                new_api_calls=0,reference_inputs=False,manual_candidate_selection=False)
    views={v['id']:v for v in one['views']}
    changes=[];proof=[]
    for analysis in two['view_analysis']:
        trace=source/analysis['trace_file'];path=trace/'arbitration/result.json'
        if not path.is_file():continue
        saved=read(path);verdict=saved.get('verdict')
        if verdict is None:continue
        gate=read(trace/'arbitration/source_gate.json')
        targets=gate['pending_ids']
        if not targets:continue
        meta=read(trace/'arbitration/length_arbiter_input00.json')
        grid=read(trace/'input.json')['grid']
        view=views[analysis['view_id']]
        image=directory/'views'/meta['image_map'][0]['image_file']
        if hashlib.sha256(image.read_bytes()).hexdigest()!=grid['input_sha256']:
            raise ValueError('Source image hash mismatch; replay aborted')
        item={'view':view,'directory':directory,'rows':deepcopy(gate['rows']),
              'length_target_ids':targets,'length_policy':before['grid_configuration']['length_review_policy'],
              'drawing_facts':meta['drawing_facts'],'plan':grid,'context':meta['stage_one_context'],
              'entry':{'image_path':str(image)},'status':analysis,'calls':analysis['model_calls']}
        original_target_count=analysis['length_arbitration']['target_count']
        controller=LengthArbitration(item,[item],None,directory/'replay_runs',audit,lambda _:None)
        controller.status['target_count']=original_target_count
        controller.candidates={r['id']:deepcopy(r['length_arbitration']['candidates'])
            for r in saved['rows'] if r['id'] in targets}
        controller.images=meta.get('evidence_images',[])
        controller.sent_image_ids={i.get('evidence_id') or i.get('tile_id') or
            ('original' if i['role']=='original' else i['image_file']) for i in meta['image_map']}
        controller.verifier_rounds=1
        result=controller.accept('length_arbiter',verdict)
        if result is not None:raise ValueError('Replay requested another model round; no API dispatched')
        rows=validate_rows(item['rows'],view,one)
        original=[r for r in before['view_members'] if r['source_view']==view['id']]
        identity_keys=lambda values:[(r['id'],r['spec'],r['member_cnt'],r['view_tags'],r['source_view']) for r in values]
        if identity_keys(rows)!=identity_keys(original):
            raise ValueError('Replay changed identity/count/spec/tags; aborted')
        mapped={r['id']:r for r in rows}
        two['view_members']=[mapped.get(r['id'],r) for r in two['view_members']]
        for old in original:
            new=mapped[old['id']]
            if old['length_mm']!=new['length_mm'] or old.get('length_arbitration',{}).get('status')!=new.get('length_arbitration',{}).get('status'):
                changes.append({'row_id':old['id'],'before_mm':old['length_mm'],'after_mm':new['length_mm'],
                    'before_status':old.get('length_arbitration',{}).get('status'),
                    'after_status':new.get('length_arbitration',{}).get('status')})
        analysis['length_finalization_replayed']=True
        proof.append({'view_id':view['id'],'raw_verdict_sha256':hashlib.sha256((trace/'arbitration/length_arbiter_reply00.json').read_bytes()).hexdigest(),
                      'source_trace':analysis['trace_file'],'replay_trace':str(controller.path.relative_to(directory))})
    two=validate_ledger(two)
    two['grid_configuration']['deterministic_replay']={'source_job':args.source_job,'new_api_calls':0}
    write_json(directory/'acceptance/replay.json',{'source_job':args.source_job,'job_id':identity,
        'source_hash':original_hash,'new_api_calls':0,'reference_inputs':False,
        'manual_candidate_selection':False,'proof':proof,'changes':changes})
    audit.event('DETERMINISTIC_REPLAY_COMPLETED',changed_rows=len(changes),new_api_calls=0)
    store.complete_stage(identity,2,two)
    store.approve(identity,2,1,True,reason='自动重放本次原始AI裁决，未读取参考答案或人工修改台账。')
    store.claim(identity,3);execute(store,identity,3,directory,{})
    store.approve(identity,3,1,True,reason='自动完整key去重后导出。')
    store.claim(identity,4);execute(store,identity,4,directory,{})
    if digest([source_store.stage(args.source_job,1)['data'],source_store.stage(args.source_job,2)['data']])!=original_hash:
        raise ValueError('Source snapshots changed during replay')
    print(json.dumps({'job_id':identity,'source_job':args.source_job,'changes':len(changes),
        'new_api_calls':0,'totals':store.stage(identity,3)['data']['totals']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

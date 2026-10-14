"""Real API acceptance for both stages, using a fresh copy of an existing job's inputs."""
import argparse
import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.store import JobStore
from app.pipeline import execute
from app.audit import AuditLog
from app.native_pdf import write_json
from app.domain import StageOne
from app.image_workflow import freeze_images


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-job',required=True)
    parser.add_argument('--reuse-prepared',action='store_true',help='复用源任务已定位的第一阶段目录，补齐同视图放大图，仅重新识别构件')
    parser.add_argument('--reasoning-effort',choices=('high','max'),help='默认首次 high，复用目录重新识别 max')
    args=parser.parse_args()
    effort=args.reasoning_effort or ('max' if args.reuse_prepared else 'high')
    if len(args.source_job)!=32 or any(c not in '0123456789abcdef' for c in args.source_job):parser.error('无效任务ID')
    source=ROOT/'data/jobs'/args.source_job
    if not (source/'uploads').is_dir():parser.error('找不到源任务输入')
    identity=uuid.uuid4().hex;folder=ROOT/'data/jobs'/identity
    (folder/'uploads').mkdir(parents=True)
    for pdf in (source/'uploads').glob('*.pdf'):shutil.copyfile(pdf,folder/'uploads'/pdf.name)
    load_dotenv(ROOT/'.env')
    store=JobStore(ROOT/'data/jobs_v2.sqlite3');store.create(identity)
    print(f'验证任务：{identity}，原任务不变。',flush=True)
    store.claim(identity,1)
    try:
        if args.reuse_prepared:
            from app.pipeline import extract
            seed=StageOne.model_validate(store.stage(args.source_job,1)['data']).model_dump()
            if seed['workflow']!='image_first':raise ValueError('只能复用新流程的第一阶段目录')
            extract(folder,lambda text:print(text,flush=True))
            (folder/'views').mkdir(exist_ok=True)
            for view in seed['views']:
                for name in [view['image_file'],*view.get('detail_files',[])]:
                    shutil.copyfile(source/'views'/Path(name).name,folder/'views'/Path(name).name)
            freeze_images(folder,seed);store.complete_stage(identity,1,seed)
        else:execute(store,identity,1,folder,{'reasoning_effort':effort})
    except Exception as exc:
        store.fail(identity,1,str(exc));raise
    one=store.stage(identity,1)
    if any(not v['region_confirmed'] for v in one['data']['views']):
        print('第一阶段仍有裁切待确认，保留结果，未执行构件识别。',flush=True);return
    # This explicit approval verifies mechanics, not engineering quantities.
    store.approve(identity,1,one['revision'],True,reason='[FLOW_VERIFICATION] 自动边界检查通过，验证两阶段衔接；不是工程数量确认。')
    store.claim(identity,2)
    try:execute(store,identity,2,folder,{'reasoning_effort':effort})
    except Exception as exc:
        store.fail(identity,2,str(exc));raise
    two=store.stage(identity,2)['data'];results=[]
    for view in two['views']:
        if view['kind']=='ISOMETRIC':continue
        rows=[r for r in two['view_members'] if r['source_view']==view['id']]
        results.append({'view':view['title'],'rows':len(rows),'member_cnt':sum(r['member_cnt'] for r in rows),
                        'unknown_length_rows':sum(r['length_mm'] is None for r in rows),
                        'issue_rows':sum(bool(r['checks']) for r in rows),
                        'image_file':view['image_file'],
                        'image_sha256':hashlib.sha256((folder/'views'/view['image_file']).read_bytes()).hexdigest()})
    report={'job_id':identity,'source_job':args.source_job,'workflow':'image_first','reasoning_effort':effort,
            'views':results,'warnings':two['warnings'],'engineering_verified':False,
            'note':'新任务验证API与衔接；无人工全图台账基准，不宣称数量与长度正确。'}
    write_json(folder/'image_workflow_report.json',report)
    AuditLog(folder,2).event('IMAGE_WORKFLOW_VERIFIED',**report)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()

"""Fresh paid-API run; stage 1-3 inputs never include independent reference answers."""
import argparse, hashlib, json, shutil, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.store import JobStore
from app.pipeline import execute
from app.native_pdf import write_json
from app.ai_concurrency import view_concurrency
from app.group_reconciliation import reconcile
from app.accuracy import score
from app.accuracy_breakdown import breakdown


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-job',required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--suffixes',default='02,03,04,05')
    args=parser.parse_args()
    if len(args.source_job)!=32 or any(c not in '0123456789abcdef' for c in args.source_job):parser.error('无效任务ID')
    load_dotenv(ROOT/'.env')
    limit=view_concurrency(); identity=uuid.uuid4().hex; folder=ROOT/'data/jobs'/identity
    (folder/'uploads').mkdir(parents=True)
    selected=set(args.suffixes.split(',')); sources=[]
    for pdf in sorted((ROOT/'data/jobs'/args.source_job/'uploads').glob('*.pdf')):
        if any(pdf.name.endswith(f'-{suffix}_00.pdf') for suffix in selected):
            shutil.copyfile(pdf,folder/'uploads'/pdf.name);sources.append(pdf.name)
    if len(sources)!=len(selected):raise ValueError('未找到全部指定图纸')
    report=dict(job_id=identity,source_job=args.source_job,started_at=datetime.now(timezone.utc).isoformat(),
        input_files=sources,max_concurrency=limit,reasoning_effort='max',timings_seconds={},
        reference_sent_to_api=False,manual_result_corrections=0)
    store=JobStore(ROOT/'data/jobs_v2.sqlite3');store.create(identity)
    start=time.perf_counter();report_path=folder/'full_acceptance.json'
    write_json(report_path,report);print(json.dumps(report,ensure_ascii=False),flush=True)
    current=1
    try:
        for current in (1,2,3):
            if not store.claim(identity,current):raise RuntimeError('阶段认领失败')
            print(f'START stage {current}',flush=True);t=time.perf_counter()
            execute(store,identity,current,folder,{'reasoning_effort':'max','review_mode':'ai'})
            report['timings_seconds'][f'stage_{current}']=time.perf_counter()-t
            stage=store.stage(identity,current)
            if current==3 and not stage['data']['review']['ai_proposal'].get('applied'):
                proposed=stage['data']['review']['ai_proposal']
                if not proposed['validation']['valid']:raise ValueError('第三阶段提案本地验证失败：'+proposed['validation'].get('error',''))
                draft={**stage['data'],**{k:proposed['proposal'][k] for k in ('resolutions','exclusions')}}
                store.edit(identity,3,stage['revision'],reconcile(store.stage(identity,2)['data'],draft),
                           '[AUTOMATED_BENCHMARK] 原样载入通过守恒校验的AI提案；尚未与参考答案比对，未人工纠错。')
                stage=store.stage(identity,3)
            store.approve(identity,current,stage['revision'],True,
                          reason='[AUTOMATED_BENCHMARK] 用户授权完整测试，允许待确认项继续；不代表人工工程验收。')
            write_json(report_path,report);print(f'FINISH stage {current}: {report["timings_seconds"][f"stage_{current}"]:.2f}s',flush=True)
        current=4;store.claim(identity,4);t=time.perf_counter()
        execute(store,identity,4,folder,{})
        report['timings_seconds']['export']=time.perf_counter()-t
        report['timings_seconds']['pipeline_total']=time.perf_counter()-start
        # Reference is first opened after all AI calls and export finish.
        reference=json.loads(args.reference.read_text(encoding='utf-8'))
        actual={p.name.split('_',1)[1]:hashlib.sha256(p.read_bytes()).hexdigest() for p in (folder/'uploads').glob('*.pdf')}
        if any(reference['scope']['pdf_sha256'].get(name)!=sha for name,sha in actual.items()):raise ValueError('参考图纸SHA不一致')
        if any(r.get('source_pdf') not in actual for r in reference['members']):raise ValueError('参考台账包含测试范围外构件，不能静默改变基准')
        reference['scope'].update(pdf_sha256=actual,expected_plan_count=4,expected_isometric_count=0)
        reference['scope']['subset_note']='02-05；01基础截面投影不新增构件；原参考构件未改动。'
        acceptance=folder/'acceptance';acceptance.mkdir(exist_ok=True)
        write_json(acceptance/'scoped_reference.json',reference)
        predictions=store.stage(identity,3)['data']['members']
        report['accuracy']=score(reference,predictions);report['breakdown']=breakdown(reference,predictions)
        report['reference_reviewer']=reference.get('reviewer')
        report['user_approved_engineering_baseline']=reference.get('user_approved_engineering_baseline',False)
        report['reference_sha256']=hashlib.sha256(args.reference.read_bytes()).hexdigest()
        report['status']='completed'
        a=report['accuracy'];b=report['breakdown']
        lines=['# 四份图纸完整测试','',f'任务：{identity}',
            '基准来源：此前由助手直接核对原PDF建立；用户确认采用图示名义长度，尚无用户独立人工台账。',
            '本轮未向模型提供参考答案，未依据参考答案纠正模型结果。','',
            f'参考 {a["expected_single_members"]} 根，输出 {a["reported_single_members"]} 根，严格匹配 {a["matched_single_members"]} 根。',
            f'Precision {a["precision"]:.2%}，Recall {a["recall"]:.2%}，F1 {a["f1"]:.2%}。',
            f'钢材种类：参考 {b["expected_profile_count"]}，输出 {b["reported_profile_count"]}；种类Precision {b["profile_precision"]:.2%}，Recall {b["profile_recall"]:.2%}，集合正确率 {b["profile_set_accuracy"]:.2%}。','',
            '|阶段|耗时（秒）|','|---|---:|']
        lines += [f'|{k}|{v:.2f}|' for k,v in report['timings_seconds'].items()]
        lines += ['',b['length_pairing_rule'],'',
            '|规格|参考根数|输出根数|数量误差|严格Precision|严格Recall|长度配对数|长度≤1mm比例|MAE mm|最大误差 mm|MAPE|',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        def value(x,percent=False):return '—' if x is None else (f'{x:.2%}' if percent else f'{x:.2f}')
        for r in b['profiles']:
            lines.append(f'|{r["profile"]}|{r["expected_count"]}|{r["reported_count"]}|{r["count_error"]:+d}|{r["precision"]:.2%}|{r["recall"]:.2%}|{r["length_paired_count"]}|{value(r["length_accuracy"],True)}|{value(r["length_mae_mm"])}|{value(r["length_max_error_mm"])}|{value(r["length_mape"],True)}|')
        (acceptance/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    except Exception as error:
        # Do not print credentials/provider request bodies on failures.
        report['status']='failed';report['failure_type']=type(error).__name__
        if current<=4:store.fail(identity,current,'完整测试失败：'+type(error).__name__+'；详见已保存阶段与审计记录。')
        raise
    finally:
        report['timings_seconds']['wall_total']=time.perf_counter()-start
        write_json(report_path,report)
        print(json.dumps({'job_id':identity,'status':report.get('status'),'report':str(report_path)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()

"""Read-only independent acceptance: no reference answers are sent to an API."""
import argparse,hashlib,json,sqlite3,sys,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.accuracy import score
from app.group_reconciliation import source_hash
from app.native_pdf import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--proposal',type=Path,help='离线提案report.json；不将提案标记为正式结果')
    args=parser.parse_args()
    if len(args.job_id)!=32 or any(c not in '0123456789abcdef' for c in args.job_id):parser.error('无效任务ID')
    reference=json.loads(args.reference.read_text(encoding='utf-8'))
    if not reference.get('scope',{}).get('pdf_sha256'):parser.error('参考台账必须提供原PDF校验值')
    uploads=ROOT/'data/jobs'/args.job_id/'uploads'
    actual_files={f.name.split('_',1)[1]:hashlib.sha256(f.read_bytes()).hexdigest() for f in uploads.glob('*.pdf')}
    if actual_files!=reference['scope']['pdf_sha256']:parser.error('参考台账范围与任务原PDF集合不一致')
    with sqlite3.connect((ROOT/'data/jobs_v2.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        two=db.execute('SELECT data FROM stages WHERE job_id=? AND stage=2',(args.job_id,)).fetchone()
        three=db.execute('SELECT revision,status,data FROM stages WHERE job_id=? AND stage=3',(args.job_id,)).fetchone()
    if not two or not three:parser.error('任务需要第二和第三阶段结果')
    data=json.loads(three[2]);kind='persisted_stage_three'
    if args.proposal:
        report=json.loads(args.proposal.read_text(encoding='utf-8'))
        data=report.get('data');kind='offline_proposal_only'
        if not data:parser.error('提案未通过数量守恒等本地检查，不能验收')
    if data.get('source_hash')!=source_hash(json.loads(two[0])):parser.error('第三阶段或提案不是基于当前第二阶段，拒绝错版本验收')
    result=score(reference,data['members'])
    with sqlite3.connect((ROOT/'data/jobs_v2.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        unchanged=(two==db.execute('SELECT data FROM stages WHERE job_id=? AND stage=2',(args.job_id,)).fetchone()
            and three==db.execute('SELECT revision,status,data FROM stages WHERE job_id=? AND stage=3',(args.job_id,)).fetchone())
    if not unchanged:result['passed_90_percent']=False
    result.update(job_id=args.job_id,stage_three_revision=three[0],stage_three_status=three[1],
                  result_kind=kind,reference_sha256=hashlib.sha256(args.reference.read_bytes()).hexdigest(),
                  reference_reviewer=reference.get('reviewer'),source_unchanged=unchanged,api_calls=0)
    output=ROOT/'tmp/acceptance'/args.job_id/('score-'+uuid.uuid4().hex[:8]);output.mkdir(parents=True)
    write_json(output/'comparison.json',result)
    def pct(value):return f'{value:.2%}'
    lines=['# 独立参考台账验收','',f'结果类别：{kind}；来源阶段状态：{three[1]}。',
           f"参考 {result['expected_single_members']} 根；输出 {result['reported_single_members']} 根；规格、名义长度、完整所属面集合同时匹配 {result['matched_single_members']} 根。",
           f"准确率 precision：{pct(result['precision'])}；召回率 recall：{pct(result['recall'])}；F1：{pct(result['f1'])}。",
           f"全范围90%验收通过：{result['passed_90_percent']}。",'',
           '该数值基于声明范围和参考台账；按组数量匹配，不能证明每个同组单件的精确位置或加工下料长度。',
           '未确认长度、漏项、错误规格和重复多计均不会记为正确。程序测试通过、模型自评及总量相等都不替代此验收。','',
           '| 类别 | 规格 | 长度 mm | 单根数量 | 所属面 |','| --- | --- | ---: | ---: | --- |']
    for kind,key in [('漏项或不符','missing'),('多计或不符','extra')]:
        for row in result[key]:lines.append(f"| {kind} | {row['profile']} | {row['length_mm']} | {row['quantity']} | {', '.join(row['view_tags'])} |")
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),**{k:result[k] for k in ('reported_single_members','matched_single_members','precision','recall','f1','passed_90_percent')}},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

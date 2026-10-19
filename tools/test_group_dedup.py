"""Offline third-stage trial; SQLite is opened read-only and no API is called."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.group_dedup import build_group_review


def read_stage(job):
    with sqlite3.connect((ROOT/'data/jobs_v2.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        result=db.execute('SELECT revision,status,data FROM stages WHERE job_id=? AND stage=2',(job,)).fetchone()
    if result is None:raise ValueError('找不到第二阶段台账')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if len(args.job_id)!=32 or any(c not in '0123456789abcdef' for c in args.job_id):parser.error('无效任务ID')
    before=read_stage(args.job_id);data=json.loads(before[2])
    if before[1]!='awaiting_review':parser.error('请使用已完成、等待核对的第二阶段台账')
    if data.get('recognition_mode')!='view_ledger':parser.error('本工具只用于逐视图台账')
    output=args.output or ROOT/'tmp/group_dedup'/args.job_id/f'stage2-r{before[0]}-{uuid.uuid4().hex[:8]}'
    output.mkdir(parents=True,exist_ok=False)
    report=build_group_review(data)
    report['source']={'job_id':args.job_id,'stage':2,'revision':before[0],'status':before[1],
                      'data_sha256':hashlib.sha256(before[2].encode()).hexdigest(),'api_calls':0,
                      'code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                     for name in ('app/group_dedup.py','app/member_recognition.py','app/profiles.py','tools/test_group_dedup.py')}}
    (output/'stage_two_snapshot.json').write_text(before[2],encoding='utf-8')
    after=read_stage(args.job_id)
    report['source_unchanged']=before==after
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=report['summary']
    lines=['# 第三阶段离线去重试验','',f'任务：{args.job_id}；第二阶段版本：{before[0]}。',
        '', '**这是候选分组试算，没有确认任何构件，没有修改任务或调用AI，不是最终工程数量。**',
        '', '| 检查项 | 结果 |','| --- | ---: |',
        *[f'| {label} | {summary[key]} |' for key,label in [
            ('input_rows','输入台账行'),('valid_observation_units','原始观察单元（2LF按组合）'),
            ('cross_source_groups','跨来源同键组'),('count_conflicts','同键数量冲突组'),
            ('eligible_exact_candidates','通过机械筛查的同键候选组'),
            ('trial_removed_observations','同键试算扣除的重复观察'),
            ('trial_remaining_observations','仅按以上规则试算后保留的观察单元'),
            ('single_tag_groups','单标签组'),('missing_length_groups','缺少长度的组'),
            ('pending_source_views','未完成识别的视图')]],
        '',f"源台账运行前后完全一致：{report['source_unchanged']}。",'',
        '试算不处理未知部分重叠，也不展开组合角钢。存在覆盖不完整、标签不完整、长度口径或分段冲突，因而不能从试算数字得到最终数量。',
        '', '## 同键数量对照','',
        '| 规格 | 长度mm | 面名称 | 各来源数量 | 状态 | 核验信息 |',
        '| --- | ---: | --- | --- | --- | --- |']
    def cell(value):return str(value or '').replace('|','/').replace('\n',' ')
    for group in report['groups']:
        if len(group['sources'])<2:continue
        sources='；'.join((f"[{s['plane_tag']}：{s['member_cnt']}](http://127.0.0.1:8120/api/jobs/{args.job_id}/views/{s['image_file']})"
                           if s['image_file'] else f"{s['plane_tag']}：{s['member_cnt']}") for s in group['sources'])
        state='同键候选（仍待确认）' if group['eligible_for_exact_trial'] else '需核验，不扣减'
        lines.append('| '+' | '.join(map(cell,[group['spec'],group['length_mm'],'，'.join(group['view_tags']),sources,state,'；'.join(group['review_reasons'])]))+' |')
    lines+=['', '## 长度或分段分布差异','', '| 规格 | 面名称 | 各来源的长度×数量 |', '| --- | --- | --- |']
    for issue in report['distribution_issues']:
        details='；'.join(s['view_id']+'：'+', '.join(f"{r['length_mm']}×{r['member_cnt']}" for r in s['lengths']) for s in issue['sources'])
        lines.append('| '+' | '.join(map(cell,[issue['profile'],'，'.join(issue['view_tags']),details]))+' |')
    lines+=['', '## 规格分布差异','', '| 面名称 | 长度mm | 各来源的规格×数量 |', '| --- | ---: | --- |']
    for issue in report['profile_distribution_issues']:
        details='；'.join(s['view_id']+'：'+', '.join(f"{p}×{n}" for p,n in s['profiles'].items()) for s in issue['sources'])
        lines.append('| '+' | '.join(map(cell,['，'.join(issue['view_tags']),issue['length_mm'],details]))+' |')
    lines+=['', '## 未完成的来源视图','', *[f"- {v['title']}：{v['status']}" for v in report['pending_sources']],
        '', '## 其他核验候选','',
        f"标签不完整/舍入关系共 {len(report['relations'])} 对；长度或分段分布差异共 {len(report['distribution_issues'])} 组。", 
        '这些关系只进入核验清单，不做逐对减数；不同类别可能涉及同一批观察，不能相加当作独立错误数。完整来源行ID、位置说明及图片文件名见 report.json。',
        '', '## 下一步', '', '核对数量冲突、长度口径和来源覆盖后，才能确认结果组；确认后才可展开2LF为单根角钢。']
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'source_unchanged':report['source_unchanged'],
                      'summary':summary,'relations':len(report['relations']),
                      'distribution_issues':len(report['distribution_issues']),
                      'profile_distribution_issues':len(report['profile_distribution_issues'])},ensure_ascii=False,indent=2))
    if not report['source_unchanged']:raise SystemExit('测试期间源台账已被修改，本报告仅适用于保存的快照。')


if __name__=='__main__':main()

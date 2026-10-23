"""Inspectable per-label length ledger; counts are observations, not global MTO."""
from collections import Counter
import csv
import html
import json
from openpyxl import Workbook


def export(directory, report):
    rows = report['members']
    fields = ['view_title','label_id','spec','view_tags','member_cnt','length_mm','kind',
              'dimension_values_mm','formulas','endpoints','basis','issue','status','selected_role','local_checks']
    def values(row):
        return [json.dumps(row.get(k),ensure_ascii=False) if isinstance(row.get(k),(list,dict)) else row.get(k) for k in fields]
    with (directory/'members.csv').open('w',encoding='utf-8-sig',newline='') as target:
        writer = csv.writer(target); writer.writerow(fields)
        writer.writerows(values(row) for row in rows)
    counts = Counter((r['view_title'],r['spec'],r['length_mm']) for r in rows)
    summary_rows = [[title,spec,length,count] for (title,spec,length),count in sorted(
        counts.items(),key=lambda kv:(kv[0][0],kv[0][1],kv[0][2] is None,kv[0][2] or 0))]
    book = Workbook(); ledger = book.active; ledger.title = 'Per-label ledger'
    ledger.append(fields)
    for row in rows: ledger.append(values(row))
    summary = book.create_sheet('Per-view summary'); summary.append(['view_title','spec','length_mm','member_cnt'])
    for row in summary_rows: summary.append(row)
    note = book.create_sheet('Scope'); note.append(['Stage 2 observations, no cross-view deduplication or combination expansion.'])
    note.append(['Dimensions are calculated from native numeric text; graphical association remains a model claim.'])
    for sheet in (ledger,summary):
        sheet.freeze_panes = 'A2'; sheet.auto_filter.ref = sheet.dimensions
    book.save(directory/'stage_two.xlsx')
    escape = lambda value: html.escape(str(value),quote=True)
    sections = []
    for view in report['views']:
        local = [r for r in rows if r['view_id'] == view['view_id']]
        body = []
        for number,row in enumerate(local,1):
            length = f"{row['length_mm']:.2f}" if row['length_mm'] is not None else 'UNKNOWN'
            details = {'endpoints':row['endpoints'],'dimension_values_mm':row['dimension_values_mm'],
                       'formulas':row['formulas'],'basis':row['basis'],'issue':row['issue'],
                       'local_checks':row['local_checks'],'selected_role':row['selected_role'],
                       'review_notes':row.get('review_notes',[]),'arbitration':row.get('arbitration')}
            body.append(f'<tr><td>{number}</td><td>{escape(row["spec"])}</td><td>{length}</td>'
                f'<td>{escape(row["view_tags"])}</td><td>{escape(row["status"])}</td><td>{escape(row["label_id"])}'
                f'<details><summary>尺寸依据与问题</summary><pre>{escape(json.dumps(details,ensure_ascii=False,indent=2))}</pre></details></td></tr>')
        known = sum(r['length_mm'] is not None for r in local)
        sections.append(f'<section><h2>{escape(view["title"])}</h2><p>第一阶段 {view["expected_count"]} 条标签；'
            f'台账 {len(local)} 条；有长度 {known}，UNKNOWN {len(local)-known}；状态 {escape(view["state"])}</p>'
            f'<details><summary>第一阶段高清图</summary><img src="{escape(view["image_file"])}" style="width:100%"></details>'
            f'<p>{escape(view["warnings"])}</p><table><thead><tr><th>序号</th><th>规格</th><th>长度mm</th><th>view_tags</th><th>状态</th><th>标签ID与依据</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></section>')
    text = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>固定标签第二阶段</title>
<style>body{{font-family:system-ui;margin:24px;color:#172e43;line-height:1.6}}table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border:1px solid #ccd6df;padding:8px;text-align:left;vertical-align:top}}th{{background:#eef4f9}}pre{{white-space:pre-wrap;max-width:650px}}section{{margin:35px 0}}</style>
<h1>固定标签第二阶段 · 尺寸台账</h1><p>标签、规格、所属视图与已有view_tags全部继承第一阶段。未做跨视图去重，组合未展开。</p>
<p>{escape(report['summary'])}</p><p>MODEL_DERIVED表示模型解释及本地计算结果，不是人工确认。UNKNOWN保留台账，不补零、不删行。</p>
<p><a href="stage_two.xlsx">下载Excel</a> · <a href="members.csv">CSV台账</a> · <a href="stage_two.json">完整JSON与候选审计</a></p>
{''.join(sections)}</html>'''
    (directory/'index.html').write_text(text,encoding='utf-8')

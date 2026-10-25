"""Human inspection artifacts; no answer selection or recognition changes."""
import csv
import html
import json
from pathlib import Path

from app.drawing_input import render_png


def escape(value):
    return html.escape(str(value), quote=True)


def export(output: Path, source: Path, report):
    fields = ['view_title', 'plane_name', 'number', 'spec', 'raw_text', 'member_cnt',
              'view_tags', 'tag_status', 'source_text_id', 'bbox', 'axis_context']
    with (output / 'labels.csv').open('w', encoding='utf-8-sig', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for view in report['views']:
            for label in view['labels']:
                row = {key: label.get(key, '') for key in fields}
                row.update(view_title=view['title'], plane_name=view['plane_name'])
                for key in ('view_tags', 'bbox', 'axis_context'):
                    row[key] = json.dumps(row[key], ensure_ascii=False)
                writer.writerow(row)
    sections = []
    for view in report['views']:
        name = view['id'] + '_300dpi.png'
        path = output / name
        path.write_bytes(render_png({'source_path': str(source), 'page': next(
            p['page'] for p in report['page_sources'] if p['id'] == view['page_id']), 'crop': view['bbox']}, dpi=300))
        x0, y0, x1, y1 = view['bbox']
        w = (x1-x0)*view['width_pt']
        h = (y1-y0)*view['height_pt']
        marks, rows = [], []
        for label in view['labels']:
            b = label['bbox']
            x, y = (b[0]-x0)*view['width_pt'], (b[1]-y0)*view['height_pt']
            bw, bh = (b[2]-b[0])*view['width_pt'], (b[3]-b[1])*view['height_pt']
            identity = f"{view['id']}-n{label['number']}"
            color = '#c026d3' if label['axis_match'] else '#b45309'
            marks.append(f'<g id="{identity}" class="mark"><title>{escape(label["raw_text"])} {escape(label["view_tags"])}</title>'
                         f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" stroke="{color}"/>'
                         f'<text x="{x+bw+1}" y="{y+5}">{label["number"]}</text></g>')
            context = '<br>'.join(escape(direction)+': '+escape(position['before'] or '—')+' → '+
                                  escape(position['after'] or '—') for direction, position in label['axis_context'].items())
            rows.append(f'<tr><td><a href="#{identity}">{label["number"]}</a></td>'
                        f'<td>{escape(label["raw_text"])}</td><td>{escape(label["spec"])}</td>'
                        f'<td>{escape(json.dumps(label["view_tags"], ensure_ascii=False))}</td>'
                        f'<td>{escape(label["tag_status"])}<details><summary>轴间位置</summary>{context}</details></td>'
                        f'<td>{escape(label["source_text_id"])}</td></tr>')
        axis_table = []
        for axis in view['axes']:
            line = axis['line']
            axis_table.append(f'<tr><td>{escape(axis["name"])}</td><td>{escape(axis["source_text_id"])}</td>'
                              f'<td>{escape(line["vector_id"] if line else "未找到参考线")}</td></tr>')
            if line:
                a, b = line['points']
                coords = [(p[0]-x0)*view['width_pt'] for p in (a, b)]
                ys = [(p[1]-y0)*view['height_pt'] for p in (a, b)]
                marks.insert(0, f'<line class="axis" x1="{coords[0]}" y1="{ys[0]}" x2="{coords[1]}" y2="{ys[1]}"/>')
        sections.append(f'<section><h2>{escape(view["title"])}</h2><p>标签数：{view["label_count"]}；'
                        f'规格统计：{escape(view["spec_counts"])}</p>'
                        f'<details open><summary>300 DPI 原图与解析位置（可切换原始大小）</summary><div class="viewport">'
                        f'<svg viewBox="0 0 {w} {h}" style="--native-width:{w*300/72}px" onclick="this.classList.toggle(\'zoom\')">'
                        f'<image href="{escape(name)}" width="{w}" height="{h}"/>{"".join(marks)}</svg></div></details>'
                        f'<h3>逐标签台账</h3><table><thead><tr><th>序号</th><th>原始文字</th><th>规格</th>'
                        f'<th>view_tags</th><th>轴面匹配状态</th><th>原生文字ID</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
                        f'<details><summary>轴面及参考线证据</summary><table>{"".join(axis_table)}</table></details></section>')
    text = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>PDF 原生标签与轴面实验</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#172e43}}table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{padding:8px;border:1px solid #ccd6df;text-align:left}}th{{background:#eef4f9}}section{{margin:32px 0}}
.viewport{{overflow:auto;max-height:80vh;border:1px solid #ccd6df}}svg{{width:100%;display:block}}svg.zoom{{width:var(--native-width)}}
.mark rect{{fill:none;stroke-width:.5}}.mark text{{fill:#aa0088;font-size:6px;font-weight:bold;paint-order:stroke;stroke:white;stroke-width:1px}}
.mark:target rect{{fill:yellow;fill-opacity:.4;stroke:red;stroke-width:2}}.axis{{stroke:#006dc0;stroke-width:.5;opacity:.5;stroke-dasharray:3 2}}
code{{overflow-wrap:anywhere}}a{{color:#007b90}}</style>
<h1>PDF 原生标签与轴面实验</h1><p>输入：{escape(source.name)}</p>
<p>API 仅定位区域；下列数量与标签来自本地原生 PDF 文字解析。验收标准未参与识别。</p>
<p>统计：{escape(report['summary'])}</p><p>验收：{escape(report['acceptance'])}</p>
<p><a href="labels.csv">CSV 台账</a> · <a href="report.json">JSON 及证据</a> · <a href="regions.json">API 定位结果</a></p>
<p>紫框：匹配到命名轴线（邻近关系候选）；棕框：仅归属当前图纸面。蓝线：原生矢量中的轴线参考。
轴间构件及斜撑的上下左右轴名见 JSON axis_context，不将这些边界误写为所属面。</p>
{''.join(sections)}<h2>适用范围</h2><ul>{''.join('<li>'+escape(s)+'</li>' for s in report['limitations'])}</ul></html>'''
    (output / 'index.html').write_text(text, encoding='utf-8')

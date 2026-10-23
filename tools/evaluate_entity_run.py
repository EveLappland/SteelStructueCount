"""Offline evaluation of an exported workbook; never imported by AI workflows.

Uses the supplied judge's normalization and multiset matcher. UNKNOWN stays in
the precision denominator and is never counted as a match.
"""
import argparse
from collections import Counter
import hashlib
import html
import json
from io import BytesIO
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.judge import load_baseline, load_candidate, match_multiset


def evaluate(result, baseline, logs_root=None):
    result, baseline = Path(result).resolve(), Path(baseline).resolve()
    # The supplied judge leaves its ExcelFile open. Pass memory streams so a
    # scoring run never holds a Windows lock on the downloadable workbook.
    expected = load_baseline(BytesIO(baseline.read_bytes()))
    predicted = load_candidate(BytesIO((result/'stage_three.xlsx').read_bytes()), 'Quantity Length Summary')
    known = [(s, n) for s, n in predicted if math.isfinite(n)]
    unknown = [(s, None) for s, n in predicted if not math.isfinite(n)]
    matched, missing, extra = match_multiset(expected, known, 1)
    extra += unknown
    reference_types, predicted_types = {s for s, _ in expected}, {s for s, _ in predicted}
    metric = {'reference': str(baseline), 'reference_sha256': hashlib.sha256(baseline.read_bytes()).hexdigest(),
        'candidate': str(result/'stage_three.xlsx'), 'tolerance_mm': 1,
        'reference_count': len(expected), 'predicted_count': len(predicted), 'matched': len(matched),
        'false_positive': len(extra), 'false_negative': len(missing), 'unknown_components': len(unknown),
        'precision': len(matched)/len(predicted) if predicted else 0,
        'recall': len(matched)/len(expected) if expected else 0,
        'f1': 2*len(matched)/(len(expected)+len(predicted)) if expected or predicted else 0,
        'steel_types_reference': len(reference_types), 'steel_types_predicted': len(predicted_types),
        'steel_type_precision': len(reference_types & predicted_types)/len(predicted_types) if predicted_types else 0,
        'steel_type_recall': len(reference_types & predicted_types)/len(reference_types) if reference_types else 0,
        'missing': missing, 'extra': extra,
        'scope': 'spec+nominal length multiset, combinations expanded once, ±1mm; not per-position identity accuracy'}
    specs = []
    for spec in sorted(reference_types | predicted_types):
        b = sum(s == spec for s, _ in expected)
        c = sum(s == spec for s, _ in predicted)
        m = sum(s == spec for s, _, _ in matched)
        specs.append({'spec': spec, 'reference': b, 'predicted': c, 'matched': m,
            'precision': m/c if c else 0, 'recall': m/b if b else 0,
            'missing_lengths_mm': [n for s, n in missing if s == spec],
            'extra_lengths_mm': [n for s, n in extra if s == spec]})
    metric['by_spec'] = specs
    events = []
    for path in Path(logs_root or result).rglob('events.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            event = json.loads(line)
            events.append((event['at'], str(path), event))
    active, peak, requests, tokens, retries = set(), 0, 0, Counter(), 0
    for _, path, event in sorted(events, key=lambda x: x[0]):
        key = (path, event.get('response_file'))
        if event['event'] == 'MODEL_REQUEST_STARTED':
            active.add(key); requests += 1; peak = max(peak, len(active))
        if event['event'] in ('MODEL_RESPONSE_RECEIVED', 'MODEL_REQUEST_FAILED'):
            active.discard(key)
        if event['event'] == 'MODEL_RESPONSE_RECEIVED':
            tokens.update({k: event.get('usage', {}).get(k, 0) or 0 for k in ('prompt_tokens','completion_tokens','total_tokens')})
        if event['event'] in ('ENTITY_RETRY','LABEL_LENGTH_FORMAT_OR_TRANSPORT_RETRY'):
            retries += 1
    report = json.loads((result/'stage_three.json').read_text(encoding='utf-8'))
    metric['run_statistics'] = {'api_calls': requests, 'peak_http_requests': peak,
        'tokens': dict(tokens), 'retries': retries, 'third_stage': report['summary']}
    (result/'evaluation').mkdir(exist_ok=True)
    (result/'evaluation/metrics.json').write_text(json.dumps(metric, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    esc = lambda value: html.escape(str(value), quote=True)
    rows = ''.join('<tr>'+''.join(f'<td>{esc(v)}</td>' for v in [s['spec'], s['reference'], s['predicted'], s['matched'],
        f"{s['precision']:.2%}", f"{s['recall']:.2%}", s['missing_lengths_mm'], s['extra_lengths_mm']])+'</tr>' for s in specs)
    page = f'''<!doctype html><meta charset="utf-8"><title>新版第三阶段 · 离线验收指标</title>
<style>body{{font-family:system-ui;margin:30px;color:#193149;line-height:1.7}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccd7df;padding:8px;text-align:left}}pre{{white-space:pre-wrap}}</style>
<h1>新版第三阶段 · 离线验收指标</h1><p>仅在模型调用结束后读取参考表；未将参考答案发送给模型，未人工修改台账。</p>
<p>该参考是既有独立原图核对表，尚非用户最终确认的工程基准。按规格与名义长度的数量多重集计分，容差 ±1mm；UNKNOWN 留在分母中。此评分不等于逐位置配对准确率。</p>
<p>参考 {len(expected)} 根；导出 {len(predicted)} 根；匹配 {len(matched)} 根；多计/错长 {len(extra)}；漏计/错长 {len(missing)}；UNKNOWN {len(unknown)}。</p>
<p>Precision <b>{metric['precision']:.2%}</b> · Recall <b>{metric['recall']:.2%}</b> · F1 <b>{metric['f1']:.2%}</b></p>
<p>钢材种类 {len(predicted_types)}/{len(reference_types)}；种类 Precision {metric['steel_type_precision']:.2%}；Recall {metric['steel_type_recall']:.2%}。</p>
<p><a href="../stage_three.xlsx">下载本轮 Excel</a> · <a href="metrics.json">完整指标与差异 JSON</a></p>
<table><tr><th>规格</th><th>参考</th><th>输出</th><th>匹配</th><th>精确率</th><th>召回率</th><th>缺少长度 mm</th><th>多出或错长 mm</th></tr>{rows}</table>
<h2>实际调用和耗时</h2><pre>{esc(json.dumps(metric['run_statistics'],ensure_ascii=False,indent=2))}</pre>'''
    (result/'evaluation/index.html').write_text(page, encoding='utf-8')
    return metric


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--logs-root', type=Path)
    args = parser.parse_args()
    metric = evaluate(args.result, args.baseline, args.logs_root)
    print(json.dumps({k: v for k, v in metric.items() if k not in ('by_spec','missing','extra')}, ensure_ascii=False))

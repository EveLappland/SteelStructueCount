"""Read-only live comparison: exact user prompt/image versus the application envelope.

No task records or saved drawings are changed. Both arms use one frozen prompt
snapshot and the exact same PNG bytes. Results are diagnostic, not approved BOMs.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import sys
import threading
import time
import uuid

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audit import AuditLog
from app.llm_runner import _api_settings
from app.member_recognition import MemberResponse, make_input, allowed_model_tags
from app.native_pdf import write_json
from app.pipeline import load_native, model_call


def minimal_request(model, prompt, image):
    """No hidden options, system message, schema, context, or image conversions."""
    return {'model': model, 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url', 'image_url': {
            'url': 'data:image/png;base64,' + base64.b64encode(image).decode()}}]}]}


def summarize(content):
    """Summarize raw model fields without normalizing names or changing counts."""
    text = content.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    try:
        payload = json.loads(text, object_pairs_hook=unique_keys)
        members = payload['members']
        MemberResponse.model_validate(payload)
        groups = {}
        for row in members:
            key = (row['spec'], row['length_mm'])
            groups[key] = groups.get(key, 0) + row['member_cnt']
        return {'row_count': len(members), 'member_cnt': sum(r['member_cnt'] for r in members),
                'unknown_length_rows': sum(r['length_mm'] is None for r in members),
                'issue_rows': sum(bool(r['issue']) for r in members),
                'ledger': [{'spec': spec, 'length_mm': length, 'member_cnt': count}
                           for (spec, length), count in groups.items()],
                'note': '原始六字段汇总，未经轴线校验、人工核对或去重。'}
    except (ValueError, KeyError, TypeError) as exc:
        return {'parse_error': str(exc), 'note': '原始输出已保存，不修补或猜填字段。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--view-title', required=True)
    args = parser.parse_args()
    if len(args.job_id) != 32 or any(c not in '0123456789abcdef' for c in args.job_id):
        parser.error('job-id 必须是32位十六进制任务ID')
    with sqlite3.connect((ROOT / 'data/jobs_v2.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        record = db.execute('SELECT data,revision FROM stages WHERE job_id=? AND stage=1',
                            (args.job_id,)).fetchone()
    if not record:
        parser.error('找不到第一阶段')
    one = json.loads(record[0])
    views = [v for v in one['views'] if v['title'] == args.view_title and v['kind'] != 'ISOMETRIC']
    if len(views) != 1:
        parser.error('视图标题没有唯一匹配')
    view = views[0]
    folder = ROOT / 'data/jobs' / args.job_id
    # Reuse the saved image, never render or crop another copy for this comparison.
    image_path = folder / 'views' / Path(view.get('image_file') or f"{view['id']}_300dpi.png").name
    image = image_path.read_bytes()
    if not image.startswith(b'\x89PNG\r\n\x1a\n'):
        parser.error('必须是已有的高清PNG')
    prompt = (ROOT / 'app/prompts/stage_two_members.md').read_text(encoding='utf-8')
    output = ROOT / 'tmp/request_comparison' / uuid.uuid4().hex[:12]
    output.mkdir(parents=True)
    (output / 'prompt_snapshot.md').write_text(prompt, encoding='utf-8')
    (output / 'image.png').write_bytes(image)
    width, height = struct.unpack('>II', image[16:24])
    load_dotenv(ROOT / '.env')
    base, key, model = _api_settings({})
    audit = AuditLog(output, stage=2, secrets=(key,))
    client = OpenAI(base_url=base, api_key=key, timeout=600, max_retries=0)
    manifest = {'job_id': args.job_id, 'stage_one_revision': record[1], 'title': view['title'],
                'image_file': str(image_path), 'image_sha256': hashlib.sha256(image).hexdigest(),
                'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'pipeline_sha256': hashlib.sha256((ROOT/'app/pipeline.py').read_bytes()).hexdigest(),
                'width_px': width, 'height_px': height, 'model': model,
                'arms': {}, 'task_mutation': False,
                'limitations': '各一次调用，不能排除模型随机性；同时改变多项请求配置，不能归因到单项。'}
    write_json(output / 'report.json', manifest)
    print(f'结果目录：{output}', flush=True)
    audit.event('COMPARISON_STARTED', **manifest)
    stop = threading.Event()
    started = time.monotonic()
    def heartbeat():
        while not stop.wait(30):
            print(f'最小请求 API 处理中，已等待 {round(time.monotonic()-started)} 秒。', flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        response = client.chat.completions.create(**minimal_request(model, prompt, image))
        raw = json.loads(response.model_dump_json())
        write_json(output / 'minimal.raw.json', raw)
        content = raw['choices'][0]['message'].get('content') or ''
        audit.text(output / 'minimal.answer.txt', content)
        result = summarize(content)
        result.update(elapsed_seconds=round(time.monotonic()-started, 2),
                      finish_reason=raw['choices'][0].get('finish_reason'), usage=raw.get('usage'),
                      request_options={'model': model}, system_message=False,
                      json_schema=False, stage_one_context=False, image_count=1)
        manifest['arms']['minimal'] = result
        write_json(output / 'report.json', manifest)
        print('最小请求：' + json.dumps({k:v for k,v in result.items() if k not in ('ledger','usage')}, ensure_ascii=False), flush=True)
    except Exception as exc:
        manifest['arms']['minimal'] = {'error': audit.scrub(str(exc))}
        write_json(output / 'report.json', manifest)
        audit.event('MINIMAL_FAILED', error=str(exc))
        print('最小请求失败，详情已脱敏保存。', flush=True)
    finally:
        stop.set()
    page = next(p for p in load_native(folder) if p['id'] == view['page_id'])
    bundle = make_input(page, view, one)
    bundle['image'] = {'width_px': width, 'height_px': height, 'dpi': 300, 'format': 'PNG'}
    write_json(output / 'structured_input.json', bundle)
    structured_prompt = prompt + '\n输入包：\n' + json.dumps(bundle, ensure_ascii=False, separators=(',', ':'))
    entry = next(e for e in json.loads((folder / 'pdf_manifest.json').read_text(encoding='utf-8'))
                 if e['id'] == view['page_id'])
    started = time.monotonic()
    try:
        payload = model_call(client, model, structured_prompt,
            {**entry, 'id': view['id'], 'crop': view['bbox'], 'image_dpi': 300,
             'image_path': str(output / 'image.png'),
             'allowed_view_tags': allowed_model_tags(bundle)},
            output / 'responses/attempt-live/structured.json', MemberResponse,
            emit=lambda message: print(message, flush=True), audit=audit)
        result = summarize(json.dumps(payload, ensure_ascii=False))
        result.update(elapsed_seconds=round(time.monotonic()-started, 2),
                      system_message=True, json_schema=True, stage_one_context=True, image_count=1)
        manifest['arms']['structured_single_image'] = result
        print('应用封装请求：' + json.dumps({k:v for k,v in result.items() if k != 'ledger'}, ensure_ascii=False), flush=True)
    except Exception as exc:
        manifest['arms']['structured_single_image'] = {'error': audit.scrub(str(exc))}
        audit.event('STRUCTURED_FAILED', error=str(exc))
        print('应用封装请求失败，详情已脱敏保存。', flush=True)
    events = [json.loads(line) for line in audit.path.read_text(encoding='utf-8').splitlines()]
    manifest['arms']['structured_single_image']['attempts'] = [
        {'options': event['options'], 'request_signature': event['request_signature']}
        for event in events if event['event']=='MODEL_REQUEST_STARTED']
    write_json(output / 'report.json', manifest)
    audit.event('COMPARISON_COMPLETED', report='report.json')


if __name__ == '__main__':
    main()

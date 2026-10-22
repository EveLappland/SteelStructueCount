"""Real public API -> all stages -> Excel, without reference inputs or row edits."""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from app.ai_concurrency import view_concurrency
from app.drawing_facts import review_policy
from app.group_reconciliation import source_hash
from app.member_recognition import member_key


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8124')
    parser.add_argument('--pdf', type=Path, action='append', default=[])
    parser.add_argument('--resume-job')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    limit = view_concurrency({})
    policy = review_policy()
    if not args.resume_job and (not args.pdf or not all(p.is_file() for p in args.pdf)):
        parser.error('Supply existing PDFs or --resume-job')
    with httpx.Client(base_url=args.base, timeout=90, trust_env=False,
                      limits=httpx.Limits(max_keepalive_connections=0)) as client:
        def get(url, **kwargs):
            # Read-only monitoring must survive a local keep-alive disconnect.
            # Never repeat uploads, approvals, or paid inference here.
            for attempt in range(3):
                try:
                    response = client.get(url, **kwargs)
                    response.raise_for_status()
                    return response
                except httpx.TransportError:
                    if attempt == 2:
                        raise
                    time.sleep(1)
        if args.resume_job:
            identity = args.resume_job
        else:
            with ExitStack() as stack:
                files = [('files', (p.name, stack.enter_context(p.open('rb')), 'application/pdf')) for p in args.pdf]
                reply = client.post('/api/jobs', files=files, data={
                    'auto_run': 'true', 'stop_after_stage': '4',
                    'max_concurrency': str(limit), 'label_color_mode': 'original'})
                reply.raise_for_status()
                identity = reply.json()['id']
        manifest = args.output / 'input.json'
        prior = json.loads(manifest.read_text(encoding='utf-8')) if args.resume_job and manifest.exists() else {}
        write(manifest, {**prior, 'job_id': identity, 'url': args.base + '/?job=' + identity,
              'files': [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in args.pdf] or prior.get('files', []),
              'concurrency': limit, 'length_review_policy': policy,
              'manual_row_edits': False, 'reference_inputs': False})
        print('JOB_ID=' + identity, flush=True)
        previous = None
        began = time.monotonic()
        while True:
            reply = get('/api/jobs/' + identity)
            reply.raise_for_status()
            job = reply.json()
            state = (job['state'], job['active_stage'], job['events'][-1]['message'] if job['events'] else '')
            if state != previous:
                print(json.dumps(state, ensure_ascii=False), flush=True)
                previous = state
            write(args.output / 'job.json', job)
            if job['state'] in ('failed', 'interrupted'):
                raise RuntimeError('Job stopped: ' + str(job.get('error_detail')))
            if job['state'] == 'completed':
                break
            if time.monotonic() - began > 10800:
                raise TimeoutError('Three hour monitor limit; resume this job instead of creating another')
            time.sleep(5)
        stages, histories = {}, {}
        for n in (1, 2, 3):
            reply = get(f'/api/jobs/{identity}/stages/{n}')
            reply.raise_for_status()
            stage_reply = reply.json()
            stages[n] = stage_reply['data']
            histories[str(n)] = stage_reply.get('history', [])
            write(args.output / f'stage_{n}.json', stages[n])
        write(args.output / 'history.json', histories)
        events, cursor = [], 0
        while True:
            reply = get(f'/api/jobs/{identity}/logs', params={'after': cursor, 'limit': 1000})
            reply.raise_for_status()
            page = reply.json()
            events.extend(page['items'])
            cursor = page['next_cursor']
            if cursor >= page['total']:
                break
        for key, name in [('download_url', 'steel_takeoff.xlsx'), ('views_download_url', 'evidence.zip')]:
            reply = get(job[key])
            reply.raise_for_status()
            (args.output / name).write_bytes(reply.content)
    write(args.output / 'events.json', events)
    starts, durations, active, peak = {}, {}, set(), 0
    calls, roles, usage = Counter(), Counter(), Counter()
    for event in sorted(events, key=lambda e: e['at']):
        stage, kind = str(event['stage']), event['event']
        if kind == 'STAGE_STARTED':
            starts[stage] = event['at']
        if kind in ('STAGE_AWAITING_REVIEW', 'EXPORT_COMPLETED') and stage in starts:
            durations[stage] = (datetime.fromisoformat(event['at']) - datetime.fromisoformat(starts[stage])).total_seconds()
        if kind == 'MODEL_REQUEST_STARTED':
            calls[stage] += 1
            roles[event.get('task_kind') or 'stage_one'] += 1
            active.add(event['response_file'])
            peak = max(peak, len(active))
        if kind in ('MODEL_RESPONSE_RECEIVED', 'MODEL_REQUEST_FAILED'):
            active.discard(event['response_file'])
        if kind == 'MODEL_RESPONSE_RECEIVED':
            usage.update({k: v for k, v in (event.get('usage') or {}).items() if isinstance(v, (int, float))})
    two, three = stages[2], stages[3]
    rows = {r['id']: r for r in two['view_members']}
    used = Counter(a['row_id'] for g in three['resolutions'] for a in g['allocations'])
    checks = {'completed_without_manual_approval': job['state'] == 'completed',
              'all_views_processed': bool(two['view_analysis']) and all(a['status'] in
                  {'completed','completed_with_warnings','empty','filtered'} for a in two['view_analysis']),
              'request_concurrency_respected': peak <= limit,
              'length_call_caps_respected': all(a.get('length_review_calls', 0) <= policy['max_calls_per_view'] for a in two['view_analysis']),
              'source_planes_preserved': all(r['source_plane_tag'] in r['view_tags'] for r in rows.values()),
              'no_explicit_exclusions': not three['exclusions'],
              'all_observations_accounted_for': all(r['id'] in used or
                  any(e['observation_id'] == r['id'] for e in three['exceptions']) for r in rows.values()),
              'stage_two_not_rewritten': source_hash(two) == three['source_hash'],
              'exact_keys_preserved': all(member_key(g) == member_key(rows[a['row_id']])
                  for g in three['confirmed_groups'] for a in g['allocations']),
              'components_expanded_once': len(three['members']) == sum(g['member_cnt'] * g['component_multiplier'] for g in three['confirmed_groups']),
              'summary_matches_members': len(three['members']) == sum(s['quantity'] for s in three['summary']),
              'no_stage_three_model_calls': calls.get('3', 0) == 0,
              'no_manual_edits': not any(h['action'] in ('edit','before_edit') for hs in histories.values() for h in hs)}
    report = {'job_id': identity, 'url': args.base + '/?job=' + identity, 'status': 'completed_full',
              'stage_seconds': durations, 'requests_by_stage': dict(calls), 'requests_by_role': dict(roles),
              'usage': dict(usage), 'peak_http_requests': peak, 'length_review_policy': policy,
              'observations': len(rows), 'unknown_length_observations': sum(r['length_mm'] is None for r in rows.values()),
              'views': [{'view_id': a['view_id'], 'status': a['status'],
                  'observations': a['member_cnt'], 'length_calls': a.get('length_review_calls',0),
                  'arbitration': a.get('length_arbitration',{})} for a in two['view_analysis']],
              'final_totals': three['totals'], 'checks': checks,
              'manual_row_edits': False, 'reference_inputs': False}
    write(args.output / 'report.json', report)
    print(json.dumps({k:v for k,v in report.items() if k!='views'}, ensure_ascii=False, indent=2), flush=True)
    if not all(checks.values()):
        raise RuntimeError('Workflow checks failed; see report.json')


if __name__ == '__main__':
    main()

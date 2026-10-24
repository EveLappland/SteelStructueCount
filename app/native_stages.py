"""Read fixed-label deliveries into the original web stage layout.

This is a presentation adapter: it never changes native labels, measurements,
images, model replies or source hashes. Stage actions live in NativeRuns.
"""
import json
from pathlib import Path

from fastapi import HTTPException


def records(job, number):
    return [entry for entry in job.get('stages', []) if entry['stage'] == number]


def metadata(job, number):
    saved = job.get('stage_states', {}).get(str(number))
    if saved:
        return {'stage': number, **saved}
    complete = records(job, number)
    expected = len(job['inputs']) if job['mode'] == 'PDF' and number < 3 else 1
    if job['mode'] == 'MATCH' and number < 3:
        status = 'approved'
    elif len(complete) >= expected:
        status = 'approved' if job['state'] == 'completed' or job['stage'] > number else 'awaiting_review'
    elif job['stage'] == number or complete:
        status = job['state'] if job['state'] in ('failed','interrupted') else 'running'
    else:
        status = 'queued'
    return {'stage': number, 'revision': 1, 'status': status}


def file_url(identity, directory, filename):
    return f'/api/native/runs/{identity}/files/{directory}/{filename}'


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def sources(service, job):
    """Stage-one facts and stage-two locations, including running old jobs."""
    folder = service.folder/job['id']
    if job['mode'] == 'MATCH':
        deliveries={entry.get('input_index',i):entry for i,entry in enumerate(records(job,2))}
        for index, path in enumerate(job['inputs']):
            two = folder/deliveries[index]['directory'] if index in deliveries else Path(path)
            if two.is_file():
                two = two.parent
            yield {'index': index, 'one': two, 'two': two,
                'facts': two/'stage_one_facts.snapshot.json', 'external':not two.resolve().is_relative_to(folder.resolve()),
                'snapshot':True}
        return
    ones = {entry.get('input_index', index): entry for index, entry in enumerate(records(job, 1))}
    twos = {entry.get('input_index', index): entry for index, entry in enumerate(records(job, 2))}
    for index, _ in enumerate(job['inputs']):
        one = folder/ones[index]['directory'] if index in ones else None
        working=job.get('current_output') or {}
        two = (folder/twos[index]['directory'] if index in twos else
               folder/working['directory'] if working.get('stage')==2 and working.get('input_index')==index else
               folder/f'pdf{index+1:03d}/stage_two' if not job.get('stage_states') or job.get('resume_saved') else
               folder/'pending-stage-two'/str(index))
        if one is not None:
            yield {'index': index, 'one': one, 'two': two,
                'facts': one/'stage_one_facts.json', 'external': False}


def payload(service, identity, number):
    if number not in (1, 2, 3):
        raise HTTPException(404, '阶段不存在。')
    job = service.get(identity)
    state = metadata(job, number)
    data = {'workflow': 'native_labels', 'views': [], 'view_members': [],
            'profile_catalog': [], 'warnings': [], 'reports': []}
    folder = service.folder/identity
    if number == 3:
        entries = records(job, 3)
        if entries:
            directory = entries[-1]['directory']
            result = read(folder/directory/'stage_three.json', {})
            data.update(result)
            data['reports'] = [{'url': file_url(identity,directory,'index.html'), 'title':'完整去重报告'}]
        return {**state, 'job_id': identity, 'data': data, 'snapshots': job.get('history', [])}
    for source in sources(service, job):
        bundle = read(source['facts'])
        if bundle is None:
            continue
        namespace = bundle['source_sha256']
        two = read(source['two']/'stage_two.json', {})
        two_views = {v['view_id']: v for v in two.get('views', [])}
        for view in bundle['views']:
            uid = namespace+':'+view['view_id']
            if source.get('snapshot'):
                image = two_views[view['view_id']]['image_file']
                url = (f"/api/native/runs/{identity}/sources/{source['index']}/{image}" if source['external'] else
                       file_url(identity,source['two'].relative_to(folder).as_posix(),image))
            else:
                directory = source['one'].relative_to(folder).as_posix()
                url = file_url(identity, directory, view['image_file'])
            data['views'].append({**view, 'id': uid, 'image_url':url,
                'steel_types':sorted({row['spec'] for row in view['labels']}),
                'axis_labels':list(dict.fromkeys(a['name'] for a in view.get('axes', []))),
                'state':two_views.get(view['view_id'], {}).get('state', 'not_started'),
                'requests':two_views.get(view['view_id'], {}).get('requests', 0)})
        for member in two.get('members', []):
            data['view_members'].append({**{k:v for k,v in member.items() if k!='candidates'},
                                         'source_view':namespace+':'+member['view_id']})
        if not source['external']:
            directory = (source['one'] if number == 1 else source['two']).relative_to(folder).as_posix()
            if (folder/directory/'index.html').is_file():
                data['reports'].append({'url':file_url(identity,directory,'index.html'),
                                       'title':Path(job['inputs'][source['index']]).name})
    data['profile_catalog'] = sorted({label['spec'] for view in data['views'] for label in view['labels']})
    data['axis_names'] = sorted({a for view in data['views'] for a in view['axis_labels']} |
                                {view['plane_name'] for view in data['views']})
    data['summary'] = {'view_count':len(data['views']),
        'label_count':sum(len(v['labels']) for v in data['views']),
        'steel_type_count':len(data['profile_catalog']), 'axis_count':len(data['axis_names']),
        'member_count':sum(r['member_cnt'] for r in data['view_members']),
        'known_length_count':sum(r['member_cnt'] for r in data['view_members'] if r['length_mm'] is not None)}
    return {**state, 'job_id':identity, 'data':data, 'snapshots':job.get('history', [])}


def status(service, identity):
    job = service.get(identity)
    value = {**job, 'backend':'native', 'active_stage':job.get('next_stage', job['stage']) or 1,
             'stages':[metadata(job, number) for number in (1,2,3)],
             'error_detail':job.get('error'), 'events':[]}
    from .audit import AuditLog
    value['events'] = [{'at':e['at'], 'message':e.get('message') or e.get('error') or e['event']}
        for e in AuditLog(service.folder/identity).read(0,10000)['items']]
    entries = records(job, 3)
    value['download_url'] = None
    value['views_download_url'] = None
    if job['state'] == 'completed' and entries:
        directory = entries[-1]['directory']
        value['download_url'] = file_url(identity,directory,'stage_three.xlsx')
        value['views_download_url'] = f'/api/native/runs/{identity}/evidence-download'
        if (service.folder/identity/directory/'evaluation/index.html').is_file():
            value['evaluation_url'] = file_url(identity,directory,'evaluation/index.html')
    return value

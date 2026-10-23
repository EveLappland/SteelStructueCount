"""Verified stage-two observations; source PDF hashes namespace local label IDs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from PIL import Image
from .label_facts import FORMAT, digest
from .label_length_contracts import evaluate


def read_inputs(paths):
    observations, views, sources = {}, {}, []
    for supplied in paths:
        path = Path(supplied).resolve()
        if path.is_dir():
            path /= 'stage_two.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        bundle = json.loads((path.parent/'stage_one_facts.snapshot.json').read_text(encoding='utf-8'))
        material = deepcopy(bundle); signature = material.pop('bundle_sha256', None)
        if bundle.get('format') != FORMAT or digest(material) != signature or data.get('format') != 'label-lengths/v1':
            raise ValueError('需要有效的一阶段事实快照和固定标签二阶段结果')
        if data['stage_one_sha256'] != signature:
            raise ValueError('第二阶段与第一阶段来源指纹不符')
        namespace = bundle['source_sha256']
        native = {v['view_id']:v for v in bundle['views']}
        selected, labels = set(), {}
        for entry in data['views']:
            local_id = entry['view_id']
            if local_id in selected or local_id not in native or entry['state'] not in ('completed','empty'):
                raise ValueError('只能接入已完成且视图ID不重复的第二阶段')
            selected.add(local_id)
            view = deepcopy(native[local_id]); uid = namespace+':'+local_id
            if uid in views:
                raise ValueError('同一PDF视图重复输入；不能作为独立视图再次计数')
            image = (path.parent/entry['image_file']).resolve()
            if not image.is_relative_to(path.parent) or hashlib.sha256(image.read_bytes()).hexdigest() != view['image_sha256']:
                raise ValueError('第二阶段原图路径或指纹无效')
            with Image.open(image) as opened:
                if list(opened.size) != view['image_size_px']:
                    raise ValueError('第二阶段原图尺寸无效')
            view.update(uid=uid, namespace=namespace, image_path=str(image))
            views[uid] = view
            for label in view['labels']:
                if label['label_id'] in labels or label['member_cnt'] != 1 or view['plane_name'] not in label['view_tags']:
                    raise ValueError('第一阶段标签身份/数量/所属面无效')
                labels[label['label_id']] = (uid,label)
        seen = set()
        for row in data['members']:
            identity = row['label_id']
            if identity not in labels or identity in seen:
                raise ValueError('第二阶段缺少、重复或新增标签')
            seen.add(identity); view_uid,label = labels[identity]; view = views[view_uid]
            if row['view_id'] != view['view_id']:
                raise ValueError('标签来源视图发生变化')
            for field in ('spec','member_cnt','component_multiplier','view_tags','bbox_view'):
                if row[field] != label[field]:
                    raise ValueError(f'第二阶段改变固定标签字段：{field}')
            candidate = None
            for raw in row['candidates'].values():
                evaluated = evaluate(raw['answer'],view['text_facts'])
                if evaluated['answer']['label_id'] != identity:
                    raise ValueError('长度候选引用其他标签')
                if (evaluated['length_mm'] == row['length_mm'] and evaluated['formulas'] == row['formulas']
                        and evaluated['answer']['kind'] == row['kind']
                        and all(evaluated['answer'][k] == row[k] for k in ('endpoints','basis','issue'))
                        and evaluated['dimension_values_mm'] == row['dimension_values_mm']):
                    candidate = evaluated
            if row['length_mm'] is not None and (candidate is None or not candidate['usable'] or row['length_mm']<=0):
                raise ValueError('第二阶段长度没有可重新计算的原始证据')
            obs_id = namespace+':'+identity
            observations[obs_id] = {**deepcopy(row),'observation_id':obs_id,'view_uid':view_uid,
                'source_plane':view['plane_name'],'measurement':candidate,
                'axis_context':deepcopy(label.get('axis_context',{})), 'direction':label['direction']}
        if seen != set(labels):
            raise ValueError('第二阶段未保留全部第一阶段标签')
        sources.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                        'stage_one_sha256':signature,'source_sha256':namespace})
    if not sources:
        raise ValueError('至少输入一份第二阶段结果')
    return observations,views,sorted(sources,key=lambda s:s['path'])

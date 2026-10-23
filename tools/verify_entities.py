"""Verify third-stage provenance, conservation and local arithmetic, not accuracy."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.entity_inputs import read_inputs
from app.entity_reduction import conservation
from app.entity_report import component_rows
from app.label_length_contracts import evaluate
from app.native_pdf import write_json


def verify(path):
    path=Path(path).resolve()
    if path.is_dir():path/='stage_three.json'
    report=json.loads(path.read_text(encoding='utf-8'))
    if report['format']!='cross-view-entities/v1':raise ValueError('未知的第三阶段格式')
    observations,views,sources=read_inputs([s['path'] for s in report['sources']])
    if sources!=report['sources']:raise ValueError('第三阶段来源文件发生变化')
    entities=report['entities']; ids=[e['id'] for e in entities]
    if len(ids)!=len(set(ids)):raise ValueError('实体ID重复')
    check=conservation(entities,observations)
    if check!=report['conservation']:raise ValueError('保存的守恒结果不一致')
    expected_maps=[]
    for entity in entities:
        source=entity['length_source_id']; candidate=entity['measurement']
        if candidate is not None:
            if source not in entity['observation_ids']:raise ValueError('长度来源不属于实体')
            row=observations[source]
            if candidate['answer']['label_id']!=row['label_id']:raise ValueError('测量标签ID不一致')
            recalculated=evaluate(candidate['answer'],views[row['view_uid']]['text_facts'])
            if recalculated!=candidate or recalculated['length_mm']!=entity['length_mm']:
                raise ValueError('最终长度无法按原始文字重新计算')
        elif entity['length_mm'] is not None:
            raise ValueError('已知长度没有尺寸证据')
        for identity in entity['observation_ids']:
            row=observations[identity]
            expected_maps.append({'observation_id':identity,'label_id':row['label_id'],'view_uid':row['view_uid'],
                'entity_id':entity['id'],'original_length_mm':row['length_mm'],'entity_length_mm':entity['length_mm'],
                'changed':row['length_mm']!=entity['length_mm']})
    if expected_maps!=report['observation_entity_map']:raise ValueError('观察对应或原长度发生变化')
    for view in report['views']:
        image=(path.parent/view['image_file']).resolve()
        if not image.is_relative_to(path.parent) or hashlib.sha256(image.read_bytes()).hexdigest()!=views[view['uid']]['image_sha256']:
            raise ValueError('第三阶段图片路径或内容发生变化')
    components=component_rows(entities)
    if components!=json.loads((path.parent/'components.json').read_text(encoding='utf-8')):
        raise ValueError('组件展开结果与导出不一致')
    if sum(e['member_cnt'] for e in entities)!=report['summary']['entity_quantity']:
        raise ValueError('实体数量汇总不一致')
    return {'every_observation_assigned_once':True,'per_view_counts_preserved':True,
        'stage_two_sources_unchanged':True,'images_unchanged':True,'entity_measurement_arithmetic_verified':True,
        'component_expansion_verified':True,'input_observations':len(observations),
        'entity_quantity':sum(e['member_cnt'] for e in entities),'single_component_quantity':len(components),
        'limits':'Data provenance, conservation and arithmetic only; not a spatial/engineering accuracy evaluation.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result',required=True,type=Path)
    args=parser.parse_args();result=verify(args.result)
    directory=args.result if args.result.is_dir() else args.result.parent
    write_json(directory/'verification.json',result)
    print(json.dumps(result,ensure_ascii=False))

"""Verify immutable identities, local arithmetic and artifacts; no accuracy oracle."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.label_facts import load


def verify(facts_path, result_path):
    bundle = load(facts_path)
    result = json.loads(Path(result_path).read_text(encoding='utf-8'))
    assert result['stage_one_sha256'] == bundle['bundle_sha256'], 'Source fingerprint changed'
    selected = {v['view_id'] for v in result['views']}
    labels = {l['label_id']:l for v in bundle['views'] if v['view_id'] in selected for l in v['labels']}
    rows = result['members']
    ids = Counter(r['label_id'] for r in rows)
    assert set(ids) == set(labels) and all(v == 1 for v in ids.values()), 'Missing/extra/duplicate labels'
    for row in rows:
        source = labels[row['label_id']]
        for field in ('spec','member_cnt','component_multiplier','view_tags','bbox_view'):
            assert row[field] == source[field], f'Changed {field}: {row["label_id"]}'
        if row['length_mm'] is not None:
            assert row['length_mm'] > 0
            assert any(c['usable'] and c['length_mm'] == row['length_mm'] and
                       c['formulas'] == row['formulas'] and not c['checks']
                       for c in row['candidates'].values()), f'No locally valid source candidate: {row["label_id"]}'
    source_views = {v['view_id']:v for v in bundle['views']}
    for view in result['views']:
        image = Path(result_path).parent/view['image_file']
        assert hashlib.sha256(image.read_bytes()).hexdigest() == source_views[view['view_id']]['image_sha256']
    return {'identity_count':len(rows),'known_length_count':sum(r['length_mm'] is not None for r in rows),
            'unknown_length_count':sum(r['length_mm'] is None for r in rows),
            'identities_unchanged':True,'images_unchanged':True,'selected_arithmetic_verified':True,
            'pipeline_complete':all(v['state'] in ('completed','empty') for v in result['views']),
            'limits':'This verifies data integrity and calculation, not graphical endpoint/length accuracy.'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--facts',type=Path,required=True)
    parser.add_argument('--result',type=Path,required=True)
    args=parser.parse_args()
    report=verify(args.facts,args.result)
    (args.result.parent/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

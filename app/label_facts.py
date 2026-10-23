"""Versioned native-label facts delivered to label-constrained length agents."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from PIL import Image
from .drawing_facts import build_facts
from .native_pdf import write_json

FORMAT = 'native-label-facts/v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def in_view(box, region):
    return [(v-region[i % 2])/(region[i % 2+2]-region[i % 2]) for i, v in enumerate(box)]


def deliver(directory, report, pages):
    directory = Path(directory)
    sources = {p['id']: p for p in pages}
    views = []
    for view in report['views']:
        image = directory/f"{view['id']}_300dpi.png"
        with Image.open(image) as opened:
            size = list(opened.size)
        region = view['bbox']
        axes = []
        for axis in view['axes']:
            line = axis['line']
            if line is None:
                continue
            points = [[(p[i]-region[i])/(region[i+2]-region[i]) for i in (0, 1)] for p in line['points']]
            normal = 1 if line['orientation'] == 'HORIZONTAL' else 0
            axes.append({'name': axis['name'], 'text_id': axis['source_text_id'],
                'orientation': line['orientation'], 'coordinate_view': points[0][normal],
                'span_view': sorted(p[1-normal] for p in points), 'points_view': points,
                'vector_ids': line.get('support_vector_ids', [line['vector_id']])})
        labels = [{'label_id': row['id'], 'text_id': row['source_text_id'],
                   'spec': row['spec'], 'member_cnt': row['member_cnt'],
                   'component_multiplier': row['component_multiplier'],
                   'bbox_view': in_view(row['bbox'], region), 'direction': row['direction'],
                   'view_tags': row['view_tags'], 'tag_status': row['tag_status'],
                   'axis_context': row['axis_context'], 'axis_evidence': row['axis_match']}
                  for row in view['labels']]
        order = {kind: [a['name'] for a in sorted(axes, key=lambda a: a['coordinate_view'])
                        if a['orientation'] == kind] for kind in ('HORIZONTAL', 'VERTICAL')}
        views.append({'view_id': view['id'], 'page_id': view['page_id'], 'title': view['title'],
                      'kind': view['kind'], 'plane_name': view['plane_name'], 'bbox_page': region,
                      'image_file': image.name, 'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                      'image_size_px': size, 'axes': axes, 'axis_order': order, 'labels': labels,
                      'text_facts': build_facts(sources[view['page_id']], view)['facts']})
    bundle = {'format': FORMAT, 'source_sha256': report['pdf_sha256'], 'views': views,
              'count_unit': 'one native label = one member/assembly; no component expansion or cross-view deduplication',
              'coordinates': 'view-normalized XY, origin top-left; positions are not millimetres',
              'limits': 'Axis/label association is a program proximity proposal. Dimension endpoints and segmentation are not yet established.'}
    bundle['bundle_sha256'] = digest(bundle)
    write_json(directory/'stage_one_facts.json', bundle)
    return bundle


def load(path):
    """Validate identities, JSON and image hashes before making any API request."""
    path = Path(path).resolve()
    bundle = json.loads(path.read_text(encoding='utf-8'))
    material = deepcopy(bundle)
    signature = material.pop('bundle_sha256', None)
    if material.get('format') != FORMAT or digest(material) != signature:
        raise ValueError('第一阶段事实文件格式或指纹无效')
    seen_views, seen_labels = set(), set()
    for view in bundle['views']:
        identity = view['view_id']
        if identity in seen_views:
            raise ValueError('第一阶段视图ID重复')
        seen_views.add(identity)
        image = Path(view['image_file'])
        if image.name != view['image_file'] or image.is_absolute():
            raise ValueError('交付图片必须是事实文件目录内的文件名')
        image = path.parent/image
        if hashlib.sha256(image.read_bytes()).hexdigest() != view['image_sha256']:
            raise ValueError('第一阶段图片与交付指纹不一致')
        with Image.open(image) as opened:
            if list(opened.size) != view['image_size_px']:
                raise ValueError('交付图片尺寸不一致')
        for label in view['labels']:
            if label['label_id'] in seen_labels or label['member_cnt'] != 1:
                raise ValueError('标签ID必须唯一，每条标签计一根/一个组合')
            seen_labels.add(label['label_id'])
            if view['plane_name'] not in label['view_tags']:
                raise ValueError('标签缺少所属图纸面')
    return bundle

"""Image/label coordinate mapping. No dimension inference or model calls."""
from pathlib import Path
import hashlib
from PIL import Image
from .grid_layout import freeze_grid


def prepare(directory, source, view):
    directory = Path(directory)
    (directory/'views').mkdir(parents=True, exist_ok=True)
    plan = freeze_grid(directory, view['view_id'], source)
    owners = {}
    for label in view['labels']:
        b = label['bbox_view']
        x, y = (b[0]+b[2])/2, (b[1]+b[3])/2
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError('第一阶段标签中心不在所属视图内')
        px, py = min(x*plan['width_px'], plan['width_px']-1e-6), min(y*plan['height_px'], plan['height_px']-1e-6)
        tile = next(t for t in plan['tiles'] if t['core_bbox_px'][0] <= px < t['core_bbox_px'][2]
                    and t['core_bbox_px'][1] <= py < t['core_bbox_px'][3])
        owners[label['label_id']] = tile['id']
    return plan, owners


def label_input(label, tile=None, plan=None):
    result = {k: label[k] for k in ('label_id', 'text_id', 'spec', 'bbox_view', 'direction',
                                   'view_tags', 'tag_status', 'axis_context')}
    if tile:
        crop = tile['crop_bbox_px']
        result['bbox_tile'] = [(value*(plan['width_px'] if i%2 == 0 else plan['height_px'])-crop[i%2])/
                              (crop[i%2+2]-crop[i%2]) for i,value in enumerate(label['bbox_view'])]
    return result


def frame_input(view, tile=None, plan=None):
    """Send useful positions/names, not long audit-only vector-ID lists.

    Grid children receive native text in their actual crop. Full-view roles
    retain the complete text catalog; off-crop dimensions are not local facts.
    The immutable delivery bundle itself is never edited or truncated.
    """
    facts = view['text_facts']
    if tile:
        crop = tile['crop_bbox_px']; w,h = plan['width_px'],plan['height_px']
        facts = [f for f in facts if crop[0] <= (f['bbox_view'][0]+f['bbox_view'][2])/2*w <= crop[2]
                 and crop[1] <= (f['bbox_view'][1]+f['bbox_view'][3])/2*h <= crop[3]]
    return {**{k:view[k] for k in ('view_id','title','kind','plane_name','axis_order')},
            'axes':[{k:(round(a[k],6) if k == 'coordinate_view' else a[k])
                     for k in ('name','text_id','orientation','coordinate_view','span_view')} for a in view['axes']],
            'text_facts':[{k:f[k] for k in ('id','text','bbox_view','values_mm')} for f in facts]}


def details(directory, image, view, targets, limit=4):
    """Full-axis strips enlarge context; full original image is always supplied.

    These rectangles do not claim endpoints. Merge touching/overlapping strips,
    preventing a member from being interpreted as separate cropped pieces.
    """
    boxes = []
    for label in view['labels']:
        if label['label_id'] not in targets:
            continue
        b = label['bbox_view']; x,y = (b[0]+b[2])/2,(b[1]+b[3])/2
        dx,dy = label['direction']
        if abs(dx) >= .98:
            box = [0, max(0,y-.12), 1, min(1,y+.12)]
        elif abs(dy) >= .98:
            box = [max(0,x-.08), 0, min(1,x+.08), 1]
        else:
            box = [max(0,x-.25), max(0,y-.25), min(1,x+.25), min(1,y+.25)]
        boxes.append(box)
    merged = []
    for box in boxes:
        index = 0
        while index < len(merged):
            other = merged[index]
            if min(box[2],other[2]) >= max(box[0],other[0]) and min(box[3],other[3]) >= max(box[1],other[1]):
                box = [min(box[0],other[0]),min(box[1],other[1]),max(box[2],other[2]),max(box[3],other[3])]
                merged.pop(index); index = 0
            else:
                index += 1
        merged.append(box)
    if len(merged) > limit:
        merged = [[min(b[0] for b in merged),min(b[1] for b in merged),
                   max(b[2] for b in merged),max(b[3] for b in merged)]]
    result = []
    with Image.open(image) as opened:
        w,h = opened.size
        for index,box in enumerate(merged):
            if box == [0,0,1,1]:
                continue
            key = hashlib.sha256(str(box).encode()).hexdigest()[:12]
            target = Path(directory)/'views'/f"context-{view['view_id']}-{key}.png"
            opened.crop((int(box[0]*w),int(box[1]*h),min(w,round(box[2]*w)),min(h,round(box[3]*h)))).save(target,dpi=(300,300))
            result.append({'path': str(target), 'bbox_view': box})
    return result

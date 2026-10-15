"""AI view proposals checked against native evidence; no reference crop coordinates."""
import json
import math
import re
from copy import deepcopy
from .discovery_inventory import labels


def localization_prompt(page,frame):
    texts=[{'id':t['id'],'text':t['text'],'bbox':t['bbox']} for t in page['texts']]
    return (
        'Locate ALL independently titled structural views in this PDF page image. '
        'Return one view per independent native title, not reference-table/title-block mentions. '
        'For EACH view return structure_bbox (physical structure envelope ONLY), bbox (complete crop), '
        'title_bbox, exact title, kind, visible_axes, fixed_axis, fixed_label/elevation reference, '
        'evidence_ids including the exact native title ID, boundary_evidence_ids and boundary_reason. '
        'All boxes are ORIGINAL PAGE normalized [left,top,right,bottom], not pixel/crop coordinates. '
        'Locate the structure using grid extents and member outlines. Then make the full crop include '
        'outermost grid bubbles, both ends of dimension chains, leader endpoints, splice markers, '
        'the printed title and locally applying notes below it (e.g. braces below TOS). '
        'Do not crop only the steel envelope. Separate neighbouring views using whitespace and titles. '
        'Exclude sheet border grids, north arrow, references, notes tables, title blocks and node/plate details. '
        'If a complete boundary cannot be determined, explicitly say so in boundary_reason. '
        'Preserve printed axis names; determine global directions consistently with the project frame. '
        'For each view return axis_marks for the grid names visible inside THAT view, with original TEXT evidence_ids. '
        'Reference names represent Alignment elevation planes or EL level plan planes, not only vertical grid axes. '
        'Register visible EL level names including their printed TOS/BOB datum as well as Alignment names; '
        'use Z for level marks and bind each name only to its own view. '
        'fixed_label is the alignment of this elevation, not the list of all axes visible in it. '
        'Classify EACH located view using takeoff_role: MEMBERS for layouts of complete beams/columns/braces; '
        'REFERENCE for isometric overviews, notes, and foundation plans showing only column cross-section projections; '
        'EXCLUDE for plate/bolt/connection details and drawing tables; REVIEW when the use is ambiguous. '
        'Explain the visible content and counting consequence in filter_reason. '
        'A BOB datum, lack of profile labels, or a plate detail elsewhere on the same page does NOT by itself justify filtering a structural layout. '
        'Keep every independent structural view in the catalog even if REFERENCE or REVIEW; never filter an entire PDF by its name. '
        'Do not return filter_reviewed; the application owns manual review state. '
        'Never reuse a repeated grid label from a neighbouring view as this view evidence. '
        'PLAN uses X/Y with fixed Z elevation; ISOMETRIC provides coverage only. '
        f"Native independent titles: {json.dumps(page['titles'],ensure_ascii=False)}\n"
        f"Project frame: {json.dumps(frame,ensure_ascii=False)}\n"
        f"Native positioned text: {json.dumps(texts,ensure_ascii=False,separators=(',',':'))}")


def contains(outer,inner,tolerance=1e-6):
    return outer[0]-tolerance<=inner[0] and outer[1]-tolerance<=inner[1] and outer[2]+tolerance>=inner[2] and outer[3]+tolerance>=inner[3]


def intersects(a,b):return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])


def center(box):return [(box[0]+box[2])/2,(box[1]+box[3])/2]


def distance(point,box,page):
    dx=max(box[0]-point[0],0,point[0]-box[2])*page['width_pt']
    dy=max(box[1]-point[1],0,point[1]-box[3])*page['height_pt']
    return math.hypot(dx,dy)


def expand(box,other):return [min(box[0],other[0]),min(box[1],other[1]),max(box[2],other[2]),max(box[3],other[3])]


def evidence_kind(text):
    if list(labels(text)):return 'profile'
    if re.fullmatch(r'[\w-]+/[A-Za-z0-9]+',text.strip()):return 'axis'
    if re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?\s*(?:mm|m)?',text.strip(),re.I):return 'dimension_candidate'
    if re.search(r'OF SPLICE|BELOW TOS|ALL HORIZONTAL|\bEL\.|\bBOB\b|\bTOS\b',text,re.I):return 'annotation'
    return None


def refine_view_regions(page,views,audit=None):
    result=deepcopy(views)
    native={t['id']:t for t in page['texts']}
    anchors={}
    cores={}
    for v in result:
        title=next((t for t in page['titles'] if t['title']==v['title'] and t['kind']==v['kind']),None)
        if title:anchors[v['id']]=title
        # Legacy/manual proposals may omit a structure box; title alone must not
        # authorize unbounded extension into other views.
        cores[v['id']]=v.get('structure_bbox') or v['bbox']
    for view in result:
        identity=view['id'];before=list(view['bbox']);box=list(before)
        issues=[];included=[];ambiguous=[];vector_ids=[];rejected=[]
        title=anchors.get(identity);core=cores[identity]
        if title:
            view['title_bbox']=title['title_bbox']
            view['evidence_ids']=list(dict.fromkeys(view['evidence_ids']+[title['evidence_id']]))
            box=expand(box,title['title_bbox']);included.append(title['evidence_id'])
        else:issues.append('没有对应的原生独立视图标题。')
        if not contains(box,core):box=expand(box,core)
        xmargin=min(.04,max(.02,(core[2]-core[0])*.15))
        ybottom=max(core[3],title['title_bbox'][3] if title else core[3])+.035
        ytop=min(core[1],title['title_bbox'][1] if title else core[1])-.055
        search=[max(0,before[0]-xmargin),max(0,ytop),min(1,before[2]+xmargin),min(1,ybottom)]
        for item in page['texts']:
            kind=evidence_kind(item['text'])
            if not kind:continue
            p=center(item['bbox'])
            if not search[0]<=p[0]<=search[2] or not search[1]<=p[1]<=search[3]:continue
            ownership=sorted((distance(p,c,page),key) for key,c in cores.items())
            if len(ownership)>1 and abs(ownership[1][0]-ownership[0][0])<3:
                ambiguous.append(item['id']);continue
            if ownership[0][1]!=identity:continue
            box=expand(box,item['bbox']);included.append(item['id'])
        # Preserve short primitives touching accepted annotations/endpoints, but
        # never follow long grid/dimension lines across the sheet.
        support_boxes=[native[e]['bbox'] for e in included if e in native]
        for vector in page['vectors']:
            a,b=vector['points'];length=math.hypot((a[0]-b[0])*page['width_pt'],(a[1]-b[1])*page['height_pt'])
            if length>180 or not length:continue
            p=center(vector['bbox'])
            if not search[0]<=p[0]<=search[2] or not search[1]<=p[1]<=search[3]:continue
            if support_boxes and min(distance(p,s,page) for s in support_boxes)<=12:
                if min((distance(p,c,page),key) for key,c in cores.items())[1]!=identity:continue
                box=expand(box,vector['bbox']);vector_ids.append(vector['id'])
        box=[max(0,box[0]-.008),max(0,box[1]-.008),min(1,box[2]+.008),min(1,box[3]+.008)]
        for other in page['titles']:
            if title and other['evidence_id']==title['evidence_id']:continue
            if intersects(box,other['title_bbox']):issues.append('裁切混入另一个独立视图标题：'+other['title'])
        for item in page['texts']:
            if re.search(r'^REFERENCE DOCUMENTS?$|^SHEET SIZE$|^DRAWING TITLE$',item['text'],re.I) and intersects(box,item['bbox']):
                issues.append('裁切混入图签或引用表：'+item['text'])
        claimed=view.get('boundary_evidence_ids',[])
        missing=[e for e in claimed if e not in native and not any(v['id']==e for v in page['vectors'])]
        if missing:issues.append('模型边界依据引用不存在的证据。')
        for evidence_id in claimed:
            if evidence_id in native and not contains(box,native[evidence_id]['bbox']):
                item=native[evidence_id];p=center(item['bbox'])
                foreign_title=next((t for t in page['titles'] if t['evidence_id']==evidence_id
                                    and (not title or t['evidence_id']!=title['evidence_id'])),None)
                if foreign_title:
                    rejected.append({'id':evidence_id,'text':item['text'],
                        'owner_view':next((key for key,anchor in anchors.items() if anchor['evidence_id']==evidence_id),None),
                        'reason':'该证据是另一独立视图的原生标题，拒绝跨视图边界错引，不扩大裁切。'})
                    continue
                ownership=sorted((distance(p,c,page),key) for key,c in cores.items())
                owner=ownership[0][1]
                # A repeated axis name on the neighbouring view is a bad model
                # citation, not evidence that this crop must include that view.
                if owner!=identity and owner in anchors and contains(cores[owner],item['bbox']) and (
                        len(ownership)==1 or ownership[1][0]-ownership[0][0]>3):
                    rejected.append({'id':evidence_id,'text':item['text'],'owner_view':owner,
                                     'reason':'证据完整位于另一独立视图结构区域，拒绝模型跨视图错引，不扩大裁切。'})
                else:issues.append('模型引用的边界文字不在裁切内：'+evidence_id)
        rejected_ids={e['id'] for e in rejected}
        view['boundary_evidence_ids']=[e for e in claimed if e not in rejected_ids]
        view['evidence_ids']=[e for e in view['evidence_ids'] if e not in rejected_ids]
        view['bbox']=box
        view['localization']={'status':'needs_review' if issues or ambiguous else 'checked',
            'model_bbox':before,'structure_bbox':core,'final_bbox':view['bbox'],'included_text_ids':included,
            'included_vector_ids':vector_ids,'ambiguous_text_ids':ambiguous,'issues':issues,
            'rejected_boundary_evidence':rejected,
            'reason':'标题为锚点，按到结构区域的距离分配附近轴线、尺寸候选和说明；补入完整文字框及局部短线。'}
        view['region_confirmed']=not issues and not ambiguous
        if audit:audit.event('VIEW_REGION_CHECKED',view_id=identity,**view['localization'])
    return result

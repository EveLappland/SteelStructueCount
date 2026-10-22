"""Length expert contracts, reference arithmetic and immutable-row patching.

No HTTP, PDF rendering or orchestration here. Geometry is auxiliary evidence;
consistent image measurements never become confirmed nominal lengths.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Literal

from pydantic import Field, field_validator
from .domain_base import Contract
from .member_recognition import LengthInput, calculate_length


class Anchor(Contract):
    name: str = Field(min_length=1,max_length=500)
    point: tuple[float,float]

    @field_validator('point')
    @classmethod
    def normalized(cls,value):
        if not all(0<=v<=1 for v in value):raise ValueError('point 必须是原图 [0,1] 坐标')
        return value


class DimensionReference(Contract):
    # References are dictionary keys, never paths. Decimal points or drawing
    # names in an otherwise consistent ID need not invalidate a whole reply.
    id: str = Field(min_length=1,max_length=80)
    kind: Literal['DIMENSION','LEVEL','OFFSET']
    axis: Literal['X','Y']
    value_mm: float
    start: Anchor | None = None
    end: Anchor | None = None
    evidence: str = Field(min_length=1,max_length=1800)
    source_ids: list[str] = Field(default_factory=list)
    baseline_basis: str | None = Field(default=None,max_length=1800)


class Term(Contract):
    reference_id: str
    factor: float = Field(ge=-100,le=100)


class Component(Contract):
    direction: Literal['DIRECT','HORIZONTAL','VERTICAL']
    terms: list[Term] = Field(min_length=1)
    basis: str = Field(min_length=1,max_length=1800)


class LengthResolution(Contract):
    member_id: str
    status: Literal['CONFIRMED','ESTIMATED','UNRESOLVED','CONFLICT']
    method: Literal['DIRECT','DIAGONAL','UNKNOWN']
    endpoints: list[Anchor] | None = Field(min_length=2,max_length=2)
    components: list[Component]
    reference_ids: list[str]
    issue: str | None = Field(max_length=2400)
    endpoint_kinds: list[Literal['END','SPLICE','INTERSECTION','UNKNOWN']] | None = Field(default=None,min_length=2,max_length=2)


class ContextRequest(Contract):
    bbox: tuple[float,float,float,float]
    reason: str = Field(min_length=1,max_length=1600)

    @field_validator('bbox')
    @classmethod
    def valid_box(cls,box):
        if not (0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1):raise ValueError('上下文区域必须是原图归一化有效矩形')
        return box


class LengthExpertResponse(Contract):
    references: list[DimensionReference]
    results: list[LengthResolution]
    context_request: ContextRequest | None = None


def target_rows(rows):
    return [row for row in rows if row.get('length_mm') is None or
            (row.get('length_input') or {}).get('method')=='DIAGONAL' or
            row.get('segmentation_status')=='UNRESOLVED']


def calibration(references,size):
    """Independent axis checks, at least two quoted spans per axis; no scale guess."""
    result={}
    for axis,index in [('X',0),('Y',1)]:
        samples=[]
        for ref in references:
            if ref['kind']!='DIMENSION' or ref['axis']!=axis or ref['value_mm']<=0:continue
            if not ref['start'] or not ref['end']:continue
            a,b=ref['start']['point'],ref['end']['point']
            span=abs(b[index]-a[index])*size[index]
            transverse=abs(b[1-index]-a[1-index])*size[1-index]
            if span<100 or transverse>max(6,span*.02):continue
            samples.append({'reference_id':ref['id'],'span_px':span,'mm_per_px':ref['value_mm']/span,
                            'interval':sorted([a[index],b[index]])})
        # Repeating the same span is not an independent calibration check.
        independent=[]
        for sample in samples:
            if not any(max(abs(a-b) for a,b in zip(sample['interval'],old['interval']))<.003 for old in independent):
                independent.append(sample)
        scales=sorted(sample['mm_per_px'] for sample in independent)
        center=scales[len(scales)//2] if scales else None
        spread=(max(scales)-min(scales))/center if scales else None
        result[axis]={'status':'consistent' if len(independent)>=2 and spread<=.02 else 'insufficient' if len(independent)<2 else 'conflict',
                      'mm_per_px':center,'relative_spread':spread,'samples':independent,
                      'note':'模型读出的标注与位置的数值一致性，未证明工程归属正确；点位存在定位误差。'}
    return result


def estimate(endpoints,scales,size):
    if not endpoints:return None
    a,b=[e['point'] for e in endpoints];dx=abs(a[0]-b[0])*size[0];dy=abs(a[1]-b[1])*size[1]
    needed=[('X',dx),('Y',dy)]
    if any(pixels>2 and scales[axis]['status']!='consistent' for axis,pixels in needed):return None
    x=dx*(scales['X']['mm_per_px'] or 0);y=dy*(scales['Y']['mm_per_px'] or 0)
    # An illustrative localization bound, NOT a statistically calibrated CI.
    error=math.hypot(*(6*(scales[axis]['mm_per_px'] or 0) if pixels>2 else 0 for axis,pixels in needed))
    value=math.hypot(x,y)
    return {'length_mm':round(value,2),'assumed_error_mm':round(error,2),
            'assumed_endpoint_error_px':3,'basis':'经至少两个不同标注跨度核对的比例测量，仅供辅助；未用于确定长度或去重'} if value>0 else None


def assess_reply(payload,rows,size,*,target_ids=None):
    payload=LengthExpertResponse.model_validate(payload).model_dump(mode='json')
    targets={r['id']:r for r in rows if r['id'] in target_ids} if target_ids is not None else {r['id']:r for r in target_rows(rows)}
    refs={r['id']:r for r in payload['references']};feedback=[];updates={}
    counts=Counter(r['member_id'] for r in payload['results'])
    if len(refs)!=len(payload['references']):feedback.append({'check':'duplicate_reference','message':'尺寸引用 ID 重复。'})
    if set(counts)!=set(targets) or any(n!=1 for n in counts.values()):
        feedback.append({'check':'target_coverage','message':'必须且只能对每个指定构件返回一次结果。',
                         'missing':sorted(set(targets)-set(counts)),'unexpected':sorted(set(counts)-set(targets))})
    scales=calibration(list(refs.values()),size)
    for resolution in payload['results']:
        identity=resolution['member_id'];errors=[] if len(refs)==len(payload['references']) else ['尺寸引用ID重复，不能合入有歧义的表达式。'];length=None
        if identity not in targets or counts[identity]!=1:continue
        ids=set(resolution['reference_ids']);terms={term['reference_id'] for c in resolution['components'] for term in c['terms']}
        if not ids<=refs.keys() or not terms<=ids:errors.append('尺寸表达式必须引用本回复中存在且已声明的 reference_id。')
        if resolution['status']=='CONFIRMED':
            required={'DIRECT'} if resolution['method']=='DIRECT' else {'HORIZONTAL','VERTICAL'} if resolution['method']=='DIAGONAL' else set()
            directions=[c['direction'] for c in resolution['components']]
            if not required or set(directions)!=required or len(directions)!=len(required):errors.append('确定长度必须提供 DIRECT 或两条 DIAGONAL 直角边的唯一表达式。')
            if not resolution['endpoints'] or resolution['endpoints'][0]['name']==resolution['endpoints'][1]['name'] or resolution['endpoints'][0]['point']==resolution['endpoints'][1]['point']:errors.append('确定长度需要两个不同端点及其图示依据。')
            if not ids:errors.append('确定长度必须引用图示尺寸，比例测量不能直接声明为 CONFIRMED。')
            values={}
            if not errors:
                for component in resolution['components']:
                    direction=component['direction']
                    if any(t['factor']==0 for t in component['terms']):errors.append('尺寸表达式不能含零系数。')
                    expected='X' if direction=='HORIZONTAL' else 'Y' if direction=='VERTICAL' else None
                    if expected and any(refs[t['reference_id']]['axis']!=expected for t in component['terms']):errors.append('直角边方向与尺寸引用方向不一致。')
                    value=sum(Decimal(str(refs[t['reference_id']]['value_mm']))*Decimal(str(t['factor'])) for t in component['terms'])
                    if value<=0:errors.append('尺寸表达式计算结果必须为正。')
                    values[direction]=float(value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
                if not errors:
                    length=LengthInput(method=resolution['method'],direct_mm=values.get('DIRECT'),horizontal_mm=values.get('HORIZONTAL'),
                        vertical_mm=values.get('VERTICAL'),evidence='；'.join(c['basis'] for c in resolution['components'])[:1000]).model_dump()
        else:
            if resolution['method']!='UNKNOWN' or resolution['components']:errors.append('未确定或仅比例估测时 method=UNKNOWN、components=[]，不能填确定长度。')
            if not resolution['issue']:errors.append('非确定结果必须说明原因。')
        measured=estimate(resolution['endpoints'],scales,size)
        if resolution['status']=='ESTIMATED' and not measured:errors.append('比例估测缺少通过交叉检查的对应方向标定或端点。')
        for message in errors:feedback.append({'check':'length_reference_validation','member_id':identity,'message':message})
        updates[identity]={'resolution':resolution,'length_input':length,'estimate':measured,'errors':errors}
    return {'payload':payload,'updates':updates,'feedback':feedback,'calibration':scales,
            'missing':sorted(set(targets)-set(updates))}


def apply_reply(rows,assessment):
    result=deepcopy(rows);changes=[]
    for row in result:
        update=assessment['updates'].get(row['id'])
        if not update:continue
        resolution=update['resolution'];prior={k:deepcopy(row.get(k)) for k in ('length_mm','length_input','length_formula','issue','endpoints','segmentation_status')}
        refs=[r for r in assessment['payload']['references'] if r['id'] in resolution['reference_ids']]
        applied=bool(update['length_input'] and not update['errors'])
        if applied:
            row['length_input']=update['length_input'];row['length_mm'],row['length_formula'],_=calculate_length(LengthInput.model_validate(update['length_input']))
            row['endpoints']=[e['name'] for e in resolution['endpoints']];row['segmentation_status']='CONFIRMED'
            # Keep all previous text in history. Non-length concerns in already
            # known records remain active; unresolved-length explanations retire.
            if prior['length_mm'] is None or prior['segmentation_status']=='UNRESOLVED':row['issue']=resolution['issue']
        if update['estimate']:row['length_estimate']=update['estimate']
        row['length_expert_checks']=list(update['errors'])
        if not applied:row['length_expert_checks'].append('长度专家：'+(resolution['issue'] or '未获得可合入的尺寸依据'))
        row['length_review']={'status':resolution['status'] if not update['errors'] else 'CONFLICT','applied':applied,
            'before':prior,'resolution':resolution,'references':refs,'local_checks':update['errors'],
            'measurement':update['estimate'],'arithmetic_only':True}
        changes.append({'member_id':row['id'],'applied':applied,'before_mm':prior['length_mm'],'after_mm':row['length_mm'],
                        'status':row['length_review']['status'],'references':resolution['reference_ids']})
    for row in result:
        if row['id'] in assessment['missing']:
            row['length_expert_checks']=['长度专家未返回此目标记录，原结果保留。']
    return result,changes

"""Compact per-view ledger. PDF positions only bind the image to native evidence."""
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from typing import Literal
from pydantic import Field, StrictInt, field_validator
from .domain import Contract
from .profiles import section
from .axis_catalog import build_axis_catalog,axis_name
from .native_pdf import view_context


def normalize_view_tag(value):
    """Keep the named plane, stripping presentation-only drawing title prefixes."""
    value=value.strip()
    return axis_name(value) or re.sub(r'^(?:PLAN\s+AT|ELEVATION\s+ON\s+ALIGNMENT)\s+', '',value,flags=re.I).strip()


def normalize_view_tags(values):
    return sorted({name for value in values if (name:=normalize_view_tag(value))})


def member_key(row):
    """Physical grouping identity. Counts and source IDs are not identity fields."""
    profile,multiplier=section(row['spec'])
    spec=profile if multiplier==1 else f'{multiplier}LF-{profile}'
    return spec,tuple(normalize_view_tags(row['view_tags'])),row['length_mm']


def key_record(row):
    spec,tags,length=member_key(row)
    return {'spec':spec,'view_tags':list(tags),'length_mm':length}


class RecognizedMember(Contract):
    spec: str = Field(min_length=1, max_length=120)
    view_tags: list[str] = Field(min_length=1)
    member_cnt: StrictInt = Field(ge=1, le=10000)
    length_mm: float | None = Field(gt=0)
    location: str = Field(max_length=4000)
    issue: str | None = Field(max_length=4000)

    @field_validator('view_tags')
    @classmethod
    def plane_names_only(cls,values):
        values=normalize_view_tags(values)
        if not values:raise ValueError('view_tags 至少需要一个非空的平面或立面名称')
        return values


class MemberResponse(Contract):
    members: list[RecognizedMember]


class LengthInput(Contract):
    method: Literal['DIRECT', 'DIAGONAL', 'UNKNOWN']
    direct_mm: float | None = Field(default=None, gt=0)
    horizontal_mm: float | None = Field(default=None, gt=0)
    vertical_mm: float | None = Field(default=None, gt=0)
    evidence: str = Field(max_length=1000)


class ImageMember(Contract):
    spec: str = Field(min_length=1, max_length=120)
    # The model reports only axis/level names. A member need not lie on one;
    # the server adds the drawing's own plane after parsing the response.
    view_tags: list[str]
    member_cnt: StrictInt = Field(ge=1, le=10000)
    length: LengthInput
    location: str = Field(max_length=4000)
    issue: str | None = Field(max_length=4000)

    @field_validator('view_tags')
    @classmethod
    def plane_names_only(cls, values):
        return normalize_view_tags(values)


class ImageMemberResponse(Contract):
    members: list[ImageMember]


def calculate_length(length):
    """Use drawing dimensions only; never infer real length from image pixels."""
    if length.method == 'DIRECT':
        return length.direct_mm, 'direct_mm', [] if length.direct_mm else ['缺少直构件名义长度']
    if length.method == 'DIAGONAL':
        if length.horizontal_mm is None or length.vertical_mm is None:
            return None, 'sqrt(horizontal_mm² + vertical_mm²)', ['斜撑缺少水平或竖直直角边尺寸']
        horizontal = Decimal(str(length.horizontal_mm))
        vertical = Decimal(str(length.vertical_mm))
        value = (horizontal * horizontal + vertical * vertical).sqrt().quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return float(value), f'sqrt({horizontal}² + {vertical}²)', []
    return None, 'unknown', ['缺少可确认的长度依据']


def view_tag(view):
    """Drawing names are deduplication labels; application IDs remain provenance."""
    if view.get('plane_name'):return normalize_view_tag(view['plane_name'])
    if view['kind']=='ELEVATION' and view.get('fixed_label'):
        return normalize_view_tag(view['fixed_label'])
    return normalize_view_tag(view['title'])


def tag_aliases(view):
    """Old titles and internal IDs remain aliases of the canonical plane name."""
    aliases = {value for value in (view.get('id'), view.get('title'), view_tag(view)) if value}
    if view['kind'] == 'PLAN':
        match = re.search(r'\bAT\s+(.+)$', view['title'], re.I)
        if match:
            aliases.add(match[1].strip())
    aliases.update(normalize_view_tags(aliases))
    return aliases


def allowed_model_tags(bundle):
    """Request canonical names; response parsing still accepts legacy full titles."""
    tags = set(bundle['axis_catalog']['current_axes'])
    for view in bundle['view_catalog']:
        tags.update(tag_aliases({'title': view['tag'], 'kind': view['kind']}))
    return normalize_view_tags(tags)


def make_input(page, view, stage_one):
    """Pair the confirmed image with its catalogs and crop-scoped native evidence."""
    context_views=[view if v['id']==view['id'] else v for v in stage_one['views']]
    axes=stage_one.get('axis_catalog') or build_axis_catalog([page],context_views)
    view_catalog=[{'tag':view_tag(v),'kind':v['kind'],
                   'alignment':v.get('fixed_label') if v['kind']=='ELEVATION' else None,
                   'axes':axes['per_view'].get(v['id'],[]),'uploaded':True}
                  for v in context_views if v['kind']!='ISOMETRIC']
    known_tags=set().union(*(tag_aliases(v) for v in context_views if v['kind']!='ISOMETRIC'))
    for plane in axes['axes']:
        if plane['name'] in known_tags:continue
        kind=plane.get('kind') or ('PLAN' if plane['name'].upper().startswith('EL.') else 'ELEVATION')
        view_catalog.append({'tag':plane['name'],'kind':kind,
                             'alignment':plane['name'] if kind=='ELEVATION' else None,
                             'axes':[],'uploaded':bool(plane.get('uploaded_view_ids'))})
    return {
        'current_view_tag':view_tag(view),
        'profile_catalog':list(stage_one.get('profile_catalog',[])),
        'native_evidence':{'page_id':page['id'],'crop_bbox':list(view['bbox']),
                           'coordinate_system':'normalized PDF page coordinates, not real-world millimetres',
                           **view_context(page,view['bbox'])},
        'axis_catalog':{'axes':[{'name':a['name'],'kind':a.get('kind','ELEVATION')} for a in axes['axes']],
                        'axis_count':axes['axis_count'],
                        'definition':'轴线或面名称：Alignment立面或EL标高平面。current_axes 是当前视图已提取名称，可能漏取；不能据此删除图片中可见的面标签，也不代表均已上传独立视图',
                        'current_axes':axes['per_view'].get(view['id'],[])},
        'view_catalog':view_catalog,
    }


def recognition_prompt(bundle):
    guidance = (Path(__file__).parent/'prompts'/'stage_two_members.md').read_text(encoding='utf-8')
    # The model sees image plus stage-one catalogs. Native PDF data stays local
    # for validation and audit, as requested for the image-first workflow.
    compact={key:bundle[key] for key in ('current_view_tag','profile_catalog','view_catalog','axis_catalog','image') if key in bundle}
    return guidance + '\n输入包：\n' + json.dumps(compact, ensure_ascii=False, separators=(',', ':'))


def validate_rows(rows, view, stage_one):
    catalog=[v for v in stage_one['views'] if v['kind']!='ISOMETRIC']
    allowed = {view_tag(v) for v in catalog}|{normalize_view_tag(a['name']) for a in stage_one.get('axis_catalog',{}).get('axes',[])}
    aliases={}
    for v in catalog:
        for alias in tag_aliases(v):
            aliases.setdefault(alias,set()).add(view_tag(v))
    source=next((v for v in catalog if v['id']==view['id']),view)
    profiles = {section(p)[0] for p in stage_one.get('profile_catalog', [])}
    result = []
    for index, raw in enumerate(rows, 1):
        length_input = raw.get('length') or raw.get('length_input')
        calculated_issues = []
        formula = raw.get('length_formula', '')
        if length_input:
            length_input = LengthInput.model_validate(length_input)
            computed, formula, calculated_issues = calculate_length(length_input)
            if 'length' in raw or length_input.method == 'DIAGONAL':
                raw = {**raw, 'length_mm': computed}
            length_input = length_input.model_dump()
        # Saved ledgers may predate location. Do not pretend their old basis is
        # an axis location; retain it separately and ask for a position on edit.
        source_tag=view_tag(source)
        prepared={k: raw.get(k,'') if k=='location' else raw[k]
                  for k in RecognizedMember.model_fields if k!='view_tags'}
        prepared['view_tags']=normalize_view_tags([*raw.get('view_tags',[]),source_tag])
        row = RecognizedMember.model_validate(prepared).model_dump()
        issues = list(calculated_issues)
        issues.extend(raw.get('grid_checks',[]))
        issues.extend(raw.get('length_expert_checks',[]))
        tags=[]
        for tag in row['view_tags']:
            matches=aliases.get(tag,set())
            if len(matches)==1:tags.append(next(iter(matches)))
            else:
                tags.append(tag)
                if len(matches)>1:issues.append('视图名称对应多个目录项，需确认标签')
        row['view_tags'] = sorted(set(tags))
        identity=key_record(row)
        if set(row['view_tags']) - allowed: issues.append('引用了目录外或概览视图标签')
        if len(set(row['view_tags'])-{source_tag})>1:
            issues.append('模型返回多个所属轴面；不同标签的构件应分行，原行及数量保留')
        # Stage one's per-view list is a partial observation, not proof that a
        # globally known plane is absent from this member. Preserve the model's
        # image-based cross-view tags for later reconciliation.
        if section(row['spec'])[0] not in profiles: issues.append('规格不在第一阶段已确认目录')
        if row['length_mm'] is None: issues.append('缺少可确认的长度依据')
        if not row['location']: issues.append('缺少构件位置说明，需补充核对')
        if row['issue']: issues.insert(0, row['issue'])
        result.append({**row, 'id': raw.get('id', f"{view['id']}-m{index:03d}"),
                       'source_view': view['id'], 'page_id': view['page_id'],
                       'source_plane_tag':source_tag,'member_key':identity,
                       'evidence_ids':raw.get('evidence_ids',[]),
                       **({'length_input': length_input, 'length_formula': formula} if length_input else {}),
                       **({'legacy_basis':raw.get('legacy_basis',raw.get('basis'))}
                          if raw.get('legacy_basis') or raw.get('basis') else {}),
                       'checks': list(dict.fromkeys(issues)), 'review_status': 'pending_review',
                       **{name:raw[name] for name in ('candidate_ids','segmentation_status','endpoints','original_label_point','grid_checks',
                           'length_review','length_estimate','length_expert_checks') if name in raw}})
    return result


def validate_ledger(data):
    """Recheck edits against catalog, preserve server source identities and provenance."""
    views = {v['id']: v for v in data['views']}
    seen = set()
    rows = []
    for raw in data.get('view_members', []):
        if raw['id'] in seen: raise ValueError('构件台账 ID 重复')
        seen.add(raw['id'])
        view = views.get(raw['source_view'])
        from .view_filter import eligible
        if not view or not eligible(view): raise ValueError('构件来源必须是已纳入统计的平面或立面')
        rows.extend(validate_rows([raw], view, data))
    data['view_members'] = rows
    data['length_catalog'] = [
        {'spec': spec, 'length_mm': length, 'member_cnt': sum(r['member_cnt'] for r in rows
          if r['spec'] == spec and r['length_mm'] == length), 'deduplicated': False}
        for spec, length in sorted({(r['spec'], r['length_mm']) for r in rows},
                                  key=lambda p: (p[0], p[1] is None, p[1] or 0))]
    return data

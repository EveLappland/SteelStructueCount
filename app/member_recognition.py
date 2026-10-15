"""Compact per-view ledger. PDF positions only bind the image to native evidence."""
import json
import re
from pathlib import Path

from pydantic import Field, StrictInt, field_validator
from .domain import Contract
from .geometry import section
from .axis_catalog import build_axis_catalog,axis_name
from .native_pdf import view_context


def normalize_view_tag(value):
    """Keep the named plane, stripping presentation-only drawing title prefixes."""
    value=value.strip()
    return axis_name(value) or re.sub(r'^(?:PLAN\s+AT|ELEVATION\s+ON\s+ALIGNMENT)\s+', '',value,flags=re.I).strip()


def normalize_view_tags(values):
    return sorted({name for value in values if (name:=normalize_view_tag(value))})


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


def view_tag(view):
    """Drawing names are deduplication labels; application IDs remain provenance."""
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
                        'definition':'轴线或面名称：Alignment立面或EL标高平面；与当前图纸绑定，不代表均已上传独立视图',
                        'current_axes':axes['per_view'].get(view['id'],[])},
        'view_catalog':view_catalog,
    }


def recognition_prompt(bundle):
    guidance = (Path(__file__).parent/'prompts'/'stage_two_members.md').read_text(encoding='utf-8')
    # Only the view-scoped package built above is sent, not arbitrary caller fields.
    compact={key:bundle[key] for key in ('current_view_tag','profile_catalog','view_catalog','axis_catalog','image','native_evidence') if key in bundle}
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
        # Saved ledgers may predate location. Do not pretend their old basis is
        # an axis location; retain it separately and ask for a position on edit.
        row = RecognizedMember.model_validate({k: raw.get(k,'') if k=='location' else raw[k]
                                              for k in RecognizedMember.model_fields}).model_dump()
        issues = []
        tags=[]
        for tag in row['view_tags']:
            matches=aliases.get(tag,set())
            if len(matches)==1:tags.append(next(iter(matches)))
            else:
                tags.append(tag)
                if len(matches)>1:issues.append('视图名称对应多个目录项，需确认标签')
        row['view_tags'] = sorted(set(tags))
        if view_tag(source) not in row['view_tags']: issues.append('标签未包含当前来源视图')
        if set(row['view_tags']) - allowed: issues.append('引用了目录外或概览视图标签')
        axes=stage_one.get('axis_catalog',{})
        current_axes=set(normalize_view_tags(axes.get('per_view',{}).get(view['id'],[])))
        axis_names={normalize_view_tag(a['name']) for a in axes.get('axes',[])}
        if any(tag in axis_names and tag not in current_axes for tag in row['view_tags']):
            issues.append('引用的轴线或面名称未绑定到第一阶段的当前视图')
        if section(row['spec'])[0] not in profiles: issues.append('规格不在第一阶段已确认目录')
        if row['length_mm'] is None: issues.append('缺少可确认的长度依据')
        if not row['location']: issues.append('缺少构件位置说明，需补充核对')
        if row['issue']: issues.insert(0, row['issue'])
        result.append({**row, 'id': raw.get('id', f"{view['id']}-m{index:03d}"),
                       'source_view': view['id'], 'page_id': view['page_id'],
                       'evidence_ids':raw.get('evidence_ids',[]),
                       **({'legacy_basis':raw.get('legacy_basis',raw.get('basis'))}
                          if raw.get('legacy_basis') or raw.get('basis') else {}),
                       'checks': list(dict.fromkeys(issues)), 'review_status': 'pending_review'})
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

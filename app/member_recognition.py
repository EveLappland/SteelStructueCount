"""Compact per-view ledger. PDF positions only bind the image to native evidence."""
import json
from pathlib import Path

from pydantic import Field, StrictInt
from .domain import Contract
from .geometry import section
from .native_pdf import view_context, inside


class RecognizedMember(Contract):
    spec: str = Field(min_length=1, max_length=120)
    view_tags: list[str] = Field(min_length=1)
    member_cnt: StrictInt = Field(ge=1, le=10000)
    length_mm: float | None = Field(gt=0)
    basis: str = Field(min_length=1, max_length=4000)
    issue: str | None = Field(max_length=4000)


class MemberResponse(Contract):
    members: list[RecognizedMember]


def view_tag(view):
    """Drawing names are deduplication labels; application IDs remain provenance."""
    if view['kind']=='ELEVATION' and view.get('fixed_label'):
        return view['fixed_label']
    return view['title']


def make_input(page, view, stage_one):
    # Keep geometry auxiliary: a large path dump can overwhelm the visual task.
    context = view_context(page, view['bbox'], max_vectors=120)
    catalog = stage_one.get('discovery_inventory', {}).get('profiles', [])
    labels = [e for p in catalog for e in p.get('evidence', [])
              if e.get('page_id') == page['id'] and inside(e, view['bbox'])]
    # Native labels are also retained in context.texts even for older inventory formats.
    return {
        'current_view_id': view['id'], 'current_view_title': view['title'],
        'current_view_tag':view_tag(view),
        'page': {'id': page['id'], 'file': page['file'], 'page': page['page'],
                 'width_pt': page['width_pt'], 'height_pt': page['height_pt']},
        'crop_bbox_page_normalized': view['bbox'],
        'coordinate_usage': '文字和矢量位置为原PDF整页归一化坐标，仅用于与裁切图片关联。',
        'recognition_priority':'高清图片及识别步骤优先；原生文字用于读数，少量矢量仅辅助轮廓，不要求坐标求解。',
        'profile_catalog': stage_one.get('profile_catalog', []),
        'view_catalog': [{**{k: v.get(k) for k in ('id', 'title', 'kind', 'fixed_label',
                                               'elevation_m', 'elevation_basis')},'tag':view_tag(v)}
                         for v in stage_one['views']],
        'profile_labels': labels, 'native_evidence': context,
    }


def recognition_prompt(bundle):
    guidance = (Path(__file__).parent/'prompts'/'stage_two_members.md').read_text(encoding='utf-8')
    # Avoid repeating all native labels with file/page/catalog metadata in the prompt.
    compact={**bundle,'profile_labels':[{'text':e['text'],'bbox':e['bbox']} for e in bundle['profile_labels']]}
    return guidance + '\n输入包：\n' + json.dumps(compact, ensure_ascii=False, separators=(',', ':'))


def validate_rows(rows, view, stage_one):
    catalog=[v for v in stage_one['views'] if v['kind']!='ISOMETRIC']
    allowed = {view_tag(v) for v in catalog}
    aliases={}
    for v in catalog:
        for alias in (v['id'],v['title'],view_tag(v)):
            aliases.setdefault(alias,set()).add(view_tag(v))
    source=next((v for v in catalog if v['id']==view['id']),view)
    profiles = {section(p)[0] for p in stage_one.get('profile_catalog', [])}
    result = []
    for index, raw in enumerate(rows, 1):
        row = RecognizedMember.model_validate({k: raw[k] for k in RecognizedMember.model_fields}).model_dump()
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
        if len(row['view_tags']) < 2: issues.append('跨视图标签尚不完整')
        if section(row['spec'])[0] not in profiles: issues.append('规格不在第一阶段已确认目录')
        if row['length_mm'] is None: issues.append('缺少可确认的长度依据')
        if row['issue']: issues.insert(0, row['issue'])
        result.append({**row, 'id': raw.get('id', f"{view['id']}-m{index:03d}"),
                       'source_view': view['id'], 'page_id': view['page_id'],
                       'evidence_ids':raw.get('evidence_ids',[]),
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
        if not view or view['kind'] == 'ISOMETRIC': raise ValueError('构件来源必须是已登记的平面或立面')
        rows.extend(validate_rows([raw], view, data))
    data['view_members'] = rows
    data['length_catalog'] = [
        {'spec': spec, 'length_mm': length, 'member_cnt': sum(r['member_cnt'] for r in rows
          if r['spec'] == spec and r['length_mm'] == length), 'deduplicated': False}
        for spec, length in sorted({(r['spec'], r['length_mm']) for r in rows},
                                  key=lambda p: (p[0], p[1] is None, p[1] or 0))]
    return data

"""Text-only reconciliation proposals, isolated from independent references."""
import json
import hashlib
import re
import uuid
from pathlib import Path

from pydantic import Field

from .domain import Contract
from .group_reconciliation import Resolution,Exclusion,reconcile,source_hash
from .settings import _api_settings
from .member_recognition import view_tag
from .profiles import section
from .native_pdf import write_json


class GroupProposal(Contract):
    resolutions: list[Resolution] = Field(default_factory=list)
    exclusions: list[Exclusion] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def normalize_proposal_ids(payload, audit=None):
    """Repair descriptive group IDs only; never change observation references."""
    if not isinstance(payload, dict) or not isinstance(payload.get('resolutions'), list):
        return
    seen = set()
    for row in payload['resolutions']:
        if not isinstance(row, dict):
            continue
        original = row.get('id')
        if not isinstance(original, str) or not original.strip():
            continue  # Required/type checks still belong to the contract.
        canonical = original if re.fullmatch(r'[A-Za-z0-9_-]{1,120}', original) else (
            'group-' + hashlib.sha256(original.encode('utf-8')).hexdigest())
        if canonical in seen:
            raise ValueError('结果组ID不可重复（包括规范化后的编号）')
        seen.add(canonical)
        if canonical != original:
            row['id'] = canonical
            if audit:
                audit.event('GROUP_ID_NORMALIZED', model_id=original, canonical_id=canonical,
                            reason='仅规范化结果组编号；来源观察、数量、规格、长度和标签保持原响应值。')


def unallocated_usable_rows(two, result):
    """Catalog-unknown observations can remain pending, not forced into a false group."""
    invalid = {r['row_id'] for r in result['review']['invalid_rows']}
    profiles = {section(p)[0] for p in two['profile_catalog']}
    usable = {r['id'] for r in two['view_members'] if r['id'] not in invalid
              and r.get('length_mm') and r.get('view_tags') and section(r['spec'])[0] in profiles}
    return [e for e in result['exceptions'] if e['observation_id'] in usable and e.get('member_cnt')]


def propose_groups(two,directory,settings,emit,audit,*,reuse_saved=False):
    from .pipeline import model_call,OpenAI
    folder=directory/'responses'/f'attempt-groups-{uuid.uuid4().hex}'
    folder.mkdir(parents=True)
    snapshot=folder/'stage_two_input.json';write_json(snapshot,two)
    bundle={'source_hash':source_hash(two),'profile_catalog':two['profile_catalog'],
        'views':[{'id':v['id'],'name':view_tag(v),'kind':v['kind'],
                  'takeoff_role':v.get('takeoff_role','MEMBERS'),
                  'bound_planes':two.get('axis_catalog',{}).get('per_view',{}).get(v['id'],[])}
                 for v in two['views'] if v['kind']!='ISOMETRIC'],
        'observations':[{k:r.get(k) for k in ('id','source_view','spec','view_tags','member_cnt','length_mm','location','issue','checks')}
                        for r in two['view_members']],
        'view_analysis':two.get('view_analysis',[])}
    guidance=(Path(__file__).parent/'prompts/stage_three_groups.md').read_text(encoding='utf-8')
    base,key,model=_api_settings(settings)
    emit('AI生成分组核验提案：只发送第二阶段台账与面目录，不发送验收参考答案。')
    prompt=guidance+'\n输入台账：\n'+json.dumps(bundle,ensure_ascii=False,separators=(',',':'))
    client=OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1)
    try:
        for attempt in range(3):
            path=folder/('proposal.json' if attempt==0 else f'proposal_repair{attempt}.json')
            proposal=model_call(client,model,prompt,
                {'id':'group-review','page':1,'source_path':str(snapshot),'text_only':True},
                path,GroupProposal,emit=emit,audit=audit,reasoning_effort=settings.get('reasoning_effort','high'),reuse_saved=reuse_saved)
            draft=reconcile(two);draft.update(resolutions=proposal['resolutions'],exclusions=proposal['exclusions'])
            try:
                result=reconcile(two,draft)
                # No local invention of groups. Ask the model to account for
                # every usable row, rather than silently exporting an empty ledger.
                remaining=unallocated_usable_rows(two,result)
                if remaining:raise ValueError('仍有具备长度的观察未分配：'+json.dumps(remaining,ensure_ascii=False))
                validation={'valid':True,'totals':result['totals']};break
            except ValueError as error:
                result=None;validation={'valid':False,'error':str(error)}
                audit.event('GROUP_PROPOSAL_REJECTED',attempt=attempt,error=str(error))
                if attempt<2:
                    emit(f'AI分组本地检查未通过，正在请求修正（{attempt+1}/2）。')
                    prompt=guidance+'\n输入台账：\n'+json.dumps(bundle,ensure_ascii=False,separators=(',',':'))+'\n上次提案：\n'+json.dumps(proposal,ensure_ascii=False)+'\n本地检查错误：\n'+str(error)+'\n目录外规格的观察可以不分配、保留待确认；不得仅为通过校验换规格或排除。其余具备长度和目录内规格的观察仍须完整分配。修正并返回完整结果，不只返回差异。'
    finally:client.close()
    write_json(folder/'validation.json',validation)
    if result is not None:write_json(folder/'validated_proposal.json',result)
    audit.event('GROUP_PROPOSAL_CHECKED',source_hash=bundle['source_hash'],proposal_file=str(folder/'proposal.json'),
                **validation,reason='本地检查来源分配与数量守恒；通过后由阶段执行器自动采用，不代表工程准确性验收。')
    return {'proposal':proposal,'validation':validation,'data':result,'folder':str(folder)}

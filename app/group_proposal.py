"""Text-only reconciliation proposals, isolated from independent references."""
import json
import uuid
from pathlib import Path

from pydantic import Field

from .domain import Contract
from .group_reconciliation import Resolution,Exclusion,reconcile,source_hash
from .llm_runner import _api_settings
from .member_recognition import view_tag
from .native_pdf import write_json


class GroupProposal(Contract):
    resolutions: list[Resolution] = Field(default_factory=list)
    exclusions: list[Exclusion] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    proposal=model_call(OpenAI(base_url=base,api_key=key,timeout=600,max_retries=1),model,
        guidance+'\n输入台账：\n'+json.dumps(bundle,ensure_ascii=False,separators=(',',':')),
        {'id':'group-review','page':1,'source_path':str(snapshot),'text_only':True},
        folder/'proposal.json',GroupProposal,emit=emit,audit=audit,reasoning_effort='max',reuse_saved=reuse_saved)
    draft=reconcile(two);draft.update(resolutions=proposal['resolutions'],exclusions=proposal['exclusions'])
    try:
        result=reconcile(two,draft)
        validation={'valid':True,'totals':result['totals']}
    except ValueError as error:
        result=None;validation={'valid':False,'error':str(error)}
    write_json(folder/'validation.json',validation)
    if result is not None:write_json(folder/'validated_proposal.json',result)
    audit.event('GROUP_PROPOSAL_CHECKED',source_hash=bundle['source_hash'],proposal_file=str(folder/'proposal.json'),
                **validation,reason='本地检查来源分配与数量守恒；这不证明图纸识别准确，未修改或确认任务。')
    return {'proposal':proposal,'validation':validation,'data':result,'folder':str(folder)}

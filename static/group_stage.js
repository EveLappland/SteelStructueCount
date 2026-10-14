'use strict';
function renderGroupReconciliation() {
    const data=draft.data,target=$('#stage-content'),rows=matchSource.view_members;
    const totals=data.totals;
    target.append(node('p',`已确认 ${totals.confirmed_groups} 组 / ${totals.confirmed_assemblies} 个组合实体 / ${totals.confirmed_single_members} 根单件；待核验观察 ${totals.pending_observation_units} 个，未完成来源视图 ${totals.pending_source_views} 个。`),
        node('p','修改后请保存，服务器会重新核算数量。每个结果组从各来源分配同等数量；部分重叠须拆成多个结果组。组合角钢在最终统计时展开一次。'),
        table(['钢材类型','名义长度 mm','已确认单根数量','总长度 mm'],data.summary.map(s=>[s.profile,s.length_mm,s.quantity,s.total_length_mm])));
    target.append(button('AI生成分组提案（max）',async()=>{
        try{
            if(dirty)throw Error('请先保存当前修改，再生成AI提案。');
            await api(`/api/jobs/${jobId}/stages/3/retry`,'POST',{revision:draft.revision,reuse_saved:false,settings:{...settings(),review_mode:'ai'}});
            stageKey='';await poll();
        }catch(error){showError(error);}
    }));
    const candidates=node('details');candidates.append(node('summary',`匹配候选目录（${data.review.groups.length} 组）`));
    const editor=node('section'),compare=node('div','',{class:'comparison'});
    const selected=node('select');
    const ai=data.review.ai_proposal;
    if(ai) {
        target.append(node('p',ai.validation.valid?'AI提案已通过来源分配与数量守恒检查，仍需核对图纸准确性。':`AI提案未通过本地检查：${ai.validation.error}`),
            ...(ai.validation.totals?[node('p',`AI提案：${ai.validation.totals.confirmed_assemblies} 个构件段，组合展开后 ${ai.validation.totals.confirmed_single_members} 根；排除 ${ai.validation.totals.excluded_observation_units} 个错误观察。载入并核对保存前不计入上方已确认汇总。`)]:[]),
            ...ai.proposal.notes.map(note=>node('p',note)),
            button('载入AI提案到可编辑草稿',()=>{
                if(data.resolutions.length||data.exclusions.length){showError(Error('当前已有核验修改，请先通过历史保存/恢复，或明确清空结果组后再载入AI提案，避免覆盖。'));return;}
                data.resolutions=structuredClone(ai.proposal.resolutions);data.exclusions=structuredClone(ai.proposal.exclusions);
                mark();updateChoices();showExclusions();edit(data.resolutions[0]);
            }));
    }
    function updateChoices() {
        selected.replaceChildren(node('option','选择已有结果组',{value:''}),...data.resolutions.map(r=>node('option',`${r.id} · ${r.spec} · ${r.length_mm} mm × ${r.member_cnt}`,{value:r.id})));
    }
    selected.onchange=()=>edit(data.resolutions.find(r=>r.id===selected.value));
    target.append(candidates,node('h3','核验结果组'),selected,button('新建结果组',()=>{
        const r={id:`result-${crypto.randomUUID()}`,spec:matchSource.profile_catalog[0]||'',view_tags:[],length_mm:0,member_cnt:1,allocations:[],reason:''};
        data.resolutions.push(r);mark();updateChoices();edit(r);
    }),editor,compare);
    function edit(result) {
        editor.replaceChildren();compare.replaceChildren();if(!result)return;selected.value=result.id;
        const allocations=node('div');
        editor.append(input('规格（组合保持 2LF 等前缀）',result.spec,v=>{result.spec=v;mark();}),
            input('所属面名称（逗号分隔）',result.view_tags.join(', '),v=>{result.view_tags=[...new Set(splitRefs(v).map(LedgerTools.normalizeTag).filter(Boolean))];mark();}),
            input('名义段长 mm',result.length_mm,v=>{result.length_mm=Number(v);mark();},'number'),
            input('结果组数量（展开组合前）',result.member_cnt,v=>{result.member_cnt=Number(v);mark();},'number'),
            input('核验依据（必填）',result.reason,v=>{result.reason=v;mark();}),allocations);
        function showAllocations() {
            allocations.replaceChildren(table(['来源观察','原数量','分配数量','操作'],result.allocations.map(a=>{
                const row=rows.find(r=>r.id===a.row_id);
                return [`${row?.source_view} · ${a.row_id} · ${row?.location}`,row?.member_cnt,
                    input('分配',a.member_cnt,v=>{a.member_cnt=Number(v);mark();},'number'),
                    button('移除分配',()=>{result.allocations=result.allocations.filter(x=>x!==a);mark();showAllocations();})];
            })));
            const sourceSelect=node('select');sourceSelect.append(...rows.filter(r=>!result.allocations.some(a=>a.row_id===r.id)).map(r=>node('option',`${r.id} · ${r.spec} × ${r.member_cnt} · ${r.location}`,{value:r.id})));
            allocations.append(sourceSelect,button('添加来源分配',()=>{
                if(!sourceSelect.value)return;result.allocations.push({row_id:sourceSelect.value,member_cnt:1});mark();showAllocations();
            }));
            compare.replaceChildren();
            for(const source of new Set(result.allocations.map(a=>rows.find(r=>r.id===a.row_id)?.source_view))) {
                const view=matchSource.views.find(v=>v.id===source);if(!view)continue;
                const image=`/api/jobs/${jobId}/views/${encodeURIComponent(view.image_file||view.id+'_300dpi.png')}`;
                const figure=node('figure');figure.append(node('figcaption',view.title),node('a','打开高清原图',{href:image,target:'_blank',rel:'noopener'}),
                    node('img','',{src:image,alt:view.title,loading:'lazy',style:'max-width:100%'}));compare.append(figure);
            }
        }
        editor.append(button('删除结果组',()=>{data.resolutions=data.resolutions.filter(r=>r!==result);mark();updateChoices();edit(null);}));
        showAllocations();
    }
    candidates.append(table(['规格','长度 mm','所属面','各来源数量','核验提示','操作'],data.review.groups.map(g=>[
        g.spec,g.length_mm,g.view_tags,g.sources.map(s=>`${s.title}: ${s.member_cnt}`).join('；'),g.review_reasons.join('；'),
        button('载入核验编辑',()=>{
            const id=`result-${g.id}`;let r=data.resolutions.find(r=>r.id===id);
            if(!r){r={id,spec:g.spec,view_tags:[...g.view_tags],length_mm:g.length_mm||0,member_cnt:g.agreed_member_cnt||1,
                allocations:g.sources.flatMap(s=>s.row_ids.map(id=>({row_id:id,member_cnt:rows.find(r=>r.id===id).member_cnt}))),reason:''};data.resolutions.push(r);mark();updateChoices();}
            edit(r);editor.scrollIntoView({block:'start',behavior:'smooth'});
        })])));
    const exclusions=node('details');exclusions.append(node('summary','明确排除错误观察（保留原记录和原因）'));
    function showExclusions() {
        exclusions.replaceChildren(node('summary','明确排除错误观察（保留原记录和原因）'),table(['观察 ID','排除数量','依据','操作'],data.exclusions.map(e=>[
            input('观察 ID',e.row_id,v=>{e.row_id=v;mark();}),input('数量',e.member_cnt,v=>{e.member_cnt=Number(v);mark();},'number'),
            input('排除依据',e.reason,v=>{e.reason=v;mark();}),button('撤销',()=>{data.exclusions=data.exclusions.filter(x=>x!==e);mark();showExclusions();})])),
            button('添加排除记录',()=>{data.exclusions.push({row_id:'',member_cnt:1,reason:''});mark();showExclusions();}));
    }
    showExclusions();target.append(exclusions);updateChoices();
}

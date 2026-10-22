'use strict';
const $ = selector => document.querySelector(selector);
const stageNames = {1:'视图、轴线与高清裁切',2:'图片识别钢材台账',3:'跨视图匹配与单根统计'};
const stateNames = {queued:'排队中',running:'执行中',awaiting_review:'等待阶段确认',approved:'已确认',stale:'已失效',failed:'失败',interrupted:'已中断，可重试',exporting:'导出中',completed:'已完成',filtered:'已过滤',filter_pending:'用途待确认',completed_with_warnings:'已完成（有待处理项）',recognizing_grids:'网格识别中',coordinating:'总代理汇总中',checking_lengths:'长度专家核验中',gathering_length_evidence:'长度补证中',verifying_lengths:'独立核验中',arbitrating_lengths:'自动仲裁中',skipped:'无需专项处理',empty:'空台账',region_pending:'裁切待确认'};
let jobId='', job=null, draft=null, stageNumber=1, stageKey='', pages=[], dirty=false, timer=null;
let activeView=null, matchSource=null;
function node(tag,text='',attrs={}) { const n=document.createElement(tag); n.textContent=text; for(const [k,v] of Object.entries(attrs)) { if(k==='class') n.className=v; else n.setAttribute(k,v); } return n; }
function showError(error) { console.error(error);$('#error').hidden=false; $('#error').textContent=error.message||String(error); }
async function api(path,method='GET',body=null) { const opts={method}; if(body) { opts.headers={'Content-Type':'application/json'};opts.body=JSON.stringify(body); } const r=await fetch(path,opts);let data;try{data=await r.json();}catch{throw Error('服务器返回了无法解析的响应。');}if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data; }
function settings() { const raw=$('#max-concurrency').value.trim();const parallel=Number(raw);if(raw&&(!Number.isInteger(parallel)||parallel<1))throw Error('AI请求并发数必须是正整数。');return {base_url:$('#base-url').value,api_key:$('#api-key').value,model:$('#model').value,max_concurrency:raw?String(parallel):'',label_color_mode:$('#label-color-mode').value}; }
async function refreshTrace() {
    if(!jobId)return;
    try {
        let result=await api(`/api/jobs/${jobId}/logs?limit=1000`);
        if(result.total>1000)result=await api(`/api/jobs/${jobId}/logs?after=${result.total-1000}&limit=1000`);
        $('#trace-content').replaceChildren(node('p',`显示最近 ${result.items.length} / ${result.total} 条，完整记录可下载。`),...result.items.map(item=>{
            const details=node('details','',{class:'trace-row'});
            details.append(node('summary',`${new Date(item.at).toLocaleTimeString()} · ${item.event}${item.reason?' · '+item.reason:''}`));
            details.append(node('pre',JSON.stringify(item,null,2)));
            return details;
        }));
        $('#download-trace').href=`/api/jobs/${jobId}/logs-download`;
    } catch(error) { showError(error); }
}
function mark() { dirty=true;$('#save').disabled=false; }
function input(label,value,onChange,type='text') { const wrap=node('label',label);const field=node('input','',{type});field.value=value??'';field.addEventListener('input',()=>onChange(field.value));wrap.append(field);return wrap; }
function select(label,value,options,onChange) { const wrap=node('label',label),field=node('select'); for(const [v,text] of options) { const option=node('option',text,{value:v});option.selected=String(value??'')===String(v);field.append(option); }field.onchange=()=>onChange(field.value);wrap.append(field);return wrap; }
function splitRefs(value) { return value.split(',').map(v=>v.trim()).filter(Boolean); }
function setField(target,key,value,type='text') { target[key]=type==='number'?(value===''?null:Number(value)):value;mark(target); }
function table(headers,rows) { const table=node('table'),head=node('tr');headers.forEach(h=>head.append(node('th',h)));table.append(head);rows.forEach(values=>{const tr=node('tr');values.forEach(value=>{const td=node('td');if(value instanceof Node)td.append(value);else td.textContent=value==null?'待确认':typeof value==='object'?JSON.stringify(value):String(value);tr.append(td);});table.append(tr);});const wrap=node('div','',{class:'table-wrap'});wrap.append(table);return wrap; }
function button(text,action,style='') { const b=node('button',text,{type:'button',class:style});b.onclick=action;return b; }
function refreshJson() { $('#json-editor').value=JSON.stringify(draft.data,null,2); }
async function loadJobs() { const list=await api('/api/jobs');$('#job-list').replaceChildren(node('option','选择任务',{value:''}));for(const item of list)$('#job-list').append(node('option',`${new Date(item.created_at).toLocaleString()} · ${stateNames[item.state]} · ${item.id.slice(0,8)}`,{value:item.id}));$('#job-list').value=jobId; }
async function chooseJob(id) { if(dirty&&!window.confirm('当前草稿尚未保存，是否离开？'))return;clearTimeout(timer);jobId=id;history.replaceState(null,'',id?'/?job='+encodeURIComponent(id):'/');$('#task-trace').hidden=!id;stageKey='';dirty=false;draft=null;await poll(); }
async function poll() { clearTimeout(timer);try { if(!jobId)return;job=await api(`/api/jobs/${jobId}`);$('#status').textContent=`${stateNames[job.state]||job.state} · ${jobId}`;$('#event-log').replaceChildren(...job.events.map(e=>node('li',`${new Date(e.at).toLocaleTimeString()} ${e.message}`)));if(job.error_detail)showError(Error(job.error_detail));$('#downloads').replaceChildren();for(const [url,label] of [[job.download_url,'下载 Excel'],[job.views_download_url,'下载截图与证据包']])if(url)$('#downloads').append(node('a',label,{href:url,class:'download'}));
renderStageTabs();
const current=job.stages.find(s=>s.stage===Math.min(job.active_stage,3));const key=current?`${current.stage}:${current.revision}:${current.status}`:'';if(key!==stageKey&&!dirty){stageKey=key;if(current)await openStage(current.stage,false);}if(['queued','running','exporting'].includes(job.state))timer=setTimeout(poll,1800);await loadJobs(); }catch(e){showError(e);timer=setTimeout(poll,4000);} }
async function openStage(n,checkDirty=true) { if(checkDirty&&dirty&&!window.confirm('当前修改尚未保存，是否切换阶段？'))return;try{draft=await api(`/api/jobs/${jobId}/stages/${n}`);stageNumber=n;pages=await api(`/api/jobs/${jobId}/pages`);matchSource=n===3?(await api(`/api/jobs/${jobId}/stages/2`)).data:null;dirty=false;$('#error').hidden=true;renderStage();}catch(e){showError(e);} }
function renderStageTabs() { $('#stage-tabs').replaceChildren(...job.stages.map(s=>{const b=button(`${s.stage}. ${stageNames[s.stage]} · ${stateNames[s.status]}`,()=>openStage(s.stage),s.stage===stageNumber?'active':'');b.setAttribute('aria-current',s.stage===stageNumber?'step':'false');return b;})); }
function renderStage() { renderStageTabs();$('#review').hidden=false;$('#stage-title').textContent=`阶段 ${stageNumber} · ${stageNames[stageNumber]}`;$('#stage-state').textContent=`版本 ${draft.revision} · ${stateNames[draft.status]}`;$('#stage-content').replaceChildren();$('#warnings').replaceChildren();const data=draft.data;const editable=['awaiting_review','approved','failed','interrupted'].includes(draft.status)&&!['queued','running','exporting'].includes(job.state);$('#save').disabled=!editable;$('#approve').disabled=draft.status!=='awaiting_review';const restartTwo=job.state==='completed'&&stageNumber===2&&draft.status==='approved';$('#retry').disabled=!restartTwo&&(!(job.active_stage===stageNumber||job.active_stage===4&&stageNumber===3)||!['failed','interrupted','awaiting_review','completed'].includes(job.state));$('#retry').textContent=restartTwo?'从第二阶段重新执行':'重试当前阶段';$('#retry').title=restartTwo?'复用第一阶段高清图，重新调用 AI；旧台账保留历史，第三阶段和导出重新生成。':'';$('#approve').textContent=stageNumber===3?'采用当前台账并导出':'继续下一阶段';
if((stageNumber<=2&&!data.views)||(stageNumber===3&&!Array.isArray(data.summary))){$('#save').disabled=true;$('#approve').disabled=true;$('#stage-content').append(node('p','该阶段尚未生成可用台账。运行中请等待；失败后可重试当前阶段。'));if(job.error_detail)showError(Error(job.error_detail));refreshJson();renderHistory();return;}
const warnings=stageNumber===1?data.warnings||[]:stageNumber===2?(data.warnings||[]):(data.exceptions||[]).map(e=>`${e.observation_id}：${e.issues.join('；')}`);
if(warnings.length>12){const details=node('details');details.append(node('summary',`${warnings.length} 项待确认，展开核对`),...warnings.map(w=>node('p',w)));$('#warnings').append(details);}else warnings.forEach(w=>$('#warnings').append(node('p',w)));
if(stageNumber===1&&data.catalog_mode==='ai_pages'){renderPreparedCatalog();}else if(stageNumber===2&&data.recognition_mode==='view_ledger'){renderViewLedger();}else if(stageNumber===3&&data.reconciliation_mode==='group_ledger'){renderGroupReconciliation();}else{$('#stage-content').append(node('p','历史流程仅支持查看完整数据及下载已有结果。'));$('#save').disabled=true;$('#approve').disabled=true;$('#retry').disabled=true;}refreshJson();renderHistory(); }
function renderHistory() { const target=$('#history');target.replaceChildren(node('h3','修改与确认记录'));for(const snap of draft.snapshots||[]){const row=node('p',`${snap.at} · v${snap.revision} · ${snap.action} · ${snap.reason} `);row.append(button('载入此草稿',()=>{if(!snap.data||!Object.keys(snap.data).length)return;draft.data=structuredClone(snap.data);mark();renderStage();dirty=true;}));target.append(row);} }
$('#files').onchange=()=>{$('#upload-error').hidden=true;$('#file-list').replaceChildren(...[...$('#files').files].map(f=>node('p',`${f.name} · ${(f.size/1048576).toFixed(1)} MB`)));};
$('#upload-form').onsubmit=async event=>{
    event.preventDefault();const start=$('#submit'),error=$('#upload-error');error.hidden=true;
    try{
        const files=[...$('#files').files];
        if(!files.length)throw Error('请先点击“选择 PDF 图纸”并选中文件，再开始第一阶段。刷新页面后需要重新选择文件。');
        if(files.some(file=>!file.name.toLowerCase().endsWith('.pdf')))throw Error('仅支持 PDF 文件，请重新选择图纸。');
        start.disabled=true;start.textContent='正在上传图纸…';
        const form=new FormData();for(const file of files)form.append('files',file);
        form.append('auto_run',String(!$('#pause-stages').checked));
        form.append('stop_after_stage',$('#stage12-only').checked?'2':'4');
        for(const [key,value]of Object.entries(settings()))form.append(key,value);
        const response=await fetch('/api/jobs',{method:'POST',body:form});
        const payload=await response.json();if(!response.ok)throw Error(payload.detail);
        dirty=false;await chooseJob(payload.id);
    }catch(e){error.hidden=false;error.textContent=e.message||String(e);}
    finally{start.disabled=false;start.textContent='开始第一阶段';}
};
$('#job-list').onchange=()=>chooseJob($('#job-list').value);
$('#apply-json').onclick=()=>{try{draft.data=JSON.parse($('#json-editor').value);mark();renderStage();dirty=true;}catch(e){showError(e);}};
$('#save').onclick=async()=>{try{const reason=$('#edit-reason').value.trim();if(!reason)throw Error('请填写修改依据。');
draft=await api(`/api/jobs/${jobId}/stages/${stageNumber}`,'PATCH',{revision:draft.revision,data:draft.data,reason});dirty=false;stageKey='';$('#edit-reason').value='';renderStage();await poll();}catch(e){showError(e);}};
$('#approve').onclick=async()=>{try{if(dirty)throw Error('请先保存修改，再确认阶段。');$('#approve').disabled=true;await api(`/api/jobs/${jobId}/stages/${stageNumber}/approve`,'POST',{revision:draft.revision,acknowledge_pending:$('#pending').checked,settings:stageNumber===1?settings():{}});$('#pending').checked=false;stageKey='';await poll();}catch(e){showError(e);$('#approve').disabled=false;}};
$('#retry').onclick=async()=>{try{if(dirty)throw Error('请先保存当前修改。');$('#retry').disabled=true;await api(`/api/jobs/${jobId}/stages/${stageNumber}/retry`,'POST',{revision:draft.revision,settings:{...settings(),reasoning_effort:$('#retry-effort').value}});stageKey='';await poll();}catch(e){showError(e);renderStage();}};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
$('#refresh-trace').onclick=refreshTrace;
$('#task-trace').addEventListener('toggle',()=>{if($('#task-trace').open)refreshTrace();});
loadJobs().then(()=>{const id=new URLSearchParams(location.search).get('job');if(id)chooseJob(id);}).catch(showError);

function renderViewLedger() {
    const data=draft.data,target=$('#stage-content');
    target.append(node('p','逐视图候选台账包含跨视图重复。确认后进入第三阶段，核验匹配组及来源数量，再生成最终统计。'));
    const overview=node('section'),currentSummary=node('section');
    const axisCatalog=data.axis_catalog||{axes:[],per_view:{},axis_count:0,plan_count:0,elevation_view_count:0};
    target.append(node('h3','第二阶段图纸目录统计'),
        node('p',`钢材种类：${data.profile_catalog.length} 种（图纸目录） · 轴线或面名称数：${axisCatalog.axis_count} · 已上传平面视图数：${axisCatalog.plan_count} · 已上传立面视图数：${axisCatalog.elevation_view_count}`),
        node('p',`轴线或面名称：${axisCatalog.axes.map(a=>a.name).join('、')||'尚未提取'}`));
    function summarySection(container,rows,label) {
        const stats=LedgerTools.summarize(rows);container.replaceChildren(node('h3',label),
            node('p',`钢材种类：${stats.profileCount} 种 · ${stats.quantity} 个构件段 · ${stats.unknownQuantity} 个缺少长度（未去重，待核对）`),
            table(['钢材类型','单根长度 mm','数量'],stats.groups.map(g=>[g.spec,g.length_mm==null?'待确认':Number(g.length_mm).toFixed(2),g.member_cnt])));
    }
    function updateSummaries() {
        summarySection(overview,data.view_members,'全部视图台账汇总（包含跨视图重复）');
        if(activeView)summarySection(currentSummary,data.view_members.filter(r=>r.source_view===activeView.id),`${activeView.title} 台账汇总`);
    }
    target.append(overview);
    if(data.recognition_strategy==='grid_agents'){
        const policy=data.grid_configuration?.length_review_policy;
        target.append(node('p',policy?.mode==='economy'
            ?`长度复核采用节省模式：一次核验，仅冲突或缺少依据的记录再仲裁；每视图最多 ${policy.max_calls_per_view} 次长度复核请求（含失败重试）。网格与总代理另计，共享并发上限 ${data.grid_configuration?.request_concurrency??'—'}。`
            :`网格、总代理与长度复核共用请求并发上限 ${data.grid_configuration?.request_concurrency??'—'}；${policy?`每视图长度复核最多 ${policy.max_calls_per_view} 次请求（含失败重试）。`:'此历史任务使用原长度复核流程。'}`));
        target.append(node('p','原始PDF文字及数值可追溯；端点、拼接和尺寸归属仍是模型判断。本地计算正确不代表图示对应一定正确。'));
    }
    target.append(table(['视图','识别状态','网格数','模型请求数','第一阶段数量标准','构件数（组合未展开）','规格数量差异','尚存校验问题'],data.view_analysis.map(a=>[
        data.views.find(v=>v.id===a.view_id)?.title||a.view_id,stateNames[a.status]||a.status,
        a.grid_count??'—',a.model_calls??a.rounds_attempted??'—',a.label_count,a.member_cnt,
        (a.coverage_differences||[]).map(c=>`${c.spec}：${c.label_count}处标签 / ${c.member_cnt}根钢材`).join('；'),
        (a.remaining_checks||[]).map(c=>c.message).join('；')])));
    const choices=data.views.filter(v=>LedgerTools.viewRole(v)==='MEMBERS');
    const panel=node('section');
    panel.addEventListener('input',updateSummaries);
    function show(id) {
        panel.replaceChildren();
        const view=data.views.find(v=>v.id===id);
        activeView=view;
        updateSummaries();panel.append(currentSummary);
        const axes=axisCatalog.per_view[id]||[];
        panel.append(node('p',`当前视图轴线或面名称数：${axes.length}；${axes.join('、')||'尚未提取'}`));
        const titles=tags=>tags.map(tag=>data.views.find(v=>v.id===tag)?.title||tag).join('；');
        panel.append(node('h3',view.title),node('img','',{src:`/api/jobs/${jobId}/views/${view.image_file||id+'.jpg'}`,alt:view.title,style:'max-width:100%'}));
        const analysis=data.view_analysis.find(a=>a.view_id===id);
        if(analysis?.length_review_policy)panel.append(node('p',`本视图长度复核请求：${analysis.length_review_calls??0} / ${analysis.length_review_policy.max_calls_per_view}（含失败重试；网格及总代理另计）`));
        if(analysis?.length_expert){const e=analysis.length_expert;const details=node('details');details.append(node('summary',`长度专家 · ${stateNames[e.status]||e.status} · 目标 ${e.target_count} · 合入 ${e.applied_count??0} · 长度变化 ${e.changed_count??0}`),node('pre',JSON.stringify(e,null,2)));for(const crop of analysis.length_expert_context_images||[])details.append(node('a','打开专家请求的扩展上下文',{href:`/api/jobs/${jobId}/views/${crop.image_file}`,target:'_blank'}));panel.append(details);}
        if(analysis?.length_arbitration){const a=analysis.length_arbitration;const details=node('details');details.append(node('summary',`自动长度仲裁 · ${stateNames[a.status]||a.status} · 有依据 ${a.supported??0} · 推断 ${a.inferred??0} · 未解决 ${a.unresolved??0}`),node('pre',JSON.stringify(a,null,2)));panel.append(details);}
        if(analysis?.grid_tiles?.length){const grids=node('details');grids.append(node('summary',`${analysis.grid_tiles.length} 张识图网格（原图像素，无附加标记）`));for(const tile of analysis.grid_tiles)grids.append(node('a',`${tile.id} · ${tile.width_px}×${tile.height_px}`,{href:`/api/jobs/${jobId}/views/${tile.image_file}`,target:'_blank'}));panel.append(grids);}
        panel.append(table(['规格','所属视图标签','member_cnt','长度 mm','长度依据 / 斜撑直角边 mm','构件位置','未解决问题','校验','长度专家依据','操作'],data.view_members.filter(r=>r.source_view===id).map(r=>[
            input('规格',r.spec,v=>setField(r,'spec',v)),
            LedgerTools.tagEditor(document,r,[...data.views,...axisCatalog.axes.map(a=>({kind:a.kind||'ELEVATION',title:a.name,fixed_label:a.kind==='PLAN'?null:a.name}))],()=>{mark(r);refreshJson();}),
            input('数量',r.member_cnt,v=>setField(r,'member_cnt',v,'number'),'number'),
            r.length_input?.method==='DIAGONAL' ? node('span',r.length_mm==null?'待确认':`${Number(r.length_mm).toFixed(2)} mm`) :
                input('单段长度',r.length_mm,v=>{setField(r,'length_mm',v,'number');if(r.length_input?.method==='DIRECT')r.length_input.direct_mm=r.length_mm;},'number'),
            r.length_input?.method==='DIAGONAL' ? (()=>{const wrap=node('div');wrap.append(
                input('水平边',r.length_input.horizontal_mm,v=>{r.length_input.horizontal_mm=v===''?null:Number(v);mark(r);},'number'),
                input('竖直边',r.length_input.vertical_mm,v=>{r.length_input.vertical_mm=v===''?null:Number(v);mark(r);},'number'),
                node('small',r.length_input.evidence||r.length_formula||''));return wrap;})() : (r.length_input?.evidence||r.length_formula||''),
            input('用平面或立面轴线说明位置',r.location||'',v=>setField(r,'location',v)),
            input('可留空',r.issue,v=>setField(r,'issue',v||null)),r.checks.join('；'),
            r.length_review?(()=>{const d=node('details');d.append(node('summary',`${r.length_review.status} · ${r.length_review.applied?'已合入':'原结果保留'}`),node('pre',JSON.stringify(r.length_arbitration||r.length_review,null,2)));return d;})():'未专项处理',
            button('删除',()=>{data.view_members=data.view_members.filter(x=>x!==r);mark();show(id);},'secondary')
        ])));
        const legacy=data.view_members.filter(r=>r.source_view===id&&(r.legacy_basis||r.basis));
        if(legacy.length){const history=node('details');history.append(node('summary','历史台账依据（保留原文）'),table(['规格','原有依据'],legacy.map(r=>[r.spec,r.legacy_basis||r.basis])));panel.append(history);}
        panel.append(button('添加台账行',()=>{data.view_members.push({id:`${id}-manual-${Date.now()}`,source_view:id,page_id:view.page_id,spec:'',view_tags:[LedgerTools.tagOf(view)],member_cnt:1,length_mm:null,location:'',issue:'人工补充，待核对',checks:[],review_status:'pending_review'});mark();show(id);}));
    }
    target.append(select('来源视图',choices[0]?.id,choices.map(v=>[v.id,v.title]),show),panel);
    if(choices.length)show(choices[0].id);
    else updateSummaries();
}

function renderPreparedCatalog() { const target=$('#stage-content');renderConstructionCatalog(draft.data,target,node('section')); }

function renderConstructionCatalog(data,target,panel) {
    const summary=data.catalog_summary||{},decisions={CONSTRUCTION:'采用',SKIP:'不采用',REVIEW:'待确认'};
    target.append(node('p','第一阶段：AI判断页面用途，只有建造布置图进入定位与300DPI裁切。连接节点、概览等未采用页只保留说明，不生成高清截图。'));
    target.append(node('h3',`${data.profile_catalog.length} 种钢材 · ${(summary.plan_names||[]).length} 个平面 · ${(summary.elevation_names||[]).length} 个立面`));
    target.append(node('p',`钢材种类：${data.profile_catalog.join('、')||'暂无'}；图中出现的不同面名称：${summary.plane_name_count||0} 个。`));
    target.append(node('p',`第一阶段数量标准：已统计 ${summary.counted_views??0}/${data.views.length} 个视图，合计 ${summary.member_count_reference??0} 个构件段/组合；视图间尚未去重，未完成统计不能当作零根。`));
    target.append(table(['文件','PDF页','用途判断','说明','定位问题'],(data.page_decisions||[]).map(p=>[
        p.file,p.page,decisions[p.decision]||p.decision,p.reason,p.issue||''])));
    target.append(table(['平面/立面名称','类型','相关面名称','钢材种类','独立数量标准','各规格数量','数量问题'],data.views.map(v=>[
        LedgerTools.tagOf(v),v.kind==='PLAN'?'平面':'立面',(v.axis_labels||[]).join('、'),(v.steel_types||[]).join('、'),
        v.quantity_reference?.total_count??'未完成',(v.quantity_reference?.spec_counts||[]).map(c=>`${c.spec} × ${c.member_cnt}`).join('；'),v.quantity_reference?.issue||''])));
    function show(id){
        const view=data.views.find(v=>v.id===id);activeView=view;panel.replaceChildren(node('h3',view.title));
        if(view.image_file){const url=`/api/jobs/${jobId}/views/${view.image_file}`;panel.append(node('img','',{src:url,alt:view.title,style:'max-width:100%'}),node('a','打开300DPI高清图',{href:url,target:'_blank'}));}
        if(view.colorized_image_file){const url=`/api/jobs/${jobId}/views/${view.colorized_image_file}`;panel.append(node('a','打开钢材名/轴线名着色图（300DPI）',{href:url,target:'_blank'}));}
        panel.append(input('视图原始标题',view.title,v=>setField(view,'title',v)));
        panel.append(input('平面/立面名称',view.plane_name,v=>setField(view,'plane_name',v)));
        panel.append(select('类型',view.kind,[['PLAN','平面'],['ELEVATION','立面']],v=>setField(view,'kind',v)));
        panel.append(input('此视图中的轴线或面名称（逗号分隔）',(view.axis_labels||[]).join(','),v=>{view.axis_labels=splitRefs(v);view.axes_reviewed=true;mark(view);}));
        panel.append(input('钢材种类（逗号分隔）',(view.steel_types||[]).join(','),v=>{view.steel_types=splitRefs(v);mark(view);}));
        panel.append(input('裁切区域 [左,上,右,下]',JSON.stringify(view.bbox),v=>{try{const box=JSON.parse(v);if(box.length!==4)throw Error('需四个坐标');view.bbox=box;view.region_confirmed=false;mark(view);}catch(e){showError(e);}}));
        const label=node('label'),check=node('input','',{type:'checkbox'});check.checked=view.region_confirmed;
        check.onchange=()=>{view.region_confirmed=check.checked;mark(view);};label.append(check,document.createTextNode('已核对完整标题、标尺、轴线与构件范围'));panel.append(label);
        if(view.localization?.issues?.length)panel.append(node('p',view.localization.issues.join('；')));
    }
    if(data.views.length){target.append(select('有效建造视图',data.views[0].id,data.views.map(v=>[v.id,LedgerTools.tagOf(v)]),show),panel);show(data.views[0].id);}
    else target.append(node('p','没有有效建造视图，不能进入第二阶段。请查看上方未采用或待确认原因；必要时重试第一阶段。'));
}

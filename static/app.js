'use strict';
const $ = selector => document.querySelector(selector);
const stageNames = {1:'视图、轴线与高清裁切',2:'图片识别钢材台账',3:'跨视图匹配与单根统计'};
const stateNames = {queued:'排队中',running:'执行中',awaiting_review:'等待阶段确认',approved:'已确认',stale:'已失效',failed:'失败',interrupted:'已中断，可重试',exporting:'导出中',completed:'已完成'};
let jobId='', job=null, draft=null, stageNumber=1, stageKey='', pages=[], dirty=false, timer=null;
let activeView=null, activeObservation=null, clickMode='', evidenceFocus=null, manualTargets=[], matchSource=null;
function node(tag,text='',attrs={}) { const n=document.createElement(tag); n.textContent=text; for(const [k,v] of Object.entries(attrs)) { if(k==='class') n.className=v; else n.setAttribute(k,v); } return n; }
function showError(error) { $('#error').hidden=false; $('#error').textContent=error.message||String(error); }
async function api(path,method='GET',body=null) { const opts={method}; if(body) { opts.headers={'Content-Type':'application/json'};opts.body=JSON.stringify(body); } const r=await fetch(path,opts);let data;try{data=await r.json();}catch{throw Error('服务器返回了无法解析的响应。');}if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data; }
function settings() { const raw=$('#max-concurrency').value.trim();const parallel=Number(raw);if(raw&&(!Number.isInteger(parallel)||parallel<1))throw Error('同时识别视图数必须是正整数。');return {base_url:$('#base-url').value,api_key:$('#api-key').value,model:$('#model').value,max_concurrency:raw?String(parallel):''}; }
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
function mark(target) { dirty=true; if(target)manualTargets.push({target,page_id:activeView?.page_id||pages[0]?.id,bbox:activeView?.bbox||[0,0,1,1]});$('#save').disabled=false; }
function input(label,value,onChange,type='text') { const wrap=node('label',label);const field=node('input','',{type});field.value=value??'';field.addEventListener('input',()=>onChange(field.value));wrap.append(field);return wrap; }
function select(label,value,options,onChange) { const wrap=node('label',label),field=node('select'); for(const [v,text] of options) { const option=node('option',text,{value:v});option.selected=String(value??'')===String(v);field.append(option); }field.onchange=()=>onChange(field.value);wrap.append(field);return wrap; }
function splitRefs(value) { return value.split(',').map(v=>v.trim()).filter(Boolean); }
function setField(target,key,value,type='text') { target[key]=type==='number'?(value===''?null:Number(value)):value;mark(target); }
function table(headers,rows) { const table=node('table'),head=node('tr');headers.forEach(h=>head.append(node('th',h)));table.append(head);rows.forEach(values=>{const tr=node('tr');values.forEach(value=>{const td=node('td');if(value instanceof Node)td.append(value);else td.textContent=value==null?'待确认':typeof value==='object'?JSON.stringify(value):String(value);tr.append(td);});table.append(tr);});const wrap=node('div','',{class:'table-wrap'});wrap.append(table);return wrap; }
function button(text,action,style='') { const b=node('button',text,{type:'button',class:style});b.onclick=action;return b; }
function refreshJson() { $('#json-editor').value=JSON.stringify(draft.data,null,2); }
function renderInventory() {
    const target=$('#discovery-inventory');target.replaceChildren();target.hidden=stageNumber!==1;
    if(stageNumber!==1)return;
    const inventory=draft.data.discovery_inventory;
    if(!inventory){target.append(node('p','目录尚未生成。'));return;}
    const counts=inventory.view_counts;
    target.append(node('h3',`第一阶段输出：${inventory.profile_count} 种钢材规格 · ${counts.PLAN} 个平面 · ${counts.ELEVATION} 个立面 · ${counts.ISOMETRIC} 个概览`));
    target.append(node('p',inventory.count_basis+' '+inventory.scope));
    target.append(table(['截面规格','组合组件数','原图文字证据'],inventory.profiles.map(profile=>{
        const proof=node('details');proof.append(node('summary',`${profile.evidence.length} 处标注，展开核对`));
        proof.append(table(['文件 / 页','原始文字','证据 ID'],profile.evidence.map(e=>[`${e.file} · 第 ${e.page} 页`,e.text,e.id])));
        return [profile.profile,profile.component_counts.join('、'),proof];
    })));
    if(inventory.unverified_profiles.length)target.append(node('p',`模型提出但缺少规格文字证据（不计入种数）：${inventory.unverified_profiles.join('、')}`));
    for(const [kind,label]of [['PLAN','平面目录'],['ELEVATION','立面目录'],['ISOMETRIC','概览目录']]){
        const views=inventory.views[kind]||[];
        target.append(node('h4',`${label}（${views.length}）`));
        target.append(table(['图纸原始标题','标高 / 轴线','原图文件 / 页','标题证据 ID','视图区域'],views.map(v=>{
            return [v.title,kind==='PLAN'?(v.elevation_m==null?'标高待确认':`EL.${Number(v.elevation_m).toFixed(3)} (${v.elevation_basis})`):kind==='ELEVATION'?(v.fixed_label||'轴线待确认'):'覆盖核对',`${v.file||v.page_id} · 第 ${v.page||1} 页`,(v.evidence_ids||[]).join('、'),v.region_identified?'已识别，待核对':'待补充区域'];
        })));
    }
}
async function loadJobs() { const list=await api('/api/jobs');$('#job-list').replaceChildren(node('option','选择任务',{value:''}));for(const item of list)$('#job-list').append(node('option',`${new Date(item.created_at).toLocaleString()} · ${stateNames[item.state]} · ${item.id.slice(0,8)}`,{value:item.id}));$('#job-list').value=jobId; }
async function chooseJob(id) { if(dirty&&!window.confirm('当前草稿尚未保存，是否离开？'))return;clearTimeout(timer);jobId=id;history.replaceState(null,'',id?'/?job='+encodeURIComponent(id):'/');$('#task-trace').hidden=!id;stageKey='';dirty=false;draft=null;await poll(); }
async function poll() { clearTimeout(timer);try { if(!jobId)return;job=await api(`/api/jobs/${jobId}`);$('#status').textContent=`${stateNames[job.state]||job.state} · ${jobId}`;$('#event-log').replaceChildren(...job.events.map(e=>node('li',`${new Date(e.at).toLocaleTimeString()} ${e.message}`)));if(job.error_detail)showError(Error(job.error_detail));$('#downloads').replaceChildren();for(const [url,label] of [[job.download_url,'下载 Excel'],[job.views_download_url,'下载截图与证据包']])if(url)$('#downloads').append(node('a',label,{href:url,class:'download'}));
$('#stage-tabs').replaceChildren(...job.stages.map(s=>button(`${s.stage}. ${stageNames[s.stage]} · ${stateNames[s.status]}`,()=>openStage(s.stage),s.stage===stageNumber?'active':'')));
const current=job.stages.find(s=>s.stage===Math.min(job.active_stage,3));const key=current?`${current.stage}:${current.revision}:${current.status}`:'';if(key!==stageKey&&!dirty){stageKey=key;if(current)await openStage(current.stage,false);}if(['queued','running','exporting'].includes(job.state))timer=setTimeout(poll,1800);await loadJobs(); }catch(e){showError(e);timer=setTimeout(poll,4000);} }
async function openStage(n,checkDirty=true) { if(checkDirty&&dirty&&!window.confirm('当前修改尚未保存，是否切换阶段？'))return;try{draft=await api(`/api/jobs/${jobId}/stages/${n}`);stageNumber=n;pages=await api(`/api/jobs/${jobId}/pages`);matchSource=n===3?(await api(`/api/jobs/${jobId}/stages/2`)).data:null;dirty=false;manualTargets=[];activeView=null;activeObservation=null;$('#error').hidden=true;renderStage();}catch(e){showError(e);} }
function renderStage() { $('#review').hidden=false;$('#stage-title').textContent=`阶段 ${stageNumber} · ${stageNames[stageNumber]}`;$('#stage-state').textContent=`版本 ${draft.revision} · ${stateNames[draft.status]}`;$('#stage-content').replaceChildren();$('#warnings').replaceChildren();const data=draft.data;const editable=['awaiting_review','approved','failed','interrupted'].includes(draft.status)&&!['queued','running','exporting'].includes(job.state);$('#save').disabled=!editable;$('#approve').disabled=draft.status!=='awaiting_review';$('#retry').disabled=!(job.active_stage===stageNumber||job.active_stage===4&&stageNumber===3)||!['failed','interrupted','awaiting_review'].includes(job.state);$('#approve').textContent=stageNumber===3?'确认并导出单根统计':'确认并进入下一阶段';
renderInventory();if(!data.views&&stageNumber<=2){$('#stage-content').append(node('p','该阶段尚未生成草稿。执行失败时可修正 API 设置后重试。'));refreshJson();renderHistory();return;}
const warnings=stageNumber===1?data.warnings||[]:stageNumber===2?[...(data.warnings||[]),...(data.frame_issues||[]),...(data.candidates||[]).filter(c=>!c.confirmed).map(c=>`${c.observation_id}：${c.issues.join('；')}`)]:(data.exceptions||[]).map(e=>`${e.observation_id}：${e.issues.join('；')}`);
if(warnings.length>12){const details=node('details');details.append(node('summary',`${warnings.length} 项待确认，展开核对`),...warnings.map(w=>node('p',w)));$('#warnings').append(details);}else warnings.forEach(w=>$('#warnings').append(node('p',w)));
if(stageNumber===1&&data.workflow==='image_first'){renderPreparedCatalog();}else if(stageNumber===1&&data.analysis_pending){$('#stage-content').append(node('p','第一阶段仅生成规格和视图目录，不调用 AI。确认后，第二阶段才定位视图区域、识别候选构件首尾和尺寸关系。'));}else if(stageNumber===2&&data.recognition_mode==='view_ledger'){renderViewLedger();}else if(stageNumber<=2){renderFrame();renderObservations();}else if(data.reconciliation_mode==='group_ledger'){renderGroupReconciliation();}else renderMatches();refreshJson();renderHistory(); }
function renderHistory() { const target=$('#history');target.replaceChildren(node('h3','修改与确认记录'));for(const snap of draft.snapshots||[]){const row=node('p',`${snap.at} · v${snap.revision} · ${snap.action} · ${snap.reason} `);row.append(button('载入此草稿',()=>{if(!snap.data||!Object.keys(snap.data).length)return;draft.data=structuredClone(snap.data);mark();renderStage();dirty=true;}));target.append(row);} }
function renderFrame() { const data=draft.data,details=node('details','',{class:'frame-editor'});details.append(node('summary','项目坐标系、尺寸链与标高（毫米）'));const frame=data.frame;details.append(input('标高原点 EL（m）',frame.z_origin_m,v=>{frame.z_origin_m=Number(v);mark(frame);},'number'));
const axisRows=frame.axes.map(a=>[select('方向',a.direction,[['X','X'],['Y','Y']],v=>setField(a,'direction',v)),input('原始轴名',a.label,v=>setField(a,'label',v)),input('坐标 mm（可留空由尺寸链求解）',a.value_mm,v=>setField(a,'value_mm',v,'number'),'number'),input('证据 ID',a.evidence_ids.join(','),v=>{a.evidence_ids=splitRefs(v);mark(a);}),button('删除',()=>{frame.axes=frame.axes.filter(x=>x!==a);mark();renderStage();})]);details.append(table(['方向','轴线','锚点','证据','操作'],axisRows));details.append(button('添加轴线',()=>{frame.axes.push({direction:'X',label:'新轴线',value_mm:null,evidence_ids:[]});mark();renderStage();}));
details.append(table(['方向','起轴','终轴','有符号尺寸 mm','证据','操作'],frame.dimensions.map(d=>[select('方向',d.direction,[['X','X'],['Y','Y']],v=>setField(d,'direction',v)),input('起轴',d.from_label,v=>setField(d,'from_label',v)),input('终轴',d.to_label,v=>setField(d,'to_label',v)),input('尺寸',d.distance_mm,v=>setField(d,'distance_mm',v,'number'),'number'),input('证据 ID',d.evidence_ids.join(','),v=>{d.evidence_ids=splitRefs(v);mark(d);}),button('删除',()=>{frame.dimensions=frame.dimensions.filter(x=>x!==d);mark();renderStage();})])));details.append(button('添加尺寸链',()=>{const d={direction:'X',from_label:'',to_label:'',distance_mm:0,evidence_ids:[]};frame.dimensions.push(d);mark(d);renderStage();}));details.append(node('p','尺寸为正表示沿全局正方向。偏移必须有图示尺寸或人工核对依据；标高原点不等于构件真实底端。'));details.append(table(['所有标高参考'],frame.levels.map(l=>[l])));$('#stage-content').append(details);
if(stageNumber===2)$('#stage-content').append(table(['规格','候选名义长度 mm','观察（尚未去重）'],data.length_catalog.map(c=>[c.profile,c.length_mm,c.observation_ids])));
else $('#stage-content').append(node('p',`规格目录：${data.profile_catalog.join('、')||'未识别'}`)); }
function renderObservations() { const data=draft.data,layout=node('div','',{class:'review-layout'}),left=node('section'),right=node('section');const viewSelect=select('视图',data.views[0]?.id,data.views.map(v=>[v.id,`${v.title} · ${data.observations.filter(o=>o.view_id===v.id).length} 个观察`]),id=>selectView(id));left.append(viewSelect);const viewer=node('div','',{id:'viewer',class:'viewer'}),viewInfo=node('div','',{id:'view-info'});left.append(viewer,viewInfo);const evidence=node('details','',{class:'evidence-panel'});evidence.append(node('summary','查询图纸文字与矢量证据'));const query=node('input','',{placeholder:'尺寸、规格或证据 ID'}),result=node('div','',{id:'evidence-results'});evidence.append(query,button('查询',()=>loadEvidence(query.value)),result);left.append(evidence);right.append(node('div','',{id:'observation-list'}),node('div','',{id:'observation-editor'}));layout.append(left,right);$('#stage-content').append(layout);if(stageNumber===1)left.append(button('添加视图',()=>{const page=pages[0];if(!page)return;const view={id:`manual-view-${Date.now()}`,page_id:page.id,title:'新视图',kind:'PLAN',bbox:[0,0,1,1],title_bbox:[0,0,.1,.1],visible_axes:['X','Y'],fixed_axis:'Z',fixed_label:null,elevation_m:null,elevation_basis:'TOS',rotation_deg:0,mirrored:false,axis_marks:[],evidence_ids:[]};data.views.push(view);mark(view);renderStage();selectView(view.id);}));if(data.views.length)selectView(data.views[0].id); }
function selectView(id) { activeView=draft.data.views.find(v=>v.id===id);if(!activeView)return;activeObservation=null;evidenceFocus=null;renderViewer();renderViewInfo();const target=$('#observation-list');target.replaceChildren(node('h3','该视图的候选构件'));if(activeView.kind==='ISOMETRIC'){target.append(node('p','概览仅用于覆盖核对，不计数。'));$('#observation-editor').replaceChildren();return;}for(const o of draft.data.observations.filter(o=>o.view_id===id)){const c=draft.data.candidates?.find(c=>c.observation_id===o.id);target.append(button(`${o.id.split('-').at(-1)} · ${o.profile||'未知'} · ${c?(c.confirmed?c.length_mm+' mm':'待确认'):o.role}`,()=>selectObservation(o.id),'observation-button'));}target.append(button('添加候选构件',()=>{const ref=()=>({axis_label:null,value_mm:null,offset_mm:0,towards:null,evidence_ids:[]});const point=()=>({x:ref(),y:ref(),z:{value:activeView.elevation_m,unit:'m',basis:activeView.elevation_basis||'REFERENCE',reference_basis:activeView.elevation_basis||'REFERENCE',offset_mm:0,evidence_ids:[]}});const boundary=()=>({kind:'UNKNOWN',evidence_ids:[],note:''});const o={id:`${activeView.id}-manual-${Date.now()}`,view_id:id,profile:'',material:'UNKNOWN',role:'',image_start:null,image_end:null,start:point(),end:point(),start_boundary:boundary(),end_boundary:boundary(),evidence_ids:[],assembly_identity:'',segment_identity:'',reference_line:'AXIS',deferred:false,notes:''};draft.data.observations.push(o);mark(o);selectView(id);selectObservation(o.id);}));const first=draft.data.observations.find(o=>o.view_id===id);if(first)selectObservation(first.id);else $('#observation-editor').replaceChildren(); }
function renderViewInfo() { const target=$('#view-info');target.replaceChildren();const view=activeView;if(stageNumber===1){target.append(input('视图标题',view.title,v=>setField(view,'title',v)));target.append(select('原始 PDF 页',view.page_id,pages.map(p=>[p.id,p.file]),v=>{view.page_id=v;mark(view);renderViewer();}));target.append(select('类型',view.kind,[['PLAN','平面'],['ELEVATION','立面'],['ISOMETRIC','概览']],v=>{view.kind=v;mark(view);}));for(const name of ['bbox','title_bbox','visible_axes'])target.append(input(name==='bbox'?'视图区域 [左,上,右,下]':name==='title_bbox'?'标题区域':'显示方向，例如 X,Y',name==='visible_axes'?view[name].join(','):JSON.stringify(view[name]),v=>{try{view[name]=name==='visible_axes'?v.split(',').map(s=>s.trim().toUpperCase()):JSON.parse(v);mark(view);renderViewer();}catch(e){showError(e);}}));target.append(button('图上设置视图左上角',()=>{clickMode='bbox-start';}),button('图上设置视图右下角',()=>{clickMode='bbox-end';}));target.append(button('删除视图与所属观察',()=>{draft.data.views=draft.data.views.filter(v=>v.id!==view.id);draft.data.observations=draft.data.observations.filter(o=>o.view_id!==view.id);mark();renderStage();},'secondary'));}
target.append(select('固定方向',view.fixed_axis,[['','未确定'],['X','X'],['Y','Y'],['Z','Z']],v=>{view.fixed_axis=v||null;mark(view);}));target.append(input('固定轴线名称',view.fixed_label,v=>{view.fixed_label=v||null;mark(view);}));target.append(input('平面标高 EL（m）',view.elevation_m,v=>setField(view,'elevation_m',v,'number'),'number'));target.append(input('标高基准 TOS/BOB',view.elevation_basis,v=>setField(view,'elevation_basis',v)));target.append(input('视图旋转角度',view.rotation_deg,v=>setField(view,'rotation_deg',v,'number'),'number'));target.append(select('镜像',view.mirrored?'true':'false',[['false','否'],['true','是']],v=>{view.mirrored=v==='true';mark(view);})); }
function renderViewer() { const target=$('#viewer');if(!target||!activeView)return;target.replaceChildren();const img=node('img','',{src:`/api/jobs/${jobId}/views/${activeView.page_id}.jpg`,alt:activeView.title});const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 1000 1000');svg.setAttribute('preserveAspectRatio','none');
const rect=(box,color)=>{const r=document.createElementNS(svg.namespaceURI,'rect');for(const [k,v]of Object.entries({x:box[0]*1000,y:box[1]*1000,width:(box[2]-box[0])*1000,height:(box[3]-box[1])*1000,fill:'none',stroke:color,'stroke-width':2}))r.setAttribute(k,v);svg.append(r);};rect(activeView.bbox,'#087e8b');
for(const mark of activeView.axis_marks||[]){const t=document.createElementNS(svg.namespaceURI,'text');t.setAttribute('x',mark.point[0]*1000);t.setAttribute('y',mark.point[1]*1000);t.setAttribute('fill','#b87812');t.setAttribute('font-size','12');t.textContent=mark.label;svg.append(t);}
for(const o of draft.data.observations.filter(o=>o.view_id===activeView.id)){if(!o.image_start||!o.image_end)continue;const line=document.createElementNS(svg.namespaceURI,'line');for(const [k,v]of Object.entries({x1:o.image_start[0]*1000,y1:o.image_start[1]*1000,x2:o.image_end[0]*1000,y2:o.image_end[1]*1000,stroke:o===activeObservation?'#e34234':'#27a1ad','stroke-width':o===activeObservation?4:1.5}))line.setAttribute(k,v);svg.append(line);if(o===activeObservation)for(const [label,p]of [['首',o.image_start],['尾',o.image_end]]){const t=document.createElementNS(svg.namespaceURI,'text');t.setAttribute('x',p[0]*1000+4);t.setAttribute('y',p[1]*1000-4);t.setAttribute('fill','#e34234');t.setAttribute('font-size','14');t.textContent=label;svg.append(t);}}
if(evidenceFocus)rect(evidenceFocus.bbox,'#d98000');target.append(img,svg);target.onclick=event=>{const box=img.getBoundingClientRect(),p=[(event.clientX-box.left)/box.width,(event.clientY-box.top)/box.height];if(p.some(v=>v<0||v>1))return;if(clickMode.startsWith('bbox-')){const start=clickMode==='bbox-start';activeView.bbox[start?0:2]=p[0];activeView.bbox[start?1:3]=p[1];mark(activeView);renderViewInfo();}else if(activeObservation&&clickMode){activeObservation[clickMode]=p;mark(activeObservation);}else return;clickMode='';renderViewer();}; }
function selectObservation(id) { activeObservation=draft.data.observations.find(o=>o.id===id);renderViewer();const target=$('#observation-editor');target.replaceChildren();const o=activeObservation;target.append(node('h3',o.id));for(const [key,label]of [['profile','图示规格（保留 2LF）'],['material','材质'],['role','构件角色'],['assembly_identity','独立组合身份（通常留空）'],['segment_identity','分段身份（通常留空）'],['reference_line','定位参考线'],['notes','说明']])target.append(input(label,o[key],v=>setField(o,key,v)));target.append(input('构件证据 ID（逗号分隔）',o.evidence_ids.join(','),v=>{o.evidence_ids=splitRefs(v);mark(o);}));target.append(button('复制为另一个分段',()=>{const copy=structuredClone(o);copy.id=`${activeView.id}-manual-${Date.now()}`;draft.data.observations.push(copy);mark(copy);selectView(activeView.id);selectObservation(copy.id);}));target.append(button('在图上指定首端',()=>{clickMode='image_start';}),button('在图上指定尾端',()=>{clickMode='image_end';}));
for(const [name,label]of [['start','首端'],['end','尾端']]){const fieldset=node('fieldset');fieldset.append(node('legend',`${label}：语义坐标与基准`));for(const axis of ['x','y']){const ref=o[name][axis];fieldset.append(input(`${axis.toUpperCase()} 原始轴名`,ref.axis_label,v=>{ref.axis_label=v||null;if(v)ref.value_mm=null;mark(ref);}));fieldset.append(input(`${axis.toUpperCase()} 直接坐标 mm（与轴名二选一）`,ref.value_mm,v=>{ref.value_mm=v===''?null:Number(v);if(v)ref.axis_label=null;mark(ref);},'number'));fieldset.append(input(`${axis.toUpperCase()} 偏移 mm`,ref.offset_mm,v=>setField(ref,'offset_mm',v,'number'),'number'));fieldset.append(input(`${axis.toUpperCase()} 偏向轴名（可留空）`,ref.towards,v=>{ref.towards=v||null;mark(ref);}));fieldset.append(input(`${axis.toUpperCase()} 尺寸证据 ID`,ref.evidence_ids.join(','),v=>{ref.evidence_ids=splitRefs(v);mark(ref);}));}const z=o[name].z;fieldset.append(input('EL 数值',z.value,v=>setField(z,'value',v,'number'),'number'));fieldset.append(select('EL 单位',z.unit,[['m','米'],['mm','毫米']],v=>setField(z,'unit',v)));const bases=['TOS','BOB','CENTERLINE','REFERENCE'].map(v=>[v,v]);fieldset.append(select('图纸标高基准',z.basis,bases,v=>setField(z,'basis',v)));fieldset.append(input('已知标高偏移 mm',z.offset_mm,v=>setField(z,'offset_mm',v,'number'),'number'));fieldset.append(select('偏移后的定位基准',z.reference_basis,bases,v=>setField(z,'reference_basis',v)));fieldset.append(input('标高证据 ID',z.evidence_ids.join(','),v=>{z.evidence_ids=splitRefs(v);mark(z);}));const boundary=o[name+'_boundary'];fieldset.append(select('首尾边界依据',boundary.kind,['UNKNOWN','END','SPLICE','PROFILE_CHANGE','CONTINUOUS'].map(v=>[v,v]),v=>setField(boundary,'kind',v)));fieldset.append(input('端部依据说明',boundary.note,v=>setField(boundary,'note',v)));fieldset.append(input('端部证据 ID',boundary.evidence_ids.join(','),v=>{boundary.evidence_ids=splitRefs(v);mark(boundary);}));fieldset.append(node('small',`原有证据：${[...z.evidence_ids,...boundary.evidence_ids].join('、')||'无'}`));target.append(fieldset);}
target.append(select('保留待确认',o.deferred?'true':'false',[['false','否'],['true','是']],v=>{o.deferred=v==='true';mark(o);}));target.append(button('删除该观察',()=>{draft.data.observations=draft.data.observations.filter(x=>x!==o);mark();selectView(activeView.id);},'secondary'));const candidate=draft.data.candidates?.find(c=>c.observation_id===o.id);if(candidate)target.append(node('p',`长度 ${candidate.length_mm??'待确认'} mm；${candidate.formula}；${candidate.issues.join('；')}`)); }
async function loadEvidence(q) { try {const result=await api(`/api/jobs/${jobId}/evidence?page_id=${encodeURIComponent(activeView.page_id)}&view_id=${encodeURIComponent(activeView.id)}&q=${encodeURIComponent(q)}`);const target=$('#evidence-results');target.replaceChildren(node('p',`显示 ${result.items.length} / ${result.total} 条；点击定位`));for(const item of result.items)target.append(button(`${item.id} · ${item.text||'矢量线段'}`,()=>{evidenceFocus=item;renderViewer();navigator.clipboard?.writeText(item.id).catch(()=>{});},'evidence-item'));}catch(e){showError(e);} }
function comparisonFigure(id) {
    const observation=matchSource?.observations.find(o=>o.id===id);
    const view=matchSource?.views.find(v=>v.id===observation?.view_id);
    const candidate=matchSource?.candidates.find(c=>c.observation_id===id);
    const figure=node('figure');
    if(view) {
        const preview=node('div','',{class:'viewer'});
        preview.append(node('img','',{src:`/api/jobs/${jobId}/views/${view.id}.jpg`,alt:view.title}));
        if(observation.image_start&&observation.image_end) {
            const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
            svg.setAttribute('viewBox','0 0 1000 1000');svg.setAttribute('preserveAspectRatio','none');
            const transform=p=>[(p[0]-view.bbox[0])/(view.bbox[2]-view.bbox[0])*1000,(p[1]-view.bbox[1])/(view.bbox[3]-view.bbox[1])*1000];
            const a=transform(observation.image_start),b=transform(observation.image_end);
            const line=document.createElementNS(svg.namespaceURI,'line');
            for(const [key,value]of Object.entries({x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:'#e34234','stroke-width':4}))line.setAttribute(key,value);
            svg.append(line);preview.append(svg);
        }
        figure.append(preview,node('figcaption',view.title));
    }
    figure.append(node('small',id));
    if(candidate)figure.append(node('p',`${candidate.profile} · ${candidate.length_mm} mm；${JSON.stringify(candidate.start_mm)} → ${JSON.stringify(candidate.end_mm)}`));
    return figure;
}
function renderMatches() {
    const data=draft.data,target=$('#stage-content');
    target.append(table(['规格','材质','名义长度 mm','单根数量','总长度 mm'],data.summary.map(s=>[s.profile,s.material,s.length_mm,s.quantity,s.total_length_mm])));
    target.append(node('h3',`已生成 ${data.members.length} 根；${data.exceptions.length} 条观察待确认`));
    for(const match of [...data.matches,...data.conflicts]) {
        const pairKey=[match.left,match.right].sort().join('|');
        const card=node('article','',{class:'match-card'});
        card.append(node('h3',`${match.kind} · ${match.left} ↔ ${match.right}`),node('p',match.reason));
        const comparison=node('div','',{class:'comparison'});
        comparison.append(comparisonFigure(match.left),comparisonFigure(match.right));
        card.append(comparison);
        const current=data.decisions.find(d=>[d.left,d.right].sort().join('|')===pairKey);
        const reason=node('input','',{placeholder:'匹配或独立分段的核对依据'});
        reason.value=current?.reason||'';
        const choice=select('匹配决定',current?.action||'',[['','自动判断 / 待处理'],['SAME','确认同一根'],['DISTINCT','保留独立构件'],['DEFER','保留待确认']],action=>{
            data.decisions=data.decisions.filter(d=>[d.left,d.right].sort().join('|')!==pairKey);
            if(action)data.decisions.push({left:match.left,right:match.right,action,reason:reason.value});
            mark();
        });
        reason.oninput=()=>{
            const decision=data.decisions.find(d=>[d.left,d.right].sort().join('|')===pairKey);
            if(decision){decision.reason=reason.value;mark();}
        };
        card.append(choice,reason);target.append(card);
    }
}
$('#files').onchange=()=>{$('#upload-error').hidden=true;$('#file-list').replaceChildren(...[...$('#files').files].map(f=>node('p',`${f.name} · ${(f.size/1048576).toFixed(1)} MB`)));};
$('#upload-form').onsubmit=async event=>{
    event.preventDefault();const start=$('#submit'),error=$('#upload-error');error.hidden=true;
    try{
        const files=[...$('#files').files];
        if(!files.length)throw Error('请先点击“选择 PDF 图纸”并选中文件，再开始第一阶段。刷新页面后需要重新选择文件。');
        if(files.some(file=>!file.name.toLowerCase().endsWith('.pdf')))throw Error('仅支持 PDF 文件，请重新选择图纸。');
        start.disabled=true;start.textContent='正在上传图纸…';
        const form=new FormData();for(const file of files)form.append('files',file);
        for(const [key,value]of Object.entries(settings()))form.append(key,value);
        const response=await fetch('/api/jobs',{method:'POST',body:form});
        const payload=await response.json();if(!response.ok)throw Error(payload.detail);
        dirty=false;await chooseJob(payload.id);
    }catch(e){error.hidden=false;error.textContent=e.message||String(e);}
    finally{start.disabled=false;start.textContent='开始第一阶段';}
};
$('#job-list').onchange=()=>chooseJob($('#job-list').value);
$('#apply-json').onclick=()=>{try{draft.data=JSON.parse($('#json-editor').value);manualTargets=[];mark();renderStage();dirty=true;}catch(e){showError(e);}};
$('#save').onclick=async()=>{try{const reason=$('#edit-reason').value.trim();if(!reason)throw Error('请填写修改依据。');if(stageNumber<=2){const seen=new Set();for(const entry of manualTargets){if(!entry.page_id||seen.has(entry.target))continue;seen.add(entry.target);const id=`manual:${crypto.randomUUID()}`;draft.data.manual_evidence.push({id,page_id:entry.page_id,kind:'MANUAL',bbox:entry.bbox,text:reason,points:[],style:{}});if(entry.target===draft.data.frame)entry.target.origin_evidence_ids.push(id);else if(Array.isArray(entry.target.evidence_ids))entry.target.evidence_ids.push(id);}}
draft=await api(`/api/jobs/${jobId}/stages/${stageNumber}`,'PATCH',{revision:draft.revision,data:draft.data,reason});dirty=false;manualTargets=[];stageKey='';$('#edit-reason').value='';renderStage();await poll();}catch(e){showError(e);}};
$('#approve').onclick=async()=>{try{if(dirty)throw Error('请先保存修改，再确认阶段。');$('#approve').disabled=true;await api(`/api/jobs/${jobId}/stages/${stageNumber}/approve`,'POST',{revision:draft.revision,acknowledge_pending:$('#pending').checked,settings:stageNumber===1?settings():{}});$('#pending').checked=false;stageKey='';await poll();}catch(e){showError(e);$('#approve').disabled=false;}};
$('#retry').onclick=async()=>{try{if(dirty)throw Error('请先保存当前修改。');await api(`/api/jobs/${jobId}/stages/${stageNumber}/retry`,'POST',{revision:draft.revision,settings:settings()});stageKey='';await poll();}catch(e){showError(e);}};
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
            table(['钢材类型','单根长度 mm','数量'],stats.groups.map(g=>[g.spec,g.length_mm,g.member_cnt])));
    }
    function updateSummaries() {
        summarySection(overview,data.view_members,'全部视图台账汇总（包含跨视图重复）');
        if(activeView)summarySection(currentSummary,data.view_members.filter(r=>r.source_view===activeView.id),`${activeView.title} 台账汇总`);
    }
    target.append(overview);
    target.append(table(['视图','识别状态','标签数','构件段数（未去重）','规格覆盖差异'],data.view_analysis.map(a=>[
        data.views.find(v=>v.id===a.view_id)?.title||a.view_id,a.status,a.label_count,a.member_cnt,
        (a.coverage_differences||[]).map(c=>`${c.spec}：${c.label_count}处标签 / ${c.member_cnt}个候选段`).join('；')])));
    const choices=data.views.filter(v=>v.kind!=='ISOMETRIC');
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
        panel.append(table(['规格','所属视图标签','member_cnt','长度 mm','构件位置','未解决问题','校验','操作'],data.view_members.filter(r=>r.source_view===id).map(r=>[
            input('规格',r.spec,v=>setField(r,'spec',v)),
            LedgerTools.tagEditor(document,r,[...data.views,...axisCatalog.axes.map(a=>({kind:a.kind||'ELEVATION',title:a.name,fixed_label:a.kind==='PLAN'?null:a.name}))],()=>{mark(r);refreshJson();}),
            input('数量',r.member_cnt,v=>setField(r,'member_cnt',v,'number'),'number'),
            input('单段长度',r.length_mm,v=>setField(r,'length_mm',v,'number'),'number'),
            input('用平面或立面轴线说明位置',r.location||'',v=>setField(r,'location',v)),
            input('可留空',r.issue,v=>setField(r,'issue',v||null)),r.checks.join('；'),
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

function renderPreparedCatalog() {
    const data=draft.data,target=$('#stage-content'),panel=node('section');
    target.append(node('p','第一阶段请核对高清裁切、标题及轴线或面名称。名称表示Alignment立面或EL标高平面，图中的线是这些面在当前视图中的示意。第二阶段使用确认图片、目录、提示词和当前视图的辅助证据。'));
    const catalog=data.axis_catalog||{axis_count:0,per_view:{}};
    target.append(node('p',`共 ${data.views.length} 个视图，${catalog.axis_count} 个轴线或面名称`));
    function show(id) {
        const view=data.views.find(v=>v.id===id);activeView=view;panel.replaceChildren();
        const image=view.image_file||view.id+'.jpg';
        panel.append(node('h3',view.title),node('img','',{src:`/api/jobs/${jobId}/views/${image}`,alt:view.title,style:'max-width:100%'}),
                     node('a','打开300DPI原图',{href:`/api/jobs/${jobId}/views/${image}`,target:'_blank'}));
        if(view.detail_files?.length){const details=node('details');details.append(node('summary',`同视图局部放大图（${view.detail_files.length} 张，识别时与全图一起发送）`));for(const file of view.detail_files)details.append(node('img','',{src:`/api/jobs/${jobId}/views/${file}`,alt:'同一视图局部',style:'max-width:100%'}));panel.append(details);}
        panel.append(input('原始视图标题',view.title,value=>setField(view,'title',value)));
        if(view.kind==='ELEVATION')panel.append(input('此立面的Alignment名称',view.fixed_label,value=>setField(view,'fixed_label',value)));
        panel.append(input('轴线或面名称',(view.axis_labels||[]).join(','),value=>{
            view.axis_labels=value.split(/[,，;；\n]+/).map(t=>t.trim()).filter(Boolean);view.axes_reviewed=true;mark(view);
        }));
        panel.append(node('small','多个名称用逗号分隔；包括Alignment立面名称与EL标高平面名称。目录中出现名称，不代表已上传对应面的独立图纸。'));
        panel.append(input('裁切区域 [左,上,右,下]，整页归一化',JSON.stringify(view.bbox),value=>{
            try{const box=JSON.parse(value);if(box.length!==4)throw Error('需要四个坐标');view.bbox=box;view.region_confirmed=false;mark(view);}catch{}
        }));
        const label=node('label'),check=node('input','',{type:'checkbox'});check.checked=view.region_confirmed;
        check.onchange=()=>{view.region_confirmed=check.checked;mark(view);};label.append(check,document.createTextNode('裁切区域已核对，可用于第二阶段识别'));panel.append(label);
        const diagnostic=node('details');diagnostic.append(node('summary','查看裁切检查依据'),node('pre',JSON.stringify(view.localization,null,2)));panel.append(diagnostic);
    }
    target.append(select('图纸视图',data.views[0]?.id,data.views.map(v=>[v.id,v.title]),show),panel);
    if(data.views.length)show(data.views[0].id);
}

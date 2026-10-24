'use strict';
// Transport/presentation adapter for the original page. No recognition or
// measurement logic belongs here; fixed-label outputs are displayed verbatim.
const NativeWeb = (() => {
  const ids = new Set(), jobs = new Map();let controls;
  async function request(path, method='GET', body=null) {
    const response=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
    let value;try{value=await response.json();}catch{throw Error('服务器响应无法解析。');}
    if(!response.ok){const error=Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));error.status=response.status;throw error;}
    return value;
  }
  function file(id,directory,name){return `/api/native/runs/${id}/files/${directory}/${name}`;}
  function meta(job,n){
    if(job.stage_states?.[n])return {stage:n,...job.stage_states[n]};
    const count=job.stages.filter(s=>s.stage===n).length,expected=job.mode==='PDF'&&n<3?job.inputs.length:1;
    return {stage:n,revision:1,status:job.mode==='MATCH'&&n<3?'approved':count>=expected?(job.state==='completed'||job.stage>n?'approved':'awaiting_review'):job.stage===n||count?(job.state==='interrupted'||job.state==='failed'?job.state:'running'):'queued'};
  }
  async function list(){const rows=await request('/api/native/runs');rows.forEach(row=>{ids.add(row.id);jobs.set(row.id,row);});if(controls===undefined){const capability=await optional('/api/native/capabilities');controls=!!capability?.stage_controls;$('#submit').disabled=!controls;$('#workflow-availability').textContent=controls?'':'结果展示已接回原页面；阶段服务更新前暂不创建新任务，以免暂停等设置被旧服务忽略。当前运行不受影响。';}return rows.map(row=>({...row,backend:'native'}));}
  async function get(id){
    const raw=await request(`/api/native/runs/${id}`);jobs.set(id,raw);ids.add(id);
    if(raw.backend==='native'){controls=true;$('#submit').disabled=false;$('#workflow-availability').textContent='';return raw;}
    const logs=await request(`/api/native/runs/${id}/logs`),third=raw.stages.filter(e=>e.stage===3).at(-1);
    return {...raw,backend:'native',active_stage:raw.stage||1,stages:[1,2,3].map(n=>meta(raw,n)),
      events:logs.items.map(e=>({at:e.at,message:e.message||e.error||e.event})),error_detail:raw.error,
      download_url:raw.state==='completed'&&third?file(id,third.directory,'stage_three.xlsx'):null,
      views_download_url:null};
  }
  async function optional(path){try{return await request(path);}catch(error){if(error.status===404)return null;throw error;}}
  async function stage(id,n){
    const delivered=controls===false?null:await optional(`/api/native/runs/${id}/stages/${n}`);
    if(delivered)return delivered;
    // v0.4 task compatibility during an in-flight server upgrade. Read saved
    // files through the existing API without restarting or changing the task.
    const job=jobs.get(id),data={workflow:'native_labels',views:[],view_members:[],profile_catalog:[],reports:[],warnings:[],controls_available:false};
    if(n===3){const directory=job.stages.filter(e=>e.stage===3).at(-1)?.directory||'stage_three';Object.assign(data,await optional(file(id,directory,'stage_three.json'))||{});return {...meta(job,n),data};}
    if(job.mode==='MATCH'){
      const views=await optional(file(id,'stage_three','source_views.json'))||[];
      data.views=views.map(v=>({...v,id:v.uid,axis_labels:(v.axes||[]).map(a=>a.name),image_url:file(id,'stage_three',v.image_file)}));
      data.view_members=(await optional(file(id,'stage_three','observations.json'))||[]).map(r=>({...r,source_view:r.view_uid}));
    }else{
      const ones=job.stages.filter(e=>e.stage===1),twos=job.stages.filter(e=>e.stage===2);
      for(let i=0;i<ones.length;i++){
        const one=ones[i],bundle=await optional(file(id,one.directory,'stage_one_facts.json'));if(!bundle)continue;
        const two=twos.find((e,k)=>(e.input_index??k)===(one.input_index??i));
        const twoDirectory=two?.directory||`pdf${String((one.input_index??i)+1).padStart(3,'0')}/stage_two`;
        const lengths=await optional(file(id,twoDirectory,'stage_two.json'))||{};
        data.views.push(...bundle.views.map(v=>({...v,id:`${bundle.source_sha256}:${v.view_id}`,axis_labels:(v.axes||[]).map(a=>a.name),image_url:file(id,one.directory,v.image_file),state:(lengths.views||[]).find(x=>x.view_id===v.view_id)?.state||'not_started'})));
        data.view_members.push(...(lengths.members||[]).map(r=>({...r,source_view:`${bundle.source_sha256}:${r.view_id}`})));
      }
    }
    data.profile_catalog=[...new Set(data.views.flatMap(v=>v.labels.map(l=>l.spec)))].sort();
    data.axis_names=[...new Set(data.views.flatMap(v=>[v.plane_name,...v.axis_labels]))].sort();
    data.summary={view_count:data.views.length,label_count:data.views.reduce((s,v)=>s+v.labels.length,0),steel_type_count:data.profile_catalog.length,axis_count:data.axis_names.length};
    return {...meta(job,n),data};
  }
  async function action(id,n,kind,body){return request(`/api/native/runs/${id}/stages/${n}/${kind}`,'POST',body);}
  function render(data,n){
    const target=$('#stage-content');$('#save').disabled=true;$('#save').hidden=true;$('#json-editor').readOnly=true;$('#apply-json').disabled=true;$('#apply-json').hidden=true;$('#edit-reason').disabled=true;$('#edit-reason').parentElement.hidden=true;$('#pending').parentElement.hidden=true;
    target.append(node('p','固定标签台账按原始识别结果展示。本次仅恢复阶段操作和结果查看，台账编辑暂不开放。'));
    if(data.controls_available===false){$('#approve').disabled=true;$('#retry').disabled=true;target.append(node('p','当前任务仍由升级前服务执行，结果可直接查看。服务更新后恢复阶段确认与重跑操作；不会打断正在运行的 API。'));}
    for(const report of data.reports||[])target.append(node('a',report.title,{href:report.url,target:'_blank',class:'download'}));
    if(n===3){
      if(!Array.isArray(data.entities)){target.append(node('p','第三阶段尚未生成可用台账，请先完成第二阶段。'));return;}
      const summary=data.summary||{},single=LedgerTools.summarize(data.entities.map(e=>({spec:e.profile,member_cnt:e.member_cnt*e.component_multiplier,length_mm:e.length_mm})));
      target.append(node('h3',`最终单根台账：${single.quantity} 根 · ${single.profileCount} 种钢材`),table(['规格','长度 mm','单根数量'],single.groups.map(g=>[g.spec,g.length_mm,g.member_cnt])));
      target.append(node('h3','跨视图去重依据（组合未展开）'),node('p',`输入观察 ${summary.input_observations??'—'}；实体/组合 ${summary.entity_quantity??'—'}；扣除重复 ${summary.removed_duplicate_observations??'—'}；未知长度 ${summary.unknown_entity_quantity??'—'}；回退保留 ${summary.fallback_entity_quantity??'—'}。`));
      target.append(table(['规格','长度 mm','数量（组合未展开）','所属视图标签','处理方式','依据'],(data.entities||[]).map(r=>[r.spec,r.length_mm,r.member_cnt,r.view_tags,r.method,r.reason])));return;
    }
    const views=data.views||[],rows=data.view_members||[],specs=data.profile_catalog||[],axes=data.axis_names||[];
    target.append(node('h3',`${specs.length} 种钢材 · ${views.filter(v=>v.kind==='PLAN').length} 个平面 · ${views.filter(v=>v.kind==='ELEVATION').length} 个立面`),node('p',`钢材种类：${specs.join('、')||'尚未提取'}；轴线或面名称：${axes.join('、')||'尚未提取'}。`));
    target.append(table(['平面/立面名称','原始标题','标签数量','轴线或面名称','识别状态'],views.map(v=>[v.plane_name,v.title,v.labels.length,v.axis_labels.join('、'),stateNames[v.state]||v.state||'已提取'])));
    function summary(container,local,title){const value=LedgerTools.summarize(local);container.append(node('h3',title),node('p',`${value.quantity} 根/组合；${value.profileCount} 种规格；${value.unknownQuantity} 根未知长度`),table(['规格','长度 mm','数量'],value.groups.map(g=>[g.spec,g.length_mm,g.member_cnt])));}
    if(n===2)summary(target,rows,'全部视图台账汇总（未跨视图去重）');
    const panel=node('section');
    function show(id){const view=views.find(v=>v.id===id);activeView=view;panel.replaceChildren(node('h3',view.title));panel.append(node('img','',{src:view.image_url,alt:view.title,style:'max-width:100%'}),node('a','打开300DPI高清图',{href:view.image_url,target:'_blank',class:'download'}));
      if(n===1)panel.append(table(['标签ID','规格','数量','所属视图标签','原图位置'],view.labels.map(l=>[l.label_id,l.spec,l.member_cnt,l.view_tags,l.bbox_view])));
      else {const local=rows.filter(r=>r.source_view===id);summary(panel,local,'当前视图台账汇总');panel.append(table(['标签ID','规格','数量','长度 mm','所属视图标签','端部描述','尺寸依据','问题 / 校验'],local.map(r=>[r.label_id,r.spec,r.member_cnt,r.length_mm,r.view_tags,r.endpoints,r.basis,[r.issue,...(r.local_checks||[])].filter(Boolean).join('；')])));}
    }
    if(views.length){const chosen=views.some(v=>v.id===activeView?.id)?activeView.id:views[0].id;target.append(select('当前视图',chosen,views.map(v=>[v.id,v.title]),show),panel);show(chosen);}else target.append(node('p','本阶段尚未生成视图；当前进度和失败原因见上方任务日志。'));
  }
  return {list,get,stage,action,render,isNative:id=>ids.has(id),remember:id=>ids.add(id)};
})();

'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let current = new URL(location.href).searchParams.get('run') || '';
  let job = null, activeStage = 3, busy = false, timer, deliveries = [];
  const selectedReports = new Map();
  const statuses = {queued:'已加入队列',running:'正在运行',completed:'已完成',failed:'失败',interrupted:'已中断'};
  const title = ['','第一阶段 · 固定标签、轴面与高清图','第二阶段 · 尺寸与逐标签台账','第三阶段 · 跨视图匹配、消歧与去重'];
  async function api(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let message = await response.text();
      try { const value = JSON.parse(message); message = typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail); } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  }
  function error(e) { $('error').textContent = e ? e.message || String(e) : ''; }
  function settings() {
    const value = {reasoning_effort:$('effort').value};
    for (const [id, key] of [['concurrency','max_concurrency'],['budget','max_calls']]) {
      if ($(id).value) {
        const number = Number($(id).value);
        if (!Number.isInteger(number) || number < 1) throw new Error('并发上限和请求预算必须为正整数。');
        value[key] = number;
      }
    }
    return value;
  }
  function lock(value) { busy=value; $('start-match').disabled=value; $('start-pdf').disabled=value; $('rerun').disabled=value; }
  function link(text, href) { const node=document.createElement('a'); node.textContent=text; node.href=href; node.target='_blank'; node.rel='noopener'; return node; }
  function file(path) { return `/api/native/runs/${encodeURIComponent(current)}/files/${path}`; }
  function stage() {
    document.querySelectorAll('[data-stage]').forEach(b=>b.setAttribute('aria-selected',String(Number(b.dataset.stage)===activeStage)));
    $('stage-title').textContent=title[activeStage]; $('stage-links').replaceChildren();
    const items=(job?.stages || []).filter(s=>s.stage===activeStage);
    $('stage-message').hidden=items.length>0;
    $('stage-message').textContent=job?.mode==='MATCH' && activeStage<3 ? '此任务复用已有第二阶段交付包，仅执行第三阶段。来源列表可查看视图名称与数量。' : '此阶段尚未完成；日志将显示执行进度。';
    $('report').hidden=items.length===0;
    items.forEach((item,i)=>{
      const button=document.createElement('button'); button.textContent=`查看报告 ${i+1}`;
      button.onclick=()=>show(item); $('stage-links').append(button,' ');
    });
    if(items.length) show(items.find(item=>item.directory===selectedReports.get(`${current}:${activeStage}`)) || items[0]);
  }
  function show(item) { selectedReports.set(`${current}:${activeStage}`,item.directory); const url=file(`${item.directory}/index.html`); if($('report').getAttribute('src')!==url) $('report').src=url; }
  function render(value) {
    job=value; $('status').textContent=`${statuses[value.state] || value.state} · 阶段 ${value.stage} · ${value.id}`;
    $('summary').replaceChildren(); const s=value.summary || {};
    for(const [key,label] of [['input_observations','输入观察'],['entity_quantity','实体/组合'],['removed_duplicate_observations','去掉重复观察'],['unknown_entity_quantity','未知长度'],['fallback_entity_quantity','回退保留'],['api_request_attempts','第三阶段请求']]) {
      if(s[key]!==undefined) { const node=document.createElement('div'); node.className='stat'; node.textContent=`${label}：${s[key]}`; $('summary').append(node); }
    }
    if(value.error) error(new Error(value.error));
    $('downloads').replaceChildren();
    if(value.state==='completed') {
      $('downloads').append(link('下载 Excel',file('stage_three/stage_three.xlsx')),link('完整 JSON',file('stage_three/stage_three.json')),link('来源与守恒核验',file('stage_three/verification.json')));
    }
    $('downloads').append(link('下载运行日志',file('logs/events.jsonl')));
    if(value.evaluation_url) $('downloads').append(link('查看本轮离线评分',value.evaluation_url));
    $('rerun').hidden=!['completed','failed','interrupted'].includes(value.state);
    stage();
  }
  async function history() {
    const values=await api('/api/native/runs'); $('history').replaceChildren(new Option('请选择',''));
    values.forEach(v=>$('history').append(new Option(`${new Date(v.created_at).toLocaleString()} · ${statuses[v.state]} · ${v.id.slice(0,8)}`,v.id)));
    $('history').value=current;
  }
  async function poll() {
    clearTimeout(timer); if(!current) return;
    const identity=current;
    try {
      const [value, logs]=await Promise.all([api(`/api/native/runs/${identity}`),api(`/api/native/runs/${identity}/logs`)]);
      if(current!==identity) return;
      render(value);
      const nearBottom=$('logs').scrollHeight-$('logs').scrollTop-$('logs').clientHeight<60;
      $('logs').textContent=logs.items.map(r=>`${new Date(r.at).toLocaleTimeString()} ${r.message || r.event}${r.error ? '：'+r.error : ''}`).join('\n');
      if(nearBottom) $('logs').scrollTop=$('logs').scrollHeight;
      if(['queued','running'].includes(value.state)) timer=setTimeout(poll,2500);
      else await history();
    } catch(e) { if(current===identity) {error(e); timer=setTimeout(poll,5000);} }
  }
  async function select(identity) { current=identity; job=null; error(null); const url=new URL(location.href);url.searchParams.set('run',identity);window.history.replaceState(null,'',url); await history(); await poll(); }
  async function sources() {
    const selected=new Set([...document.querySelectorAll('#sources input:checked')].map(n=>n.value));
    const values=await api('/api/native/sources'); deliveries=values; $('sources').replaceChildren();
    for(const value of values) {
      const label=document.createElement('label'), check=document.createElement('input'); check.type='checkbox';check.value=value.id;check.checked=selected.has(value.id);
      label.append(check,document.createTextNode(value.name)); const sub=document.createElement('small');
      sub.textContent=`${value.summary.member_count ?? '?'} 条观察 · ${value.views.join('；')}`; label.append(document.createElement('br'),sub); $('sources').append(label);
    }
    if(!values.length) $('sources').textContent='尚无已完成的固定标签第二阶段结果，请先上传 PDF。';
  }
  $('start-match').onclick=async()=>{
    if(busy) return; error(null); lock(true);
    try {
      const source_ids=[...document.querySelectorAll('#sources input:checked')].map(n=>n.value);
      if(!source_ids.length) throw new Error('请至少选择一个第二阶段交付包。');
      const value=await api('/api/native/runs/match',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_ids,request_id:crypto.randomUUID(),...settings()})});
      activeStage=3; await select(value.id);
    } catch(e) {error(e);} finally {lock(false);}
  };
  $('start-pdf').onclick=async()=>{
    if(busy) return; error(null); lock(true);
    try {
      if(!$('files').files.length) throw new Error('请先选择 PDF 文件。');
      const form=new FormData();for(const f of $('files').files) form.append('files',f);
      for(const [key,value] of Object.entries(settings())) form.append(key,value);
      const value=await api('/api/native/runs/pdf',{method:'POST',body:form}); activeStage=1; await select(value.id);
    } catch(e) {error(e);} finally {lock(false);}
  };
  $('history').onchange=()=>{if($('history').value) select($('history').value).catch(error);};
  $('rerun').onclick=async()=>{
    if(busy || !job) return; error(null); lock(true);
    try {
      const value=await api(`/api/native/runs/${current}/rerun`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:crypto.randomUUID(),...settings()})});
      await select(value.id);
    } catch(e) {error(e);} finally {lock(false);}
  };
  $('refresh-sources').onclick=()=>sources().catch(error);
  $('select-all').onclick=()=>{
    const used=new Set(), chosen=new Set();
    for(const value of [...deliveries].sort((a,b)=>b.updated_at-a.updated_at)) {
      const views=value.view_ids.map(id=>`${value.source_sha256}:${id}`);
      if(!views.some(id=>used.has(id))) { chosen.add(value.id); views.forEach(id=>used.add(id)); }
    }
    document.querySelectorAll('#sources input').forEach(n=>n.checked=chosen.has(n.value));
  };
  document.querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>{activeStage=Number(b.dataset.stage);stage();});
  Promise.all([sources(),history()]).then(()=>current ? poll() : stage()).catch(error);
})();

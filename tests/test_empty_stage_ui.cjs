const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('function renderStage()');
const elements=new Map(),errors=[];
const element=()=>({children:[],replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);}});
const context={stageNumber:3,job:{state:'failed',active_stage:3,error_detail:'GroupProposal ID error'},
  draft:{status:'failed',revision:1,data:{}},stageNames:{3:'核验'},stateNames:{failed:'失败'},
  $:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);},
  node:(tag,text)=>({tag,text}),renderStageTabs(){},renderInventory(){},refreshJson(){},renderHistory(){},
  showError:e=>errors.push(e.message),
  renderMatches:()=>{throw Error('empty stage must not render legacy matches');},
  renderGroupReconciliation:()=>{throw Error('empty stage must not render groups');}};
vm.createContext(context);
vm.runInContext(source.slice(start,source.indexOf('\nfunction renderHistory()',start)),context);
context.renderStage();
assert.equal(context.$('#retry').disabled,false);
assert.equal(context.$('#save').disabled,true);
assert.equal(context.$('#approve').disabled,true);
assert.deepEqual(errors,['GroupProposal ID error']);
assert.ok(context.$('#stage-content').children[0].text.includes('尚未生成'));
context.job.state='running';context.draft.status='running';context.job.error_detail=null;
context.renderStage();assert.equal(context.$('#retry').disabled,true);
console.log('Empty failed/running stage preserves error and retry controls without rendering missing arrays.');

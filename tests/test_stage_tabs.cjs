const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('static/app.js','utf8');
const tabs={replaceChildren(...children){this.children=children;}};
const context={job:{stages:[1,2,3].map(stage=>({stage,status:'approved'}))},stageNumber:1,
  stageNames:{1:'one',2:'two',3:'three'},stateNames:{approved:'adopted'},$:()=>tabs,
  button:(text,onclick,className)=>({text,onclick,className,setAttribute(k,v){this[k]=v;}}),
  openStage:n=>{context.stageNumber=n;context.renderStageTabs();}};
vm.createContext(context);
const start=source.indexOf('function renderStageTabs()');
vm.runInContext(source.slice(start,source.indexOf('\nfunction renderStage()',start)),context);
context.renderStageTabs();
for(const n of [2,3,1]){
  tabs.children[n-1].onclick();
  assert.equal(tabs.children.filter(b=>b.className==='active').length,1);
  assert.equal(tabs.children[n-1].className,'active');
  assert.equal(tabs.children[n-1]['aria-current'],'step');
}
assert.ok(source.includes('function renderStage() { renderStageTabs();'));
assert.ok(!fs.readFileSync('static/index.html','utf8').includes('保留待确认项继续（不计入已确认汇总）'));
console.log('Stage tab selection changes on navigation and ambiguous exclusion label removed.');

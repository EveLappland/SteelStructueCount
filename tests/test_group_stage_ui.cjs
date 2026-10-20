const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
class Element {
    constructor(tag,text='',attrs={}){this.tag=tag;this.textContent=text;this.children=[];Object.assign(this,attrs);}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    scrollIntoView(){}
}
const target=new Element('section');
let errors=[],calls=[],changes=0;
const row=(id,source)=>({id,source_view:source,spec:'HEA240',view_tags:['EL.20.000(TOS)','FRAME/A'],member_cnt:3,length_mm:8000,location:'source position'});
const resolution={id:'ai-result',spec:'HEA240',view_tags:['EL.20.000(TOS)','FRAME/A'],length_mm:8000,member_cnt:3,reason:'source review',allocations:[{row_id:'r1',member_cnt:3},{row_id:'r2',member_cnt:3}]};
const context={
    Element,structuredClone,crypto:require('node:crypto').webcrypto,
    LedgerTools:require('../static/ledger.js'),dirty:false,jobId:'job',stageKey:'',
    matchSource:{profile_catalog:['HEA240'],view_members:[row('r1','p'),row('r2','a')],views:[
        {id:'p',title:'PLAN AT EL.20.000(TOS)',image_file:'asset-frozen-p_300dpi.png'},
        {id:'a',title:'ELEVATION ON ALIGNMENT FRAME/A',image_file:'asset-frozen-a_300dpi.png'}]},
    draft:{revision:2,status:'awaiting_review',data:{totals:{confirmed_groups:0,confirmed_assemblies:0,confirmed_single_members:0,pending_observation_units:6,pending_source_views:0},summary:[],resolutions:[],exclusions:[],review:{
        groups:[{id:'candidate',spec:'HEA240',length_mm:8000,view_tags:['EL.20.000(TOS)','FRAME/A'],agreed_member_cnt:3,review_reasons:[],sources:[
            {view_id:'p',title:'plan',member_cnt:3,row_ids:['r1']},{view_id:'a',title:'elevation',member_cnt:3,row_ids:['r2']}]}],
        ai_proposal:{validation:{valid:true},proposal:{resolutions:[resolution],exclusions:[],notes:[]}}}}},
    $:selector=>selector==='#retry-effort'?{value:'high'}:target,node:(...args)=>new Element(...args),
    table:(headers,rows)=>{const e=new Element('table');e.append(...rows.flat());return e;},
    button:(label,fn)=>Object.assign(new Element('button',label),{onclick:fn}),
    input:(label,value,fn)=>Object.assign(new Element('input',label),{value,change:fn}),
    mark:()=>{changes++;context.dirty=true;},splitRefs:v=>v.split(',').map(s=>s.trim()).filter(Boolean),
    settings:()=>({model:'test'}),api:async(...args)=>calls.push(args),poll:async()=>{},showError:e=>errors.push(e.message)
};
vm.createContext(context);vm.runInContext(fs.readFileSync('static/group_stage.js','utf8'),context);
context.renderGroupReconciliation();
const flatten=e=>e instanceof Element?[e,...e.children.flatMap(flatten)]:[];
const find=label=>flatten(target).find(e=>e.textContent===label);
find('载入核验编辑').onclick();
assert.equal(context.draft.data.resolutions.length,1);
assert.equal(context.draft.data.resolutions[0].allocations.length,2);
assert.ok(flatten(target).some(e=>e.tag==='img'&&e.src.endsWith('/asset-frozen-p_300dpi.png')));
find('所属面名称（逗号分隔）').change('PLAN AT EL.20.000(TOS), ELEVATION ON ALIGNMENT FRAME/A');
assert.equal(context.draft.data.resolutions[0].view_tags.join('|'),'EL.20.000(TOS)|FRAME/A');
find('载入AI提案到可编辑草稿').onclick();
assert.equal(errors.length,1);assert.equal(context.draft.data.resolutions[0].id,'result-candidate');
find('删除结果组').onclick();
find('载入AI提案到可编辑草稿').onclick();
assert.equal(context.draft.data.resolutions[0].id,'ai-result');
assert.notEqual(context.draft.data.resolutions[0],resolution);
(async()=>{
    await find('按完整key重新计算并导出').onclick();assert.equal(calls.length,0);
    context.dirty=false;await find('按完整key重新计算并导出').onclick();
    assert.equal(calls[0][2].settings,undefined);assert.equal(calls[0][2].reuse_saved,false);
    assert.equal(calls[0][2].auto_run,true);
    assert.ok(changes>0);console.log('Group review controls, frozen images and proposal overwrite protection passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});

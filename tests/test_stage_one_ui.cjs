const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../static/app.js'),'utf8');
const tools=require('../static/ledger.js');
class Element {
    constructor(tag,text='',attrs={}){this.tag=tag;this.textContent=text;this.children=[];Object.assign(this,attrs);}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
}
const node=(...args)=>new Element(...args);
const context={node,LedgerTools:tools,jobId:'test-job',activeView:null,
    table:(headers,rows)=>node('table',JSON.stringify({headers,rows})),
    select:(label,value,options,onChange)=>Object.assign(node('select',label),{value,options,onChange}),
    input:(label,value,onChange)=>Object.assign(node('input',label),{value,onChange}),
    setField:(object,key,value)=>{object[key]=value;},mark:()=>{},showError:error=>{throw error;},
    splitRefs:value=>value.split(',').filter(Boolean),document:{createTextNode:text=>node('text',text)}};
vm.createContext(context);
function load(name){const start=source.indexOf('function '+name+'(');const end=source.indexOf('\n}',start)+2;vm.runInContext(source.slice(start,end),context);}
load('renderConstructionCatalog');
const target=node('section'),panel=node('section');
const data={catalog_mode:'ai_pages',profile_catalog:['HEA260'],catalog_summary:{plan_names:['Deck-Q'],elevation_names:[],plane_name_count:2},
    page_decisions:[{file:'notes.pdf',page:1,decision:'SKIP',reason:'Only connection details',issue:''}],
    views:[{id:'v',title:'STRUCTURE PLAN Q',plane_name:'Deck-Q',kind:'PLAN',axis_labels:['Deck-Q','Frame-Z'],
            steel_types:['HEA260'],image_file:'frozen_300dpi.png',bbox:[0,0,1,1],region_confirmed:true}]};
context.renderConstructionCatalog(data,target,panel);
const walk=element=>[element,...element.children.flatMap(walk)];
const all=walk(target);
assert.ok(all.some(e=>e.textContent.includes('Only connection details')));
assert.equal(all.filter(e=>e.tag==='img').length,1);
assert.ok(all.find(e=>e.tag==='img').src.endsWith('/frozen_300dpi.png'));
assert.ok(!all.filter(e=>e.tag==='img').some(e=>(e.src||'').includes('notes.pdf')));
assert.ok(!all.some(e=>e.textContent.includes('X/Y/Z')));
const name=all.find(e=>e.tag==='input'&&e.textContent==='平面/立面名称');
name.onChange('Other deck');assert.equal(data.views[0].plane_name,'Other deck');
assert.equal(context.renderInventory,undefined); // Removed old native-regex catalog UI.
const empty=node('section');context.renderConstructionCatalog({...data,views:[]},empty,node('section'));
assert.equal(walk(empty).filter(e=>e.tag==='img').length,0);
console.log('Stage one page decisions, AI catalog, editable names and screenshot scope passed.');

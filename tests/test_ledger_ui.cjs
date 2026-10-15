const assert=require('node:assert/strict');
const tools=require('../static/ledger.js');
assert.equal(tools.viewRole({kind:'PLAN'}),'MEMBERS');
assert.equal(tools.viewRole({kind:'ISOMETRIC'}),'REFERENCE');
assert.equal(tools.viewRole({kind:'PLAN',takeoff_role:'REVIEW'}),'REVIEW');
assert.equal(tools.viewRole({kind:'PLAN',takeoff_role:'EXCLUDE'}),'EXCLUDE');
const stats=tools.summarize([
    {spec:'HEA260 /EPB',member_cnt:2,length_mm:6000},
    {spec:'HEA260',member_cnt:1,length_mm:6000},
    {spec:'IPET270 /LT',member_cnt:4,length_mm:null}
]);
assert.equal(stats.quantity,7);assert.equal(stats.profileCount,2);assert.equal(stats.unknownQuantity,4);
assert.equal(stats.groups[0].member_cnt,3);assert.equal(stats.groups[0].length_mm,6000);
// Exercise the actual controls without a browser or screenshots.
class Element {
    constructor(tag){this.tag=tag;this.children=[];this.value='';}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=[...items];}
    setAttribute(key,value){this[key]=value;}
}
const doc={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text})};
const row={view_tags:['PLAN A']};let changes=0;
const editor=tools.tagEditor(doc,row,[{kind:'PLAN',title:'PLAN A'},{kind:'ELEVATION',title:'ELEVATION 1',fixed_label:'PROJECT/1'}],()=>changes++);
const choices=editor.children[1].children[1];
choices.children[1].children[0].checked=true;choices.children[1].children[0].onchange();
assert.deepEqual(row.view_tags,['PLAN A','PROJECT/1']);
editor.children[0].children[0].children[1].onclick();
assert.deepEqual(row.view_tags,['PROJECT/1']);
editor.children[2].value='PROJECT/2；PROJECT/3';editor.children[3].onclick();
assert.deepEqual(row.view_tags,['PROJECT/1','PROJECT/2','PROJECT/3']);
assert.equal(changes,3);
assert.equal(tools.tagOf({kind:'PLAN',title:'PLAN AT EL.107.500(TOS)'}),'EL.107.500(TOS)');
assert.equal(tools.tagOf({kind:'ELEVATION',title:'ELEVATION ON ALIGNMENT PROJECT-Q/A'}),'PROJECT-Q/A');
assert.equal(tools.normalizeTag('plan at EL. -2.500 ( bob )'),'EL.-2.500(BOB)');
const canonicalRow={view_tags:['EL.107.500(TOS)']};
const canonicalEditor=tools.tagEditor(doc,canonicalRow,[{kind:'PLAN',title:'PLAN AT EL.107.500(TOS)'}],()=>{});
assert.equal(canonicalEditor.children[1].children[1].children[0].children[0].checked,true);
canonicalEditor.children[2].value='PLAN AT EL.107.500(TOS)';canonicalEditor.children[3].onclick();
assert.deepEqual(canonicalRow.view_tags,['EL.107.500(TOS)']);
console.log('Ledger summary and tag add/remove controls passed.');

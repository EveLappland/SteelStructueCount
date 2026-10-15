'use strict';
(function(root) {
    function normalizeTag(value) {
        const name=String(value).trim().replace(/^(?:PLAN\s+AT|ELEVATION\s+ON\s+ALIGNMENT)\s+/i,'').trim();
        const level=name.match(/^EL\.?\s*([+-]?\d+(?:\.\d+)?)\s*(?:\(\s*([A-Za-z][\w -]*)\s*\))?$/i);
        return level?'EL.'+level[1]+(level[2]?'('+level[2].trim().toUpperCase()+')':''):name;
    }
    const tagOf=view=>normalizeTag(view.kind==='ELEVATION'&&view.fixed_label?view.fixed_label:view.title);
    const viewRole=view=>view.kind==='ISOMETRIC'?(view.takeoff_role==='EXCLUDE'?'EXCLUDE':'REFERENCE'):(view.takeoff_role||'MEMBERS');
    function summarize(rows) {
        const groups=new Map();let quantity=0,unknownQuantity=0;
        for(const row of rows) {
            const count=Number(row.member_cnt);
            if(!Number.isInteger(count)||count<1)continue;
            const spec=String(row.spec||'未知规格').toUpperCase().replace(/\s+/g,'').replace(/\/(?:EPB|EPW|EP|EMH|EMTF|LT)\b/g,'');
            const length=row.length_mm==null?null:Number(row.length_mm);
            const key=JSON.stringify([spec,length]);
            if(!groups.has(key))groups.set(key,{spec,length_mm:length,member_cnt:0});
            groups.get(key).member_cnt+=count;quantity+=count;
            if(length==null)unknownQuantity+=count;
        }
        return {quantity,unknownQuantity,groups:[...groups.values()].sort((a,b)=>a.spec.localeCompare(b.spec)||(a.length_mm??Infinity)-(b.length_mm??Infinity)),
                profileCount:new Set([...groups.values()].map(g=>g.spec)).size};
    }
    function tagEditor(doc,row,views,onChange) {
        const wrap=doc.createElement('div'),selected=doc.createElement('div'),details=doc.createElement('details');
        const summary=doc.createElement('summary');summary.textContent='选择或更改所属视图';details.append(summary);
        const choices=doc.createElement('div');details.append(choices);
        const add=doc.createElement('input');add.type='text';add.placeholder='补充图纸标签名称';
        const addButton=doc.createElement('button');addButton.type='button';addButton.textContent='添加标签';
        function update(tags) {row.view_tags=[...new Set(tags.map(normalizeTag).filter(Boolean))];onChange();render();}
        function render() {
            selected.replaceChildren();choices.replaceChildren();
            for(const tag of row.view_tags) {
                const chip=doc.createElement('div'),text=doc.createElement('span'),remove=doc.createElement('button');
                text.textContent=tag;remove.type='button';remove.textContent='移除';
                remove.setAttribute('aria-label',`移除标签 ${tag}`);
                remove.onclick=()=>update(row.view_tags.filter(t=>t!==tag));chip.append(text,remove);selected.append(chip);
            }
            for(const tag of [...new Set(views.filter(v=>v.kind!=='ISOMETRIC').map(tagOf))]) {
                const label=doc.createElement('label'),check=doc.createElement('input');check.type='checkbox';check.checked=row.view_tags.includes(tag);
                check.onchange=()=>update(check.checked?[...row.view_tags,tag]:row.view_tags.filter(t=>t!==tag));
                label.append(check,doc.createTextNode(tag));choices.append(label);
            }
        }
        addButton.onclick=()=>{const tags=add.value.split(/[,，;；\n]+/).map(t=>t.trim()).filter(Boolean);if(tags.length){update([...row.view_tags,...tags]);add.value='';}};
        wrap.append(selected,details,add,addButton);render();return wrap;
    }
    root.LedgerTools={summarize,tagEditor,tagOf,normalizeTag,viewRole};
    if(typeof module!=='undefined')module.exports=root.LedgerTools;
})(globalThis);

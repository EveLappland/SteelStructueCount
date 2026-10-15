"""Named reference planes: Alignment elevations and elevation-level plans."""
import re
from .native_pdf import inside
from .discovery_inventory import labels


def axis_name(text):
    text=text.strip()
    if list(labels(text)):return None
    alignment=re.fullmatch(r'ELEVATION\s+ON\s+ALIGNMENT\s+([^&,;]+)',text,re.I)
    if alignment:return alignment[1].strip()
    level=re.fullmatch(r'(?:PLAN\s+AT\s+)?EL\.?\s*([+-]?\d+(?:\.\d+)?)\s*(?:\(\s*([A-Za-z][\w -]*)\s*\))?',text,re.I)
    if level:return 'EL.'+level[1]+('('+level[2].strip().upper()+')' if level[2] else '')
    if re.fullmatch(r'[\w.-]+/(?:[A-Za-z]{1,4}|\d{1,4})',text):return text
    match=re.fullmatch(r'(?:GRID|AXIS|轴线)\s*[:：-]?\s*([\w.-]+)',text,re.I)
    return match[1] if match else None


def build_axis_catalog(pages,views):
    native={t['id']:t for page in pages for t in page['texts']}
    axes={};per_view={v['id']:[] for v in views if v['kind']!='ISOMETRIC'}
    def add(name,view_ids=(),evidence=None,uploaded=None,kind=None):
        if not name:return
        name=axis_name(name) or name.strip()
        if not name:return
        kind=kind or ('PLAN' if re.fullmatch(r'EL\..+',name,re.I) else 'ELEVATION')
        entry=axes.setdefault(name,{'name':name,'kind':kind,'evidence':[],'source_view_ids':[],'uploaded_view_ids':[]})
        if evidence and evidence not in entry['evidence']:entry['evidence'].append(evidence)
        for identity in view_ids:
            if identity not in entry['source_view_ids']:entry['source_view_ids'].append(identity)
            if name not in per_view.setdefault(identity,[]):per_view[identity].append(name)
        if uploaded and uploaded not in entry['uploaded_view_ids']:entry['uploaded_view_ids'].append(uploaded)
    for view in views:
        if view['kind']=='ELEVATION':add(view.get('plane_name') or view.get('fixed_label'),[view['id']],uploaded=view['id'])
        elif view['kind']=='PLAN':add(view.get('plane_name') or axis_name(view['title']) or view['title'],[view['id']],uploaded=view['id'],kind='PLAN')
        if view.get('axes_reviewed') or view.get('plane_name'):
            for name in view.get('axis_labels',[]):add(name,[view['id']],{'kind':'CONFIRMED_VIEW_REFERENCE' if view.get('axes_reviewed') else 'AI_VIEW_CATALOG'})
            continue
        for mark in view.get('axis_marks',[]):
            if mark.get('direction') not in ('X','Y','Z'):continue
            if mark.get('direction')=='Z' and not axis_name(mark['label']):continue
            references=[native[e] for e in mark.get('evidence_ids',[]) if e in native]
            if references and not any(inside(t,view['bbox']) for t in references):continue
            add(mark['label'],[view['id']],{'kind':'VIEW_AXIS_MARK','evidence_ids':mark.get('evidence_ids',[])})
    for page in pages:
        local=[v for v in views if v.get('page_id')==page['id'] and v['kind']!='ISOMETRIC' and v.get('region_confirmed',True)]
        for text in page['texts']:
            name=axis_name(text['text'])
            owners=[v['id'] for v in local if not v.get('axes_reviewed') and not v.get('plane_name') and inside(text,v['bbox'])]
            if name and owners:
                add(name,owners,{'id':text['id'],'page_id':page['id'],'text':text['text'],'bbox':text['bbox']})
    # Full native names take precedence over short marks only when unambiguous
    # in the SAME view, avoiding A vs PROJECT/A being counted twice.
    for identity,names in per_view.items():
        for short in list(names):
            if '/' in short or axes[short]['kind']!='ELEVATION':continue
            full=[n for n in names if axes[n]['kind']=='ELEVATION' and '/' in n and n.rsplit('/',1)[1]==short]
            if len(full)==1:
                source=axes[short];target=axes[full[0]]
                for ev in source['evidence']:
                    if ev not in target['evidence']:target['evidence'].append(ev)
                names.remove(short)
                if identity in source['source_view_ids']:source['source_view_ids'].remove(identity)
                if identity in source['uploaded_view_ids']:
                    source['uploaded_view_ids'].remove(identity)
                    if identity not in target['uploaded_view_ids']:target['uploaded_view_ids'].append(identity)
    axes={n:e for n,e in axes.items() if e['source_view_ids'] or e['uploaded_view_ids']}
    plans=[{'id':v['id'],'name':v['title']} for v in views if v['kind']=='PLAN']
    return {'axes':[axes[n] for n in sorted(axes)],'axis_count':len(axes),'plans':plans,
            'alignment_count':sum(e['kind']=='ELEVATION' for e in axes.values()),
            'level_plane_count':sum(e['kind']=='PLAN' for e in axes.values()),
            'plan_count':len(plans),'elevation_view_count':sum(v['kind']=='ELEVATION' for v in views),
            'per_view':{identity:sorted(names) for identity,names in per_view.items()},
            'basis':'轴线或面名称包括Alignment立面与EL标高平面；按当前视图绑定，名称出现不等于已上传该面的独立视图。'}

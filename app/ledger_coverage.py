"""Refresh native-label coverage after edits without changing recognition status."""
from collections import Counter

from .discovery_inventory import labels
from .geometry import section
from .native_pdf import inside


def refresh_coverage(data,pages):
    pages={p['id']:p for p in pages}
    views={v['id']:v for v in data['views']}
    for analysis in data.get('view_analysis',[]):
        if analysis['status'] not in ('completed','empty'):continue
        view=views[analysis['view_id']]
        native=Counter(profile for text in pages[view['page_id']]['texts'] if inside(text,view['bbox'])
                       for profile,_ in labels(text['text']))
        recognized=Counter()
        for row in data['view_members']:
            if row['source_view']==view['id']:recognized[section(row['spec'])[0]]+=row['member_cnt']
        count=sum(recognized.values());native_count=sum(native.values())
        coverage=[{'spec':spec,'label_count':native[spec],'member_cnt':recognized[spec]}
                  for spec in sorted(native.keys()|recognized.keys()) if native[spec]!=recognized[spec]]
        analysis.update(member_cnt=count,label_count=native_count,coverage_differences=coverage,
                        status='completed' if count else 'empty')
        prefix=view['title']
        data['warnings']=[w for w in data.get('warnings',[]) if not (
            w.startswith(prefix+'：') and ('处规格标签与' in w or '处钢材标签与' in w) or
            w.startswith(prefix+' 总数虽相同，但各规格数量覆盖不一致') or
            w.startswith(prefix+' 未识别到构件，不能作为零根验收'))]
        if count!=native_count:data['warnings'].append(f'{prefix}：{native_count}处钢材标签与{count}根钢材不一致，需核对。')
        elif coverage:data['warnings'].append(f'{prefix} 总数虽相同，但各规格数量覆盖不一致，请核对。')
        if not count:data['warnings'].append(f'{prefix} 未识别到构件，不能作为零根验收。')
    return data

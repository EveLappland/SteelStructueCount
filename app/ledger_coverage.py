"""Refresh native-label coverage after edits without changing recognition status."""
from .coverage import assess_rows
from .quantity_reference import assess_quantity


def refresh_coverage(data,pages):
    pages={p['id']:p for p in pages}
    views={v['id']:v for v in data['views']}
    for analysis in data.get('view_analysis',[]):
        if analysis['status'] not in ('completed','completed_with_warnings','empty'):continue
        view=views[analysis['view_id']]
        rows=[row for row in data['view_members'] if row['source_view']==view['id']]
        if data.get('recognition_strategy')=='grid_agents':
            assessment=assess_quantity(rows,view.get('quantity_reference'))
            analysis.update(member_cnt=assessment['actual_total'],label_count=assessment['expected_total'],
                quantity_reference_match=assessment['matches'],quantity_reference_available=assessment['reference_available'],
                coverage_differences=assessment['differences'],
                remaining_checks=[c for c in analysis.get('remaining_checks',[]) if c.get('check')!='stage_one_quantity_reference']+assessment['feedback'])
            continue
        assessment=assess_rows(rows,pages[view['page_id']],view)
        count=assessment['member_cnt'];native_count=assessment['label_count']
        coverage=assessment['coverage_differences']
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

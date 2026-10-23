"""Generic geometry tests; no project IDs, PDF answers or mocked model output."""
import unittest

from experiments.pdf_label_tags.native import analyze, assign_axis, reference_lines, steel_occurrences


def text(identity, value, box, direction=(1, 0)):
    return {'id': identity, 'text': value, 'bbox': box,
            'style': {'direction': list(direction), 'font_size': 5}}


def page(texts=(), vectors=()):
    return {'id': 'p', 'width_pt': 1000, 'height_pt': 1000,
            'texts': list(texts), 'vectors': list(vectors)}


def axis(name, coordinate, kind='HORIZONTAL'):
    return {'name': name, 'source_text_id': 'axis-name',
            'line': {'orientation': kind, 'coordinate_pt': coordinate,
                     'span_pt': [100, 900], 'vector_id': 'axis-line'}}


def label(box=(.3, .493, .36, .497), direction=(1, 0)):
    return steel_occurrences(page([text('steel', 'HEM420', box, direction)]))[0]


def vector(identity, x0, y0, x1, y1, dashes=None):
    return {'id': identity, 'bbox': [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
            'points': [[x0, y0], [x1, y1]], 'style': {'dashes': dashes}}


def bubble_example(mirrored=False, gap=False):
    vectors = [vector('border-top', .79, .493, .91, .493),
               vector('border-bottom', .79, .507, .91, .507),
               vector('border-bottom-overprint', .79, .507, .91, .507),
               vector('connector', .77, .5, .795, .5, '[3 1] 0'),
               vector('member', .5, .5, .74 if gap else .768, .5),
               vector('other-level', .5, .515, .768, .515)]
    texts = [text('axis', 'EL.42.000(TOS)', [.8, .495, .9, .505]),
             text('member-label', 'HEM420', [.6, .491, .64, .495])]
    if mirrored:
        for item in vectors:
            item['points'] = [[1-x, y] for x, y in item['points']]
        for item in texts+vectors:
            a, b, c, d = item['bbox']
            item['bbox'] = [1-c, b, 1-a, d]
    return page(texts, vectors)


class NativeLabelTagTests(unittest.TestCase):
    def test_equal_specs_at_different_positions_are_not_deduplicated(self):
        rows = steel_occurrences(page([text('s1', 'HEM420', [.2, .2, .3, .21]),
                                        text('s2', 'HEM420', [.2, .4, .3, .41])]))
        self.assertEqual(sum(r['member_cnt'] for r in rows), 2)
        self.assertEqual(len({r['id'] for r in rows}), 2)

    def test_assembly_label_counts_one_assembly(self):
        row = steel_occurrences(page([text('s', '2LF-L90*9 /LT', [.2, .2, .3, .21])]))[0]
        self.assertEqual((row['spec'], row['member_cnt'], row['component_multiplier']), ('2LF-L90*9', 1, 2))

    def test_label_orientation_selects_the_axis_family(self):
        result = assign_axis(label(), [axis('FRAME-Q/R7', 500), axis('FRAME-Q/M', 330, 'VERTICAL')], page(), 'EL.42.000(TOS)')
        self.assertEqual(result['view_tags'], ['EL.42.000(TOS)', 'FRAME-Q/R7'])

    def test_mirror_and_renamed_axis(self):
        result = assign_axis(label((.793, .4, .797, .46), (0, -1)),
                             [axis('OTHER-Z/West', 800, 'VERTICAL')], page(), 'LEVEL-ROOF')
        self.assertEqual(result['view_tags'], ['LEVEL-ROOF', 'OTHER-Z/West'])

    def test_internal_member_keeps_bracketing_context_out_of_tags(self):
        result = assign_axis(label(), [axis('GRID/8', 400), axis('GRID/9', 600)], page(), 'ROOF')
        self.assertEqual(result['view_tags'], ['ROOF'])
        self.assertEqual(result['axis_context']['HORIZONTAL'], {'before': 'GRID/8', 'after': 'GRID/9'})

    def test_diagonal_does_not_inherit_a_nearby_axis(self):
        result = assign_axis(label(direction=(.6, -.8)), [axis('GRID/8', 500)], page(), 'ROOF')
        self.assertEqual(result['view_tags'], ['ROOF'])

    def test_two_nearby_named_lines_remain_ambiguous(self):
        result = assign_axis(label(), [axis('GRID/8', 495), axis('GRID/9', 500)], page(), 'ROOF')
        self.assertEqual(result['tag_status'], 'AMBIGUOUS')
        self.assertEqual(result['view_tags'], ['ROOF'])

    def test_overlapping_views_do_not_silently_double_count(self):
        p = page([text('s', 'HEM420', [.2, .2, .3, .21])])
        region = dict(page_id='p', title='PLAN AT EL.42.000(TOS)', title_id='t', bbox=[0, 0, 1, 1])
        result = analyze([p], [dict(region, id='v1'), dict(region, id='v2')])
        self.assertEqual(result['summary']['label_count'], 0)
        self.assertEqual(len(result['ownership_conflicts']), 1)

    def test_native_references_require_a_line_not_only_a_name(self):
        p = page([text('a', 'GRID R7', [.04, .49, .09, .51])], [
            {'id': 'v', 'bbox': [.095, .5, .9, .5], 'points': [[.095, .5], [.9, .5]],
             'style': {'dashes': None}}])
        refs = reference_lines(p, dict(title_id='title', bbox=[0, 0, 1, 1]))
        self.assertEqual(refs[0]['name'], 'R7')
        self.assertEqual(refs[0]['line']['vector_id'], 'v')

    def test_bubble_border_is_rejected_and_short_leader_traces_to_member(self):
        p = bubble_example()
        refs = reference_lines(p, dict(title_id='title', bbox=[0, 0, 1, 1]))
        self.assertEqual(refs[0]['line']['vector_id'], 'connector')
        self.assertIn('member', refs[0]['line']['support_vector_ids'])
        self.assertNotIn('other-level', refs[0]['line']['support_vector_ids'])
        self.assertEqual(set(refs[0]['excluded_bubble_vector_ids']),
                         {'border-top', 'border-bottom', 'border-bottom-overprint'})
        result = assign_axis(steel_occurrences(p)[0], refs, p, 'FRAME/West')
        self.assertEqual(result['view_tags'], ['FRAME/West', 'EL.42.000(TOS)'])

    def test_short_leader_trace_works_with_mirrored_view(self):
        p = bubble_example(mirrored=True)
        refs = reference_lines(p, dict(title_id='title', bbox=[0, 0, 1, 1]))
        result = assign_axis(steel_occurrences(p)[0], refs, p, 'FRAME/West')
        self.assertEqual(result['view_tags'], ['FRAME/West', 'EL.42.000(TOS)'])

    def test_distant_disconnected_member_is_not_assigned(self):
        p = bubble_example(gap=True)
        refs = reference_lines(p, dict(title_id='title', bbox=[0, 0, 1, 1]))
        self.assertNotIn('member', refs[0]['line']['support_vector_ids'])
        result = assign_axis(steel_occurrences(p)[0], refs, p, 'FRAME/West')
        self.assertEqual(result['view_tags'], ['FRAME/West'])

    def test_bubble_alone_does_not_prove_a_reference_line(self):
        p = bubble_example()
        p['vectors'] = [v for v in p['vectors'] if v['id'].startswith('border')]
        refs = reference_lines(p, dict(title_id='title', bbox=[0, 0, 1, 1]))
        self.assertIsNone(refs[0]['line'])


if __name__ == '__main__':
    unittest.main()

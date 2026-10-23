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


if __name__ == '__main__':
    unittest.main()

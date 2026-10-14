import base64
import json
import unittest

from tools.compare_member_requests import minimal_request, summarize


class RequestComparisonTests(unittest.TestCase):
    def test_minimal_request_preserves_exact_prompt_and_image(self):
        prompt = '用户原文\n不附加信息\n'
        image = b'\x89PNG\r\n\x1a\noriginal-image-bytes'
        request = minimal_request('test-model', prompt, image)
        self.assertEqual(set(request), {'model','messages'})
        self.assertEqual(len(request['messages']), 1)
        message = request['messages'][0]
        self.assertEqual(message['role'], 'user')
        self.assertEqual(message['content'][0]['text'], prompt)
        url = message['content'][1]['image_url']['url']
        self.assertEqual(base64.b64decode(url.split(',',1)[1]), image)
        self.assertEqual(len(message['content']), 2)

    def test_summary_retains_null_lengths_and_raw_specs(self):
        row = {'spec':'2LF-L90*8','view_tags':['EL.1'], 'member_cnt':2,
               'length_mm':None,'location':'A轴与B轴之间','issue':'标尺缺失'}
        result = summarize(json.dumps({'members':[row,row]}))
        self.assertEqual(result['member_cnt'],4)
        self.assertEqual(result['unknown_length_rows'],2)
        self.assertEqual(result['ledger'],[{'spec':'2LF-L90*8','length_mm':None,'member_cnt':4}])

    def test_summary_rejects_duplicate_or_incomplete_output(self):
        for text in ('{"members":[],"members":[]}', '{"members":[{"spec":"HEA300"}]}'):
            self.assertIn('parse_error', summarize(text))


if __name__ == '__main__':
    unittest.main()

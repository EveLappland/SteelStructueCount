import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch

from app.group_proposal import GroupProposal,propose_groups
from app.pipeline import model_call
from test_group_dedup import fixture,row
from test_group_reconciliation import resolution


class ProposalTests(unittest.TestCase):
    def test_proposal_isolated_from_reference_and_does_not_confirm_or_mutate_job(self):
        two=fixture([row('p1','p'),row('a1','a')]);two['reference_answers']='DO_NOT_SEND_REFERENCE'
        original=copy.deepcopy(two)
        with tempfile.TemporaryDirectory() as temp,patch('app.group_proposal._api_settings',return_value=('https://example.test','test-key','deepseek-flash')),patch('app.pipeline.OpenAI'),patch('app.pipeline.model_call',return_value={'resolutions':[resolution()], 'exclusions':[],'notes':[]}) as call:
            result=propose_groups(two,Path(temp),{},lambda _:None,MagicMock())
            self.assertTrue(result['validation']['valid'])
            self.assertEqual(result['data']['totals']['confirmed_single_members'],3)
            self.assertTrue(call.call_args.args[3]['text_only'])
            self.assertEqual(call.call_args.kwargs['reasoning_effort'],'max')
            self.assertNotIn('DO_NOT_SEND_REFERENCE',call.call_args.args[2])
            self.assertEqual(two,original)
            self.assertTrue((Path(result['folder'])/'validated_proposal.json').is_file())

    def test_invalid_quantity_proposal_is_retained_but_not_accepted(self):
        two=fixture([row('p1','p'),row('a1','a')])
        with tempfile.TemporaryDirectory() as temp,patch('app.group_proposal._api_settings',return_value=('https://example.test','test-key','deepseek-flash')),patch('app.pipeline.OpenAI'),patch('app.pipeline.model_call',return_value={'resolutions':[resolution(count=4)],'exclusions':[],'notes':[]}):
            result=propose_groups(two,Path(temp),{},lambda _:None,MagicMock())
            self.assertFalse(result['validation']['valid'])
            self.assertIsNone(result['data'])

    def test_invalid_proposal_is_repaired_by_model_without_program_editing_members(self):
        two=fixture([row('p1','p'),row('a1','a')])
        bad={'resolutions':[resolution(count=4)],'exclusions':[],'notes':[]}
        good={'resolutions':[resolution()],'exclusions':[],'notes':['模型修正数量分配']}
        with tempfile.TemporaryDirectory() as temp,patch('app.group_proposal._api_settings',return_value=('https://example.test','k','m')),patch('app.pipeline.OpenAI'),patch('app.pipeline.model_call',side_effect=[bad,good]) as call:
            result=propose_groups(two,Path(temp),{},lambda _:None,MagicMock())
            self.assertEqual(call.call_count,2)
            self.assertIn('本地检查错误',call.call_args.args[2])
            self.assertEqual(result['proposal'],good)
            self.assertEqual(result['data']['totals']['confirmed_single_members'],3)
            self.assertTrue(result['validation']['valid'])

    def test_text_only_wire_request_never_renders_or_sends_an_image(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);source=directory/'input.json';source.write_text('{}')
            client=MagicMock();client.base_url='https://example.test'
            response=MagicMock();response.model_dump_json.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"resolutions":[],"exclusions":[],"notes":[]}'}}]})
            client.chat.completions.create.return_value=response
            with patch('app.pipeline.render_data_uri',side_effect=AssertionError('must not render')):
                model_call(client,'deepseek-flash','group review',{'id':'g','page':1,'source_path':str(source),'text_only':True},directory/'response.json',GroupProposal,reasoning_effort='max')
            request=client.chat.completions.create.call_args.kwargs
            self.assertEqual([c['type'] for c in request['messages'][1]['content']],['text'])
            self.assertEqual(request['reasoning_effort'],'max')

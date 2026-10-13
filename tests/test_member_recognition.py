import unittest
from pydantic import ValidationError
from app.member_recognition import MemberResponse, make_input, recognition_prompt, validate_rows, validate_ledger


class MemberRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.view={'id':'plan1','page_id':'p','title':'PLAN AT EL.1.000(TOS)','kind':'PLAN','bbox':[0,0,.5,1]}
        self.one={'profile_catalog':['HEA240','L100*10'],'views':[self.view,
                  {'id':'elA','kind':'ELEVATION','title':'ELEVATION A'},
                  {'id':'iso','kind':'ISOMETRIC','title':'ISOMETRIC'}]}
        self.row={'spec':'HEA240','view_tags':['elA','plan1'],'member_cnt':2,
                  'length_mm':6000,'basis':'左、右侧各一根，尺寸6000，无拼接','issue':None}

    def test_minimal_schema_and_strict_counts(self):
        self.assertEqual(set(MemberResponse.model_json_schema()['$defs']['RecognizedMember']['properties']),set(self.row))
        for count in (0,-1,1.5,True,'2'):
            with self.assertRaises(ValidationError):MemberResponse.model_validate({'members':[{**self.row,'member_cnt':count}]})
        for length in (0,-10,float('nan'),float('inf')):
            with self.assertRaises(ValidationError):MemberResponse.model_validate({'members':[{**self.row,'length_mm':length}]})
        with self.assertRaises(ValidationError):MemberResponse.model_validate({'members':[{**self.row,'x':1}]})

    def test_pack_uses_stage_one_catalog_and_crop(self):
        page={'id':'p','file':'drawing.pdf','page':1,'width_pt':800,'height_pt':600,
              'texts':[{'id':'t1','text':'HEA240','bbox':[.1,.1,.2,.2]},
                       {'id':'t2','text':'HEA300','bbox':[.6,.1,.7,.2]}], 'vectors':[]}
        bundle=make_input(page,self.view,self.one)
        self.assertEqual(bundle['profile_catalog'],self.one['profile_catalog'])
        self.assertEqual([t['id'] for t in bundle['native_evidence']['texts']],['t1'])
        self.assertEqual(bundle['current_view_id'],'plan1')
        self.assertIn('OF SPLICE',recognition_prompt(bundle))
        self.assertIn('member_cnt',recognition_prompt(bundle))

    def test_cross_view_records_remain_independent_and_tags_sorted(self):
        a=validate_rows([self.row],self.view,self.one)[0]
        view={**self.view,'id':'elA'}
        b=validate_rows([{**self.row,'view_tags':['plan1','elA']}],view,self.one)[0]
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(a['view_tags'],b['view_tags'])
        self.assertEqual(a['member_cnt'],2)
        self.assertEqual(a['checks'],[])

    def test_splices_separate_lengths_and_assembly_not_expanded(self):
        rows=validate_rows([{**self.row,'member_cnt':1,'length_mm':3000,'basis':'6000中部OF SPLICE，6000/2'},
                            {**self.row,'member_cnt':1,'length_mm':3000,'basis':'拼接另一段6000/2'},
                            {**self.row,'spec':'2LF-L100*10','member_cnt':1}],self.view,self.one)
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[-1]['member_cnt'],1)
        ledger=validate_ledger({**self.one,'view_members':rows})
        self.assertEqual(ledger['length_catalog'][1]['member_cnt'],2)
        self.assertTrue(all(not r['deduplicated'] for r in ledger['length_catalog']))

    def test_missing_lengths_bad_tags_and_catalog_are_pending(self):
        row=validate_rows([{**self.row,'length_mm':None,'view_tags':['iso','unknown'],'spec':'HEA999',
                            'issue':'标尺不完整'}],self.view,self.one)[0]
        self.assertGreaterEqual(len(row['checks']),5)
        self.assertEqual(row['length_mm'],None)
        corrected={**row,'spec':'HEA240','length_mm':6000,'view_tags':['plan1','elA'],'issue':None}
        self.assertEqual(validate_ledger({**self.one,'view_members':[corrected]})['view_members'][0]['checks'],[])

    def test_duplicate_identity_rejected(self):
        row=validate_rows([self.row],self.view,self.one)[0]
        with self.assertRaises(ValueError):validate_ledger({**self.one,'view_members':[row,row]})

    def test_user_bottom_beam_has_plan_and_alignment_names(self):
        plan={**self.view,'title':'PLAN AT EL.107.500(TOS)'}
        elevation={'id':'el1','kind':'ELEVATION','title':'ELEVATION ON ALIGNMENT 302003-PR001I/1',
                   'fixed_label':'302003-PR001I/1'}
        one={'views':[plan,elevation],'profile_catalog':['HEA260']}
        raw={**self.row,'spec':'HEA260','member_cnt':1,'view_tags':[plan['title'],elevation['fixed_label']],
             'basis':'最下面的梁位于1轴线；长度6000由用户确认'}
        result=validate_rows([raw],plan,one)[0]
        self.assertEqual(result['view_tags'],['302003-PR001I/1','PLAN AT EL.107.500(TOS)'])
        self.assertEqual(result['source_view'],'plan1')
        self.assertEqual(result['checks'],[])
        legacy=validate_rows([{**raw,'view_tags':['plan1','el1']}],plan,one)[0]
        self.assertEqual(legacy['view_tags'],result['view_tags'])
        page={'id':'p','file':'drawing.pdf','page':1,'width_pt':800,'height_pt':600,'texts':[],'vectors':[]}
        bundle=make_input(page,plan,one)
        self.assertEqual(bundle['current_view_tag'],plan['title'])
        self.assertEqual(bundle['view_catalog'][1]['tag'],'302003-PR001I/1')

    def test_duplicate_json_members_key_rejected(self):
        from app.pipeline import parse_model_content
        with self.assertRaisesRegex(ValueError,'重复键'):
            parse_model_content('{"members":[],"members":[]}',MemberResponse,{'id':'plan1'})

    def test_invalid_response_preserved_and_retry_bounded(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock,patch
        from app.pipeline import model_call
        def response(content):
            result=Mock()
            result.model_dump_json.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':content}}]})
            return result
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';source.write_bytes(b'pdf-source-for-hash')
            target=Path(folder)/'responses/attempt-one/members.json'
            client=Mock();client.base_url='https://example.test'
            client.chat.completions.create.side_effect=[response('{"members":[],"members":[]}'),
                                                        response(json.dumps({'members':[self.row]}))]
            with patch('app.pipeline.render_data_uri',return_value='data:image/png;base64,test'):
                result=model_call(client,'test','recognize',{'id':'plan1','page':1,'source_path':str(source)},target,MemberResponse)
            self.assertEqual(result['members'][0]['member_cnt'],2)
            self.assertEqual(client.chat.completions.create.call_count,2)
            self.assertTrue(target.with_suffix('.invalid.json').is_file())
            client.chat.completions.create.side_effect=[response('{}'),response('{}')]
            with patch('app.pipeline.render_data_uri',return_value='data:image/png;base64,test'),self.assertRaises(ValidationError):
                model_call(client,'test','recognize',{'id':'plan1','page':1,'source_path':str(source)},target,MemberResponse)
            self.assertEqual(client.chat.completions.create.call_count,4)

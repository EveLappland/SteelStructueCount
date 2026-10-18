import unittest
from pydantic import ValidationError
from app.member_recognition import MemberResponse, make_input, recognition_prompt, validate_rows, validate_ledger, allowed_model_tags


class MemberRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.view={'id':'plan1','page_id':'p','title':'PLAN AT EL.1.000(TOS)','kind':'PLAN','bbox':[0,0,.5,1]}
        self.one={'profile_catalog':['HEA240','L100*10'],'views':[self.view,
                  {'id':'elA','kind':'ELEVATION','title':'ELEVATION A'},
                  {'id':'iso','kind':'ISOMETRIC','title':'ISOMETRIC'}]}
        self.row={'spec':'HEA240','view_tags':['elA','plan1'],'member_cnt':2,
                  'length_mm':6000,'location':'左、右侧各一根，A轴与B轴之间','issue':None}

    def test_minimal_schema_and_strict_counts(self):
        self.assertEqual(set(MemberResponse.model_json_schema()['$defs']['RecognizedMember']['properties']),set(self.row))
        for count in (0,-1,1.5,True,'2'):
            with self.assertRaises(ValidationError):MemberResponse.model_validate({'members':[{**self.row,'member_cnt':count}]})
        for length in (0,-10,float('nan'),float('inf')):
            with self.assertRaises(ValidationError):MemberResponse.model_validate({'members':[{**self.row,'length_mm':length}]})
        with self.assertRaises(ValidationError):MemberResponse.model_validate({'members':[{**self.row,'x':1}]})
        old={k:v for k,v in self.row.items() if k!='location'}
        with self.assertRaises(ValidationError):MemberResponse.model_validate({'members':[{**old,'basis':'旧依据'}]})
        with self.assertRaises(ValidationError):MemberResponse.model_validate({'members':[{**self.row,'basis':'额外字段'}]})

    def test_pack_uses_stage_one_catalog_and_crop(self):
        page={'id':'p','file':'drawing.pdf','page':1,'width_pt':800,'height_pt':600,
              'texts':[{'id':'t1','text':'HEA240','bbox':[.1,.1,.2,.2]},
                       {'id':'t2','text':'HEA300','bbox':[.6,.1,.7,.2]}], 'vectors':[]}
        bundle=make_input(page,self.view,self.one)
        self.assertEqual(bundle['current_view_tag'],'EL.1.000(TOS)')
        self.assertEqual(bundle['profile_catalog'],self.one['profile_catalog'])
        self.assertEqual([t['id'] for t in bundle['native_evidence']['texts']],['t1'])
        self.assertEqual(bundle['native_evidence']['crop_bbox'],self.view['bbox'])
        self.assertNotIn('profile_labels',bundle)
        from pathlib import Path
        guidance=(Path(__file__).parents[1]/'app/prompts/stage_two_members.md').read_text(encoding='utf-8')
        self.assertTrue(recognition_prompt(bundle).startswith(guidance+'\n'))

    def test_prompt_contains_only_current_view_evidence_and_catalogs(self):
        page={'id':'p','texts':[{'id':'inside','text':'HEA240','bbox':[.1,.1,.2,.2]},
                              {'id':'outside','text':'OUTSIDE_SECRET','bbox':[.6,.1,.7,.2]}],
              'vectors':[{'id':'line-in','bbox':[.1,.1,.2,.2],'points':[[.1,.1],[.2,.2]],'style':{'width':.5,'dashes':'[] 0'}},
                         {'id':'line-out','bbox':[.7,.1,.8,.2],'points':[[.7,.1],[.8,.2]],'style':{}}]}
        bundle=make_input(page,self.view,self.one)
        bundle.update(texts=['TEXT_SECRET'],profile_labels=['LABEL_SECRET'])
        text=recognition_prompt(bundle)
        import json
        context=json.loads(text.split('输入包：\n',1)[1])
        self.assertEqual(context['profile_catalog'],self.one['profile_catalog'])
        self.assertEqual([v[0] for v in context['native_evidence']['vectors']],['line-in'])
        self.assertIn('HEA240',text)
        for forbidden in ('OUTSIDE_SECRET','line-out','TEXT_SECRET','LABEL_SECRET'):
            self.assertNotIn(forbidden,text)

    def test_at_suffix_tags_follow_catalog_identity(self):
        row = validate_rows([{**self.row, 'view_tags':['EL.1.000(TOS)','elA']}], self.view, self.one)[0]
        self.assertEqual(row['checks'], [])
        self.assertIn('EL.1.000(TOS)', row['view_tags'])
        tags = allowed_model_tags({'view_catalog':[{'tag':self.view['title'],'kind':'PLAN'}],
                                   'axis_catalog':{'current_axes':['GRID-Q/6']}})
        self.assertEqual(set(tags), {'EL.1.000(TOS)', 'GRID-Q/6'})

    def test_vector_context_reports_truncation_without_losing_total(self):
        vectors=[{'id':f'v{i}','bbox':[.1,.1,.2,.2],
                  'points':[[.1,.1],[.2,.2]],'style':{}} for i in range(1601)]
        bundle=make_input({'id':'p','texts':[],'vectors':vectors},self.view,self.one)
        evidence=bundle['native_evidence']
        self.assertEqual(evidence['vector_total'],1601)
        self.assertEqual(len(evidence['vectors']),1600)
        self.assertTrue(evidence['vector_context_truncated'])

    def test_colliding_short_tags_are_not_guessed(self):
        one = {**self.one, 'views':self.one['views'] + [
            {**self.view, 'id':'plan2', 'title':'FOUNDATION PLAN AT EL.1.000(TOS)'}]}
        row = validate_rows([{**self.row,'view_tags':['EL.1.000(TOS)','elA']}], self.view, one)[0]
        self.assertTrue(any('对应多个目录项' in check for check in row['checks']))
        self.assertIn('EL.1.000(TOS)', row['view_tags'])

    def test_cross_view_records_remain_independent_and_tags_sorted(self):
        a=validate_rows([self.row],self.view,self.one)[0]
        view={**self.view,'id':'elA'}
        b=validate_rows([{**self.row,'view_tags':['plan1','elA']}],view,self.one)[0]
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(a['view_tags'],b['view_tags'])
        self.assertEqual(a['member_cnt'],2)
        self.assertEqual(a['checks'],[])

    def test_splices_separate_lengths_and_assembly_not_expanded(self):
        rows=validate_rows([{**self.row,'member_cnt':1,'length_mm':3000,'location':'A轴至中部OF SPLICE'},
                            {**self.row,'member_cnt':1,'length_mm':3000,'location':'中部OF SPLICE至B轴'},
                            {**self.row,'spec':'2LF-L100*10','member_cnt':1}],self.view,self.one)
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[-1]['member_cnt'],1)
        ledger=validate_ledger({**self.one,'view_members':rows})
        self.assertEqual(ledger['length_catalog'][1]['member_cnt'],2)
        self.assertTrue(all(not r['deduplicated'] for r in ledger['length_catalog']))

    def test_missing_lengths_bad_tags_and_catalog_are_pending(self):
        row=validate_rows([{**self.row,'length_mm':None,'view_tags':['iso','unknown'],'spec':'HEA999',
                            'issue':'标尺不完整'}],self.view,self.one)[0]
        self.assertGreaterEqual(len(row['checks']),4)
        self.assertEqual(row['length_mm'],None)
        corrected={**row,'spec':'HEA240','length_mm':6000,'view_tags':['plan1','elA'],'issue':None}
        self.assertEqual(validate_ledger({**self.one,'view_members':[corrected]})['view_members'][0]['checks'],[])

    def test_duplicate_identity_rejected(self):
        row=validate_rows([self.row],self.view,self.one)[0]
        with self.assertRaises(ValueError):validate_ledger({**self.one,'view_members':[row,row]})

    def test_diagonal_location_does_not_require_a_formula(self):
        row={**self.row,'location':'A-B轴之间、1-2轴间的斜撑','length_mm':5000}
        good=validate_rows([row],self.view,self.one)[0]
        self.assertEqual(good['checks'],[])
        self.assertEqual(good['length_mm'],5000)
        self.assertNotIn('length_math_check',good)
        unknown=validate_rows([{**row,'length_mm':None,'issue':'斜撑分跨尺寸无法确认'}],self.view,self.one)[0]
        self.assertIsNone(unknown['length_mm'])
        self.assertIn('斜撑分跨尺寸无法确认',unknown['checks'])

    def test_old_basis_is_retained_without_inventing_a_location(self):
        old={k:v for k,v in self.row.items() if k!='location'}
        old['basis']='6000中部拼接，长度6000/2'
        saved=validate_ledger({**self.one,'view_members':[{**old,'id':'old','source_view':self.view['id']}]})
        row=saved['view_members'][0]
        self.assertEqual(row['legacy_basis'],old['basis'])
        self.assertEqual(row['location'],'')
        self.assertTrue(any('位置说明' in check for check in row['checks']))
        row['location']='A轴至拼接点'
        edited=validate_ledger(saved)['view_members'][0]
        self.assertEqual(edited['legacy_basis'],old['basis'])
        self.assertEqual(edited['checks'],[])

    def test_user_bottom_beam_has_plan_and_alignment_names(self):
        plan={**self.view,'title':'PLAN AT EL.107.500(TOS)'}
        elevation={'id':'el1','kind':'ELEVATION','title':'ELEVATION ON ALIGNMENT 302003-PR001I/1',
                   'fixed_label':'302003-PR001I/1'}
        one={'views':[plan,elevation],'profile_catalog':['HEA260']}
        raw={**self.row,'spec':'HEA260','member_cnt':1,'view_tags':[plan['title'],elevation['fixed_label']],
             'location':'最下面的梁位于1轴线'}
        result=validate_rows([raw],plan,one)[0]
        self.assertEqual(result['view_tags'],['302003-PR001I/1','EL.107.500(TOS)'])
        self.assertEqual(result['source_view'],'plan1')
        self.assertEqual(result['checks'],[])
        legacy=validate_rows([{**raw,'view_tags':['plan1','el1']}],plan,one)[0]
        self.assertEqual(legacy['view_tags'],result['view_tags'])
        page={'id':'p','file':'drawing.pdf','page':1,'width_pt':800,'height_pt':600,'texts':[],'vectors':[]}
        bundle=make_input(page,plan,one)
        self.assertEqual(bundle['current_view_tag'],'EL.107.500(TOS)')
        self.assertEqual(bundle['view_catalog'][1]['tag'],'302003-PR001I/1')

    def test_source_drawing_plane_is_added_to_axis_only_model_tags(self):
        from app.member_recognition import ImageMemberResponse
        plan={**self.view,'title':'PLAN AT EL.107.500(TOS)'}
        elevation={'id':'el1','page_id':'p','kind':'ELEVATION',
                   'title':'ELEVATION ON ALIGNMENT 302003-PR001I/A',
                   'fixed_label':'302003-PR001I/A'}
        one={'views':[plan,elevation],'profile_catalog':['HEA240']}
        model_row={'spec':'HEA240','view_tags':['EL.107.500(TOS)'],
                   'member_cnt':1,'length':{'method':'DIRECT','direct_mm':4000,
                   'horizontal_mm':None,'vertical_mm':None,'evidence':'标尺4000'},
                   'location':'标高107.500横梁','issue':None}
        parsed=ImageMemberResponse.model_validate({'members':[model_row]}).model_dump()['members'][0]
        row=validate_rows([parsed],elevation,one)[0]
        self.assertEqual(row['view_tags'],['302003-PR001I/A','EL.107.500(TOS)'])
        self.assertNotIn('标签未包含当前来源视图',row['checks'])
        empty=ImageMemberResponse.model_validate({'members':[{**model_row,'view_tags':[]}]}).model_dump()['members'][0]
        row=validate_rows([empty],elevation,one)[0]
        self.assertEqual(row['view_tags'],['302003-PR001I/A'])
        plan_row=validate_rows([empty],plan,one)[0]
        self.assertEqual(plan_row['view_tags'],['EL.107.500(TOS)'])
        repeated=validate_rows([{**parsed,'view_tags':[elevation['title'],elevation['fixed_label']]}],elevation,one)[0]
        self.assertEqual(repeated['view_tags'],['302003-PR001I/A'])

    def test_response_normalizes_titles_and_deduplicates_only_tags(self):
        from app.pipeline import parse_model_content
        from unittest.mock import Mock
        import json
        audit=Mock()
        tags=[' PLAN AT EL.107.500(TOS) ','EL.107.500(TOS)',
              'ELEVATION ON ALIGNMENT PROJECT-Q/A','PROJECT-Q/A',
              'plan at EL.107.500(BOB)']
        raw={**self.row,'view_tags':tags}
        result=parse_model_content(json.dumps({'members':[raw,raw]}),MemberResponse,{'id':'v'},audit)
        self.assertEqual(len(result['members']),2)
        self.assertEqual(result['members'][0]['member_cnt'],2)
        self.assertEqual(result['members'][0]['view_tags'],['EL.107.500(BOB)','EL.107.500(TOS)','PROJECT-Q/A'])
        self.assertEqual(tags[0],' PLAN AT EL.107.500(TOS) ')
        self.assertTrue(any(call.args[0]=='MODEL_VIEW_TAGS_NORMALIZED' for call in audit.event.call_args_list))
        again=MemberResponse.model_validate(result).model_dump()
        self.assertEqual(again,result)

    def test_title_aliases_on_edits_stay_canonical_and_unknown_names_stay_pending(self):
        raw={**self.row,'view_tags':[self.view['title'],'EL.1.000(TOS)',
                                   'ELEVATION ON ALIGNMENT UNKNOWN/Z']}
        row=validate_rows([raw],self.view,self.one)[0]
        self.assertEqual(row['view_tags'],['EL.1.000(TOS)','UNKNOWN/Z'])
        self.assertTrue(any('目录外' in check for check in row['checks']))
        saved=validate_ledger({**self.one,'view_members':[row]})['view_members'][0]
        self.assertEqual(saved['view_tags'],row['view_tags'])
        self.assertEqual(saved['member_cnt'],row['member_cnt'])

    def test_empty_plane_names_are_rejected(self):
        with self.assertRaises(ValidationError):
            MemberResponse.model_validate({'members':[{**self.row,'view_tags':['  ']}]})

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
            self.assertEqual(client.chat.completions.create.call_args.kwargs['messages'][1]['content'][0]['type'],'image_url')
            self.assertTrue(target.with_suffix('.invalid.json').is_file())
            client.chat.completions.create.side_effect=[response('{}'),response('{}')]
            with patch('app.pipeline.render_data_uri',return_value='data:image/png;base64,test'),self.assertRaises(ValidationError):
                model_call(client,'test','recognize',{'id':'plan1','page':1,'source_path':str(source)},target,MemberResponse)
            self.assertEqual(client.chat.completions.create.call_count,4)

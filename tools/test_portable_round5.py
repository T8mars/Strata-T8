"""Fifth audit: nested request payloads and configuration completion."""
import base64
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import portable
from serve.frontend import ChatTemplate, openai_to_messages
from serve.responses import input_messages, request_tools, ENCRYPTED_PREFIX
from serve.server import ByteTokenizer, MockEngine, Service, StrataEngine, serve


class RequestPayloads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tok=ByteTokenizer()
        cls.svc=Service(MockEngine(tok, '</think>\n\nhello', max_context=16384), tok,
                        ChatTemplate(Path(__file__).resolve().parents[1]/'serve/chat_template.jinja'))
        cls.httpd=serve(cls.svc,port=0)
        cls.port=cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()

    def post(self, value, path='/v1/chat/completions'):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        try:
            conn.request('POST',path,json.dumps(value).encode(),{'Content-Type':'application/json'})
            response=conn.getresponse(); data=response.read()
            return response.status,data
        finally: conn.close()

    def body(self,**extra):
        return {'messages':[{'role':'user','content':'hi'}],'max_tokens':32,**extra}

    def rejected(self,value,path='/v1/chat/completions'):
        with mock.patch.object(self.svc,'load',wraps=self.svc.load) as load:
            status,data=self.post(value,path)
            self.assertEqual(status,400,data); self.assertIn('error',json.loads(data)); load.assert_not_called()

    def test_r04_opaque_reasoning_requires_valid_string_text(self):
        for raw in ('{"text":[1]}','{"text":"\\ud800"}','{"text":"a","text":"b"}','{"text":NaN}'):
            enc=ENCRYPTED_PREFIX+base64.b64encode(raw.encode()).decode()
            with self.subTest(raw=raw):
                self.rejected({'input':[{'role':'user','content':'q'},{'type':'reasoning','encrypted_content':enc},
                                         {'role':'assistant','content':'a'}]},'/v1/responses')

    def test_r04_plain_reasoning_content_is_checked_before_load(self):
        for content in (1,[{'type':'reasoning_text','text':1}]):
            with self.subTest(content=content):
                self.rejected({'input':[{'type':'reasoning','content':content}]},'/v1/responses')

    def test_r05_double_encoded_messages_remain_strict_json(self):
        for encoded in ('[{"role":"user","content":"\\ud800"}]',
                        '[{"role":"user","content":"a","content":"b"}]',
                        '[{"role":"user","content":"a","extra":NaN}]'):
            with self.subTest(encoded=encoded): self.rejected(self.body(messages=encoded))

    def test_r05_tool_arguments_cannot_hide_invalid_json_values(self):
        for args in ('{"x":"\\ud800"}','{"x":NaN}','{"x":1,"x":2}'):
            with self.subTest(args=args):
                self.rejected(self.body(messages=[{'role':'user','content':'q'},
                    {'role':'assistant','content':None,'tool_calls':[{'function':{'name':'f','arguments':args}}]},
                    {'role':'tool','content':'ok'}]))
                self.rejected({'input':[{'role':'user','content':'q'},
                    {'type':'function_call','name':'f','arguments':args},
                    {'type':'function_call_output','output':'ok'}]},'/v1/responses')

    def test_r05_deep_json_is_rejected_before_recursive_template_walks(self):
        encoded='{"x":'*600+'0'+'}'*600
        self.rejected(self.body(messages=[{'role':'user','content':'q'},
            {'role':'assistant','content':None,'tool_calls':[{'function':{'name':'f','arguments':encoded}}]}]))
        self.rejected({'input':[{'role':'user','content':'q'},
            {'type':'function_call','name':'f','arguments':encoded}]},'/v1/responses')
        self.rejected(self.body(extra=json.loads(encoded)))

    def test_r06_call_identifiers_have_a_stable_type(self):
        for kind in ('function_call','function_call_output'):
            for cid in ([1],{'key':1},9):
                with self.subTest(kind=kind,cid=cid):
                    item={'type':kind,'call_id':cid,'name':'f','arguments':'{}','output':'ok'}
                    self.rejected({'input':[{'role':'user','content':'q'},item,
                                             {'type':'function_call_output','output':'ok'}]},'/v1/responses')

    def test_r06_optional_call_identifiers_and_out_of_order_results_work(self):
        items=[{'role':'user','content':'q'}, {'type':'function_call','name':'f','call_id':'one'},
               {'type':'function_call','name':'g','call_id':'two'},
               {'type':'function_call_output','call_id':'two','output':'2'},
               {'type':'function_call_output','call_id':'one','output':'1'}]
        self.assertEqual([m['content'] for m in input_messages({'input':items})[-2:]],['1','2'])

    def test_r07_sampling_integer_overflow_is_rejected_before_load(self):
        for key in ('temperature','top_p','min_p','frequency_penalty','presence_penalty','repetition_penalty'):
            with self.subTest(key=key):
                self.rejected(self.body(**{key:10**1000}))
                self.rejected({'input':'q',key:10**1000},'/v1/responses')
        self.rejected(self.body(strata_tune={'pcie_frac':10**1000}),'/v1/messages')

    def test_r07_native_sampling_clamping_contract_is_preserved(self):
        for value in (0,65,1000): self.assertIn('top_k=64',StrataEngine.sampling_keys({'top_k':value}))
        self.assertIn('temperature=0.7',StrataEngine.sampling_keys({'temperature':.7}))

    def test_r08_openai_roles_are_validated_before_load(self):
        for role in (None,3,[], 'robot'):
            with self.subTest(role=role): self.rejected(self.body(messages=[{'role':role,'content':'q'}]))
        for value in ([], False):
            with self.subTest(template=value): self.rejected(self.body(chat_template_kwargs=value))

    def test_r08_developer_role_and_null_assistant_tool_turn_remain_valid(self):
        messages,_,_=openai_to_messages({'messages':[{'role':'developer','content':'policy'},
            {'role':'user','content':'q'}, {'role':'assistant','content':None,
            'tool_calls':[{'function':{'name':'f','arguments':'{}'}}]}]})
        self.assertEqual(messages[0]['role'],'system'); self.assertEqual(messages[2]['content'],'')

    def test_r09_text_leaves_reject_objects_and_numbers(self):
        for text in (7,{'unexpected':'object'},[1]):
            with self.subTest(text=text):
                self.rejected(self.body(messages=[{'role':'user','content':[{'type':'text','text':text}]}]))
                self.rejected(self.body(messages=[{'role':'user','content':[{'type':'text','text':text}]}]),'/v1/messages')
                self.rejected({'input':[{'role':'user','content':[{'type':'input_text','text':text}]}]},'/v1/responses')
        self.rejected(self.body(messages=[{'role':'assistant','content':[{'type':'thinking','thinking':3}]}]),'/v1/messages')
        for value in (0, {}):
            with self.subTest(reasoning=value):
                self.rejected(self.body(messages=[{'role':'user','content':'q'},
                    {'role':'assistant','content':'a','reasoning_content':value}]))

    def test_r09_mixed_content_keeps_valid_text_and_image_order(self):
        messages,_,_=openai_to_messages({'messages':[{'role':'user','content':[
            {'type':'text','text':'before'},{'type':'image_url','image_url':{'url':'data:image/png;base64,AA=='}},
            {'type':'text','text':'after'}]}]})
        self.assertEqual([p['type'] for p in messages[0]['content']],['text','image','text'])

    def test_r10_image_source_leaf_types_are_checked_before_load(self):
        for url in (9,[1],{'url':9}):
            with self.subTest(url=url):
                self.rejected(self.body(messages=[{'role':'user','content':[{'type':'image_url','image_url':url}]}]))
                self.rejected({'input':[{'role':'user','content':[{'type':'input_image','image_url':url}]}]},'/v1/responses')
        for source in ({'type':'base64','data':[1]}, {'type':'base64','data':'AA==','media_type':9},
                       {'type':'url','url':[1]}):
            with self.subTest(source=source):
                self.rejected(self.body(messages=[{'role':'user','content':[{'type':'image','source':source}]}]),'/v1/messages')

    def test_r11_namespace_metadata_and_members_are_validated(self):
        for namespace in ({'type':'namespace','name':[],'tools':[{'name':'f'}]},
                          {'type':'namespace','name':'n','description':3,'tools':[{'name':'f'}]},
                          {'type':'namespace','name':'n','tools':{}},
                          {'type':'namespace','name':'n','description':'n','tools':[{'name':'f','description':3}]}):
            with self.subTest(namespace=namespace):
                self.rejected({'input':'q','tools':[namespace]},'/v1/responses')

    def test_r11_flattened_tool_name_collisions_are_rejected(self):
        self.rejected({'input':'q','tools':[{'type':'function','name':'n.f'},
            {'type':'namespace','name':'n','tools':[{'name':'f'}]}]},'/v1/responses')
        self.assertEqual(request_tools({'tools':[{'type':'namespace','name':'n','tools':[{'name':'f'}]}]})[1],
                         {'n.f':('n','f','function'), 'n__f':('n','f','function')})

    def test_r12_stream_control_fields_have_explicit_types(self):
        for body in (self.body(stream='false'),self.body(stream_options=[1]),
                     self.body(stream_options={'include_usage':'false'})):
            with self.subTest(body=body): self.rejected(body)
        self.rejected({'input':'q','stream':1},'/v1/responses')
        self.rejected(self.body(stream='false'),'/v1/messages')

    def test_r12_valid_stream_returns_sse(self):
        status,data=self.post(self.body(stream=True,stream_options={'include_usage':True}))
        self.assertEqual(status,200); self.assertIn(b'data: [DONE]',data)

    def test_r14_explicit_zero_budget_overrides_shared_defaults(self):
        self.svc.shared={'max_tokens':1}
        try:
            for extra in ({'max_tokens':0},{'max_tokens':None,'max_completion_tokens':0}):
                with self.subTest(extra=extra):
                    status,data=self.post(self.body(**extra)); result=json.loads(data)
                    self.assertEqual(status,200,data)
                    self.assertEqual(result['choices'][0]['message']['content'],'hello')
                    self.assertGreater(result['usage']['completion_tokens'],1)
            self.assertEqual(self.svc.with_shared({'max_tokens':0},'anthropic')['max_tokens'],0)
        finally: self.svc.shared={}

    def test_r14_missing_budget_inherits_shared_default(self):
        self.svc.shared={'max_tokens':17}
        try:
            self.assertEqual(self.svc.with_shared({},'openai')['max_tokens'],17)
            self.assertEqual(self.svc.with_shared({'max_tokens':None},'openai')['max_tokens'],17)
        finally: self.svc.shared={}


class ConfigurationCompletion(unittest.TestCase):
    def invoke(self,generated):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); state=root/'portable-settings.json'; state.write_text('{"user":"keep"}')
            cfg=root/'strata-iq3_s.json'; before=state.read_bytes()
            def setup():
                if generated is not None: cfg.write_text(generated)
                state.write_text('{"setup":"changed"}'); return 0
            with mock.patch.object(portable,'ROOT',root),mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable,'model_delivery',return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
                 mock.patch.object(portable.upstream,'main',side_effect=setup), \
                 mock.patch.object(portable,'fingerprint',return_value={'version':'fixture'}), \
                 mock.patch.object(portable.upstream,'upgrade_config',side_effect=lambda p,c:c), \
                 mock.patch('sys.argv',['test']):
                with self.assertRaises((RuntimeError,ValueError)): portable.configure(root/'data')
            self.assertEqual(state.read_bytes(),before); self.assertFalse(cfg.exists())

    def test_r13_setup_success_without_run_configuration_rolls_back(self): self.invoke(None)
    def test_r13_setup_success_with_invalid_run_configuration_rolls_back(self): self.invoke('[]')

    def no_op(self, matching):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);state=root/'portable-settings.json';cfg=root/'strata-iq3_s.json'
            cfg.write_text('{"exe":"fixture.exe","args":["--data","OLD-DATA"]}')
            portable.save_json(state,{'portable_fingerprint':{'data':'OLD-DATA'}})
            before={p:p.read_bytes() for p in (cfg,state)}
            fingerprint={'data':'OLD-DATA' if matching else 'NEW-DATA'}
            with mock.patch.object(portable,'ROOT',root),mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable,'model_delivery',return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
                 mock.patch.object(portable.upstream,'main',return_value=0), \
                 mock.patch.object(portable,'fingerprint',return_value=fingerprint), \
                 mock.patch.object(portable.upstream,'upgrade_config',side_effect=lambda p,c:c), \
                 mock.patch('sys.argv',['test']):
                if matching:
                    self.assertEqual(portable.configure(root/'data')[0],0)
                    self.assertEqual(portable.read_json(state)['portable_fingerprint'],fingerprint)
                else:
                    with self.assertRaises(RuntimeError): portable.configure(root/'data')
                    for path, content in before.items(): self.assertEqual(path.read_bytes(),content)

    def test_r13_unchanged_old_configuration_cannot_mark_a_new_data_folder_ready(self): self.no_op(False)
    def test_r13_same_model_no_op_configuration_is_allowed(self): self.no_op(True)

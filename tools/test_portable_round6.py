"""Sixth audit: tool schemas, history conversion and generated tool arguments."""
import http.client
import json
from pathlib import Path
import unittest
from unittest import mock

from serve.frontend import (ChatTemplate, OutputParser, anthropic_to_messages,
                            parse_tool_call)
from serve.server import ByteTokenizer, MockEngine, Service, serve
from serve.structured import StructuredOutputError


def call(value='1'):
    return '<function=f>\n<parameter=x>\n'+value+'\n</parameter>\n</function>'


class ToolRequests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tok = ByteTokenizer()
        cls.svc = Service(MockEngine(tok, '</think>\n\nhello'), tok,
                          ChatTemplate(Path(__file__).resolve().parents[1]/'serve/chat_template.jinja'))
        cls.httpd = serve(cls.svc, port=0)
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, body, path='/v1/chat/completions'):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request('POST', path, json.dumps(body).encode(), {'Content-Type':'application/json'})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def reject(self, body, path='/v1/chat/completions'):
        with mock.patch.object(self.svc, 'load', wraps=self.svc.load) as load:
            status, data = self.post(body, path)
            self.assertEqual(status, 400, data)
            self.assertIn('error', json.loads(data))
            load.assert_not_called()

    def body(self, **extra):
        return {'messages':[{'role':'user','content':'hi'}], 'max_tokens':32, **extra}

    def test_r01_tool_schema_envelopes_are_checked_before_load(self):
        for params in (1, 'schema', [], {'properties':7}, {'properties':{'x':7}}):
            for path, tools in (
                ('/v1/chat/completions', [{'type':'function','function':{'name':'f','parameters':params}}]),
                ('/v1/messages', [{'name':'f','input_schema':params}]),
                ('/v1/responses', [{'type':'function','name':'f','parameters':params}])):
                with self.subTest(params=params, path=path):
                    self.reject({'input':'hi','tools':tools} if path.endswith('responses') else self.body(tools=tools), path)

    def test_r01_tool_descriptions_are_strings_before_load(self):
        for path, tools in (
            ('/v1/chat/completions', [{'name':'f','description':7}]),
            ('/v1/messages', [{'name':'f','description':7}])):
            with self.subTest(path=path): self.reject(self.body(tools=tools), path)

    def test_r03_history_tool_names_are_checked_before_load(self):
        for name in (None, '', 7, [], {}):
            with self.subTest(name=name):
                self.reject(self.body(messages=[{'role':'user','content':'hi'},
                    {'role':'assistant','content':None,'tool_calls':[{'function':{'name':name,'arguments':{}}}]}]))
                self.reject(self.body(messages=[{'role':'user','content':'hi'},
                    {'role':'assistant','content':[{'type':'tool_use','name':name,'input':{}}]}]), '/v1/messages')

    def test_r03_anthropic_tool_inputs_are_objects_before_load(self):
        for args in (0, False, [], 7, 'value'):
            with self.subTest(args=args):
                self.reject(self.body(messages=[{'role':'user','content':'hi'},
                    {'role':'assistant','content':[{'type':'tool_use','name':'f','input':args}]}]), '/v1/messages')

    def test_r04_anthropic_roles_are_checked_before_load(self):
        for role in ('robot', '', 7):
            with self.subTest(role=role):
                self.reject(self.body(messages=[{'role':role,'content':'hi'}]), '/v1/messages')

    def test_r04_falsey_anthropic_content_is_not_silently_discarded(self):
        for value in (0, False, {}):
            with self.subTest(value=value):
                self.reject(self.body(messages=[{'role':'user','content':value}]), '/v1/messages')

    def test_r05_tool_result_and_images_survive_the_same_user_turn(self):
        content = [{'type':'tool_result','tool_use_id':'one','content':'done'},
                   {'type':'text','text':'look'},
                   {'type':'image','source':{'type':'base64','media_type':'image/png','data':'AA=='}},
                   {'type':'text','text':'after'}]
        messages, _, _ = anthropic_to_messages({'messages':[{'role':'user','content':content}]})
        self.assertEqual(messages[0], {'role':'tool','content':'done'})
        self.assertEqual([p['type'] for p in messages[1]['content']], ['text','image','text'])
        self.assertEqual(messages[1]['content'][1]['source'], 'data:image/png;base64,AA==')

    def test_r05_tool_result_embedded_image_is_preserved(self):
        messages, _, _ = anthropic_to_messages({'messages':[{'role':'user','content':[
            {'type':'tool_result','content':[{'type':'text','text':'screenshot'},
                {'type':'image','source':{'type':'url','url':'https://example.test/image.png'}}]}]}]})
        self.assertEqual(messages[0]['content'][1], {'type':'image','source':'https://example.test/image.png'})

    def test_r05_bad_image_leaves_with_tool_results_are_not_ignored(self):
        self.reject(self.body(messages=[{'role':'user','content':[
            {'type':'tool_result','content':'done'},
            {'type':'image','source':{'type':'base64','data':[1]}}]}]), '/v1/messages')

    def test_r06_custom_history_input_requires_a_string(self):
        for value in (0, False, {}, []):
            with self.subTest(value=value):
                self.reject({'input':[{'role':'user','content':'hi'},
                    {'type':'custom_tool_call','name':'f','input':value}]}, '/v1/responses')

    def test_r06_history_namespace_cannot_hide_falsey_non_strings(self):
        for value in (0, False, {}, []):
            with self.subTest(value=value):
                self.reject({'input':[{'role':'user','content':'hi'},
                    {'type':'function_call','name':'f','arguments':'{}','namespace':value}]}, '/v1/responses')

    def test_r09_valid_minimal_tools_and_count_tokens_need_no_model(self):
        with mock.patch.object(self.svc,'load', wraps=self.svc.load) as load:
            status, data = self.post(self.body(tools=[{'name':'f','input_schema':{'properties':{'x':True}}}]),
                                     '/v1/messages/count_tokens')
            self.assertEqual(status, 200, data)
            self.assertGreater(json.loads(data)['input_tokens'], 0)
            load.assert_not_called()

    def test_r02_and_r07_tool_calls_have_valid_wire_json_on_all_apis(self):
        original = self.svc.engine
        try:
            for value in ('NaN', '1'):
                self.svc.engine = MockEngine(self.svc.tok, '</think>\n\n<tool_call>\n'+call(value)+'\n</tool_call>')
                params = {'properties':{'x':True}}
                for path, tools in (
                    ('/v1/chat/completions', [{'type':'function','function':{'name':'f','parameters':params}}]),
                    ('/v1/messages', [{'name':'f','input_schema':params}]),
                    ('/v1/responses', [{'type':'function','name':'f','parameters':params}])):
                    for stream in (False, True):
                        with self.subTest(value=value, path=path, stream=stream):
                            body = {'input':'hi','tools':tools,'stream':stream} if path.endswith('responses') else self.body(tools=tools,stream=stream,max_tokens=128)
                            status, data = self.post(body, path)
                            self.assertEqual(status, 200, data)
                            payloads = [line[6:] for line in data.splitlines() if line.startswith(b'data: ') and line != b'data: [DONE]'] if stream else [data]
                            self.assertTrue(payloads, data)
                            decoded = [json.loads(payload, parse_constant=lambda x:self.fail('non-finite JSON: '+x)) for payload in payloads]
                            self.assertFalse(any('error' in item and item['error'] for item in decoded), data)
                            if path.endswith('messages'):
                                args = ''.join(item.get('delta',{}).get('partial_json','') for item in decoded) if stream else decoded[0]['content'][0]['input']
                            elif path.endswith('responses'):
                                args = ''.join(item.get('delta','') for item in decoded if item.get('type')=='response.function_call_arguments.delta') if stream else decoded[0]['output'][0]['arguments']
                            else:
                                args = ''.join(tc['function'].get('arguments','') for item in decoded for choice in item.get('choices',[]) for tc in choice.get('delta',{}).get('tool_calls',[])) if stream else decoded[0]['choices'][0]['message']['tool_calls'][0]['function']['arguments']
                            if isinstance(args,str):
                                args = json.loads(args, parse_constant=lambda x:self.fail('non-finite arguments: '+x))
                            self.assertEqual(args, {'x':'NaN' if value=='NaN' else 1})
        finally:
            self.svc.engine = original

    def test_r08_duplicate_generated_parameter_names_never_complete_a_call(self):
        original = self.svc.engine
        try:
            body = '<function=f>\n<parameter=x>\n1\n</parameter>\n<parameter=x>\n2\n</parameter>\n</function>'
            self.svc.engine = MockEngine(self.svc.tok, '</think>\n\n<tool_call>\n'+body+'\n</tool_call>')
            for path in ('/v1/chat/completions','/v1/messages','/v1/responses'):
                for stream in (False, True):
                    with self.subTest(path=path, stream=stream):
                        request = {'input':'hi','stream':stream} if path.endswith('responses') else self.body(stream=stream,max_tokens=128)
                        status, data = self.post(request,path)
                        self.assertEqual(status, 200 if stream else 502, data)
                        self.assertIn(b'duplicate parameter', data)
                        self.assertNotIn(b'"finish_reason": "tool_calls"', data)
                        self.assertNotIn(b'response.completed', data)
        finally:
            self.svc.engine = original


class GeneratedTools(unittest.TestCase):
    def stream_args(self, value, schema=None):
        parser = OutputParser(thinking=False, tools=[schema] if schema else None, stream_tools=True)
        events=[]
        for char in '<tool_call>\n'+call(value)+'\n</tool_call>':
            events.extend(parser.feed(char))
        events.extend(parser.finish())
        args = ''.join(e.text for e in events if e.kind=='tool_args')
        final = [e.call.arguments for e in events if e.kind=='tool_call']
        return json.loads(args, parse_constant=lambda value: self.fail('non-finite wire JSON: '+value)), final

    def test_r02_boolean_property_schemas_work_in_whole_calls(self):
        for prop in (True, False):
            with self.subTest(prop=prop):
                self.assertEqual(parse_tool_call(call(), {'name':'f','parameters':{'properties':{'x':prop}}}).arguments,
                                 {'x':1})

    def test_r02_boolean_property_schemas_work_in_streamed_calls(self):
        for prop in (True, False):
            with self.subTest(prop=prop):
                self.assertEqual(self.stream_args('1', {'name':'f','parameters':{'properties':{'x':prop}}}),
                                 ({'x':1}, [{'x':1}]))

    def test_r07_non_finite_generated_numbers_remain_raw_text(self):
        for value in ('NaN', 'Infinity', '-Infinity', '1e1000', '{"x":NaN}'):
            with self.subTest(value=value):
                self.assertEqual(parse_tool_call(call(value)).arguments, {'x':value})
                self.assertEqual(self.stream_args(value), ({'x':value}, [{'x':value}]))

    def test_r08_duplicate_or_deep_generated_objects_remain_raw_text(self):
        for value in ('{"x":1,"x":2}', '['*600+'0'+']'*600, '{"x":"\\ud800"}'):
            with self.subTest(value=value):
                self.assertEqual(parse_tool_call(call(value)).arguments, {'x':value})
                self.assertEqual(self.stream_args(value), ({'x':value}, [{'x':value}]))

    def test_r09_valid_declared_strings_and_nested_numbers_are_unchanged(self):
        schema = {'name':'f','description':None,'parameters':{'properties':{'x':{'type':'string'}}}}
        self.assertEqual(self.stream_args('NaN', schema), ({'x':'NaN'}, [{'x':'NaN'}]))
        self.assertEqual(self.stream_args('{"a":[1,true,null]}'),
                         ({'x':{'a':[1,True,None]}}, [{'x':{'a':[1,True,None]}}]))

    def test_r08_duplicate_xml_parameter_names_fail_whole_and_stream(self):
        body='<function=f>\n<parameter=x>\n1\n</parameter>\n<parameter=x>\n2\n</parameter>\n</function>'
        with self.assertRaisesRegex(StructuredOutputError,'duplicate parameter'):
            parse_tool_call(body)
        for chunk_size in (1, len(body)+100):
            parser=OutputParser(thinking=False,stream_tools=True)
            events=[]
            text='<tool_call>\n'+body+'\n</tool_call>'
            with self.subTest(chunk_size=chunk_size), self.assertRaisesRegex(StructuredOutputError,'duplicate parameter'):
                for index in range(0,len(text),chunk_size):
                    events.extend(parser.feed(text[index:index+chunk_size]))
            self.assertFalse(any(e.kind=='tool_call' for e in events))


if __name__ == '__main__':
    unittest.main()

"""Seventh audit: tool identity/finalization and bounded image sources."""
import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from serve.frontend import ChatTemplate, OutputParser, parse_tool_call
from serve.server import ByteTokenizer, MockEngine, Service, Vision, RequestCancelled, serve
from serve import server
from serve.structured import StructuredOutputError


def body(name='f', value='1', parameter='x'):
    return f'<function={name}>\n<parameter={parameter}>\n{value}\n</parameter>\n</function>'


def events(text, tools=None, step=1):
    parser = OutputParser(thinking=False, tools=tools, stream_tools=True)
    result = []
    for index in range(0, len(text), step):
        result.extend(parser.feed(text[index:index+step]))
    result.extend(parser.finish())
    return result


class ToolRequests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tok = ByteTokenizer()
        cls.svc = Service(MockEngine(tok, '</think>\n\nhello'), tok,
                          ChatTemplate(Path(__file__).resolve().parents[1]/'serve/chat_template.jinja'))
        cls.httpd = serve(cls.svc, port=0)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, path, request):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=5)
        try:
            conn.request('POST', path, json.dumps(request).encode(), {'Content-Type':'application/json'})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_r01_duplicate_openai_tool_names_rejected_before_load(self):
        for tools in ([{'name':'f'}, {'name':'f'}],
                      [{'type':'function','function':{'name':'f'}}, {'name':'f'}]):
            with self.subTest(tools=tools), mock.patch.object(self.svc,'load') as load:
                status, data = self.post('/v1/chat/completions', {'messages':[{'role':'user','content':'hi'}],'tools':tools})
                self.assertEqual(status, 400, data)
                self.assertIn(b'duplicate', data)
                load.assert_not_called()

    def test_r01_duplicate_anthropic_tool_names_rejected_before_load(self):
        with mock.patch.object(self.svc,'load') as load:
            status, data = self.post('/v1/messages', {'messages':[{'role':'user','content':'hi'}],
                                                     'tools':[{'name':'f'},{'name':'f'}]})
            self.assertEqual(status, 400, data)
            self.assertIn(b'duplicate', data)
            load.assert_not_called()

    def test_r02_empty_generated_name_is_a_model_failure_on_all_apis(self):
        original = self.svc.engine
        try:
            self.svc.engine = MockEngine(self.svc.tok, '</think>\n\n<tool_call>\n'+body('')+'\n</tool_call>')
            for path in ('/v1/chat/completions','/v1/messages','/v1/responses'):
                for stream in (False, True):
                    with self.subTest(path=path,stream=stream):
                        req = {'input':'hi'} if path.endswith('responses') else {'messages':[{'role':'user','content':'hi'}]}
                        status, data = self.post(path,dict(req,stream=stream,max_tokens=256))
                        self.assertEqual(status, 200 if stream else 502, data)
                        self.assertIn(b'structured_output_failed', data)
                        self.assertNotIn(b'response.completed',data)
                        self.assertNotIn(b'"finish_reason": "tool_calls"',data)
        finally:
            self.svc.engine = original

    def test_r03_custom_tool_input_survives_missing_outer_tag_on_wire(self):
        original = self.svc.engine
        try:
            value = 'print("你好")\nprint(2)'
            self.svc.engine = MockEngine(self.svc.tok, '</think>\n\n<tool_call>\n'+body('script',value,'input'))
            for stream in (False,True):
                with self.subTest(stream=stream):
                    status, data = self.post('/v1/responses',{'input':'hi','tools':[{'type':'custom','name':'script'}],
                                                            'stream':stream,'max_tokens':512})
                    self.assertEqual(status,200,data)
                    decoded = [json.loads(line[6:]) for line in data.splitlines() if line.startswith(b'data: {')] if stream else [json.loads(data)]
                    response = next(item['response'] for item in decoded if item.get('type')=='response.completed') if stream else decoded[0]
                    item = response['output'][0]
                    self.assertEqual(item['type'],'custom_tool_call')
                    self.assertEqual(item['input'],value)
                    if stream:
                        self.assertEqual(''.join(item.get('delta','') for item in decoded if item.get('type')=='response.custom_tool_call_input.delta'),value)
        finally:
            self.svc.engine = original


class GeneratedTools(unittest.TestCase):
    def test_r02_invalid_generated_names_fail_whole_and_incremental(self):
        for name in ('',' \t','\ud800'):
            with self.subTest(name=repr(name)):
                with self.assertRaises(StructuredOutputError): parse_tool_call(body(name))
                for step in (1,10000):
                    with self.assertRaises(StructuredOutputError): events('<tool_call>\n'+body(name)+'\n</tool_call>',step=step)

    def test_r03_final_arguments_match_stream_when_outer_tag_is_missing(self):
        for step in (1,7,10000):
            with self.subTest(step=step):
                result = events('<tool_call>\n'+body(value='{"nested":[1,true]}'),step=step)
                args = json.loads(''.join(e.text for e in result if e.kind=='tool_args'))
                final = [e.call for e in result if e.kind=='tool_call']
                self.assertEqual(len(final),1)
                self.assertEqual(final[0].arguments,args)
                self.assertEqual(args,{'x':{'nested':[1,True]}})

    def test_r03_final_id_matches_announced_call_without_outer_tag(self):
        result=events('<tool_call>\n'+body())
        start=next(e.call for e in result if e.kind=='tool_start')
        final=next(e.call for e in result if e.kind=='tool_call')
        self.assertEqual(final.id,start.id)
        self.assertEqual(final.arguments,{'x':1})

    def test_r03_string_schema_retained_in_final_call_without_outer_tag(self):
        tools=[{'name':'f','parameters':{'properties':{'x':{'type':'string'}}}}]
        result=events('<tool_call>\n'+body(value='false'),tools)
        self.assertEqual(next(e.call.arguments for e in result if e.kind=='tool_call'),{'x':'false'})

    def test_r03_parameters_after_function_end_never_complete_a_call(self):
        text=body()+'\n<parameter=y>\n2\n</parameter>'
        with self.assertRaisesRegex(StructuredOutputError,'function end'): parse_tool_call(text)
        for suffix in ('\n</tool_call>',''):
            for step in (1,10000):
                with self.subTest(suffix=suffix,step=step), self.assertRaisesRegex(StructuredOutputError,'function end'):
                    events('<tool_call>\n'+text+suffix,step=step)

    def test_r06_custom_script_literal_delimiters_and_unicode_are_preserved(self):
        value='print("</parameter> </tool_call> 你好")\nprint("\\n")'
        tools=[{'name':'script','parameters':{'properties':{'input':{'type':'string'}}}}]
        for suffix in ('\n</tool_call>',''):
            for step in (1,11,10000):
                with self.subTest(suffix=suffix,step=step):
                    result=events('<tool_call>\n'+body('script',value,'input')+suffix,tools,step)
                    self.assertEqual(json.loads(''.join(e.text for e in result if e.kind=='tool_args')),{'input':value})
                    self.assertEqual(next(e.call.arguments for e in result if e.kind=='tool_call'),{'input':value})

    def test_r07_custom_input_cut_before_function_end_never_completes(self):
        tools=[{'name':'script','parameters':{'properties':{'input':{'type':'string'}}}}]
        for tail in ('print(1)','print(1)\n</parameter>','print(1)\n</parameter>\n</funct'):
            with self.subTest(tail=tail):
                result=events('<tool_call>\n<function=script>\n<parameter=input>\n'+tail,tools)
                self.assertFalse(any(e.kind=='tool_call' for e in result))

    def test_r07_second_truncated_call_does_not_complete_with_first_arguments(self):
        text='<tool_call>\n'+body()+'\n</tool_call>\n<tool_call>\n<function=g>\n<parameter=x>\n2'
        result=events(text)
        self.assertEqual([(e.call.name,e.call.arguments) for e in result if e.kind=='tool_call'],[('f',{'x':1})])
        ids=[e.call.id for e in result if e.kind=='tool_start']
        self.assertEqual(len(ids),2)
        self.assertNotEqual(*ids)


class ImageSources(unittest.TestCase):
    def test_r04_data_uri_requires_a_separator(self):
        with self.assertRaisesRegex(ValueError,'image|data'):
            Vision.load('data:image/png;base64')

    def test_r04_data_uri_rejects_invalid_base64_characters(self):
        with self.assertRaisesRegex(ValueError,'image|base64'):
            Vision.load('data:image/png;base64,QU!JD')

    def test_r04_data_uri_requires_base64_encoding(self):
        with self.assertRaisesRegex(ValueError,'base64'):
            Vision.load('data:image/png,QUJD')

    def test_r05_data_uri_size_is_bounded_after_decode(self):
        with mock.patch.object(server,'MAX_IMAGE_BYTES',64,create=True):
            self.assertEqual(Vision.load('data:image/png;base64,'+base64.b64encode(b'x'*64).decode()),b'x'*64)
            with self.assertRaisesRegex(ValueError,'limit|large|bytes'):
                Vision.load('data:image/png;base64,'+base64.b64encode(b'x'*65).decode())

    def test_r05_local_file_reads_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(server,'MAX_IMAGE_BYTES',64,create=True):
            path=Path(temp)/'image.png'; path.write_bytes(b'x'*65)
            for source in (str(path),'file://'+str(path)):
                with self.subTest(source=source), self.assertRaisesRegex(ValueError,'limit|large|bytes'):
                    Vision.load(source)
            path.write_bytes(b'x'*64)
            self.assertEqual(Vision.load(str(path)),b'x'*64)

    def test_r05_http_source_is_bounded_with_and_without_content_length(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200)
                if self.path=='/length': self.send_header('Content-Length','65')
                self.end_headers(); self.wfile.write(b'x'*65)
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            with mock.patch.object(server,'MAX_IMAGE_BYTES',64,create=True):
                for tail in ('/length','/unknown'):
                    with self.subTest(tail=tail), self.assertRaisesRegex(ValueError,'limit|large|bytes'):
                        Vision.load(f'http://127.0.0.1:{httpd.server_address[1]}'+tail)
        finally:
            httpd.shutdown();httpd.server_close();thread.join(2)

    def test_r08_embeddings_cleanup_is_isolated_between_request_threads(self):
        tok=ByteTokenizer();svc=Service(MockEngine(tok,''),tok,ChatTemplate(Path(__file__).resolve().parents[1]/'serve/chat_template.jinja'))
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'req.sve';path.write_bytes(b'owner')
            svc.embeddings.path=path
            thread=threading.Thread(target=svc.discard_embeddings);thread.start();thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(path.read_bytes(),b'owner')
            svc.discard_embeddings()
            self.assertFalse(path.exists())
            self.assertIsNone(svc.embeddings.path)

    def test_r09_cancelled_encode_has_no_source_or_encoder_side_effect(self):
        vision=Vision.__new__(Vision);cancel=threading.Event();cancel.set()
        with mock.patch.object(Vision,'load') as load:
            with self.assertRaises(RequestCancelled): vision.encode('http://127.0.0.1/never',cancel)
            load.assert_not_called()

    def test_r09_cancellation_after_source_load_prevents_normalize_and_native_write(self):
        vision=Vision.__new__(Vision);cancel=threading.Event()
        def loaded(source): cancel.set();return b'data'
        with mock.patch.object(Vision,'load',side_effect=loaded),mock.patch.object(Vision,'normalize') as normalize:
            with self.assertRaises(RequestCancelled): vision.encode('data:image/png;base64,',cancel)
            normalize.assert_not_called()


if __name__=='__main__': unittest.main()

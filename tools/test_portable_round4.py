"""Fourth audit: HTTP framing, pre-load validation and isolated model profiles."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

import portable
from tools import managed_config, prepare_portable_model as prepare_model
from serve import structured
from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, MockEngine, Service, serve


class RequestBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tok = ByteTokenizer()
        cls.svc = Service(MockEngine(tok, '</think>\n\nok', max_context=16384), tok,
                          ChatTemplate(Path(__file__).resolve().parents[1]/'serve/chat_template.jinja'))
        cls.server = serve(cls.svc, port=0)
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def body(self, **extra):
        return {'messages':[{'role':'user','content':'hi'}], 'max_tokens':16, **extra}

    def post(self, value, path='/v1/chat/completions'):
        conn=http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request('POST',path,json.dumps(value).encode(),{'Content-Type':'application/json'})
            response=conn.getresponse()
            return response.status,json.loads(response.read())
        finally: conn.close()

    def raw(self, body, *, path='/v1/chat/completions', lengths=None, extra='', truncated=False):
        lengths=[len(body)] if lengths is None else lengths
        headers=''.join(f'Content-Length: {length}\r\n' for length in lengths)
        with socket.create_connection(('127.0.0.1',self.port),timeout=5) as conn:
            conn.sendall((f'POST {path} HTTP/1.0\r\nHost: 127.0.0.1:{self.port}\r\n'
                          f'Content-Type: application/json\r\n{headers}{extra}\r\n').encode()+body)
            if truncated: conn.shutdown(socket.SHUT_WR)
            result=b''
            while chunk:=conn.recv(65536): result+=chunk
        head,data=result.split(b'\r\n\r\n',1)
        return int(head.split()[1]),json.loads(data)

    def test_negative_duplicate_and_transfer_encoded_lengths_are_rejected_before_load(self):
        body=json.dumps(self.body()).encode()
        cases=[{'lengths':[-1],'truncated':True}, {'lengths':[len(body),len(body)]},
               {'extra':'Transfer-Encoding: chunked\r\n'}]
        for case in cases:
            with self.subTest(case=case), mock.patch.object(self.svc,'load', wraps=self.svc.load) as load:
                status,result=self.raw(body,**case)
                self.assertEqual(status,400,result); load.assert_not_called()

    def test_valid_json_in_a_truncated_request_cannot_generate(self):
        body=json.dumps(self.body()).encode()
        with mock.patch.object(self.svc,'load', wraps=self.svc.load) as load:
            status,result=self.raw(body,lengths=[len(body)+8],truncated=True)
        self.assertEqual(status,400,result); load.assert_not_called()

    def test_header_only_oversized_generation_and_settings_are_refused(self):
        for path,size in [('/v1/chat/completions',64*1024**2+1),('/settings',65537),('/config',65537)]:
            with self.subTest(path=path):
                status,result=self.raw(b'',path=path,lengths=[size],truncated=True)
                self.assertEqual(status,413,result)

    def test_invalid_json_constants_duplicates_and_overflow_are_rejected(self):
        body=json.dumps(self.body()).encode()
        variants=[body[:-1]+b',"temperature":NaN}',body[:-1]+b',"temperature":1e999}',
                  body[:-1]+b',"max_tokens":20}']
        for value in variants:
            with self.subTest(body=value), mock.patch.object(self.svc,'load', wraps=self.svc.load) as load:
                status,result=self.raw(value)
                self.assertEqual(status,400,result); load.assert_not_called()

    def test_excessive_json_nesting_returns_json_error_and_health_survives(self):
        body=json.dumps(self.body()).encode()[:-1]+b',"unused":'+b'['*4000+b'0'+b']'*4000+b'}'
        status,result=self.raw(body)
        self.assertEqual(status,400,result)
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            conn.request('GET','/health'); self.assertEqual(conn.getresponse().status,200)
        finally: conn.close()

    def test_unpaired_surrogate_is_rejected_before_model_loading(self):
        with mock.patch.object(self.svc,'load',wraps=self.svc.load) as load:
            status,result=self.post(self.body(messages=[{'role':'user','content':'\ud800'}]))
        self.assertEqual(status,400,result); load.assert_not_called()

    def test_invalid_token_budgets_fail_before_model_loading_in_all_apis(self):
        for path,key in [('/v1/chat/completions','max_tokens'),('/v1/chat/completions','max_completion_tokens'),
                         ('/v1/messages','max_tokens'),('/v1/responses','max_output_tokens')]:
            for value in ([],{},True,1.5,10**100):
                body=self.body(**{key:value}) if path!='/v1/responses' else {'input':'hi',key:value}
                with self.subTest(path=path,key=key,value=value), mock.patch.object(self.svc,'load',wraps=self.svc.load) as load:
                    status,result=self.post(body,path)
                    self.assertEqual(status,400,result); load.assert_not_called()

    def test_bad_anthropic_message_shape_fails_before_loading(self):
        with mock.patch.object(self.svc,'load',wraps=self.svc.load) as load:
            status,result=self.post({'messages':'wrong','max_tokens':16},'/v1/messages')
        self.assertEqual(status,400,result); load.assert_not_called()

    def test_bad_reasoning_budget_does_not_load_either_chat_api(self):
        for path in ('/v1/chat/completions','/v1/messages'):
            with self.subTest(path=path), mock.patch.object(self.svc,'load',wraps=self.svc.load) as load:
                status,result=self.post(self.body(reasoning_budget_tokens=[]),path)
                self.assertEqual(status,400,result); load.assert_not_called()

    def test_malformed_template_and_anthropic_blocks_are_request_errors(self):
        cases=[('/v1/chat/completions',self.body(chat_template_kwargs=['wrong'])),
               ('/v1/messages',self.body(messages=[{'content':'hi'}])),
               ('/v1/messages',self.body(messages=[{'role':'user','content':[1]}])),
               ('/v1/messages',self.body(messages=[{'role':'user','content':[{'type':'image','source':['wrong']}]}]))]
        for path,body in cases:
            with self.subTest(path=path,body=body), mock.patch.object(self.svc,'load',wraps=self.svc.load) as load:
                status,result=self.post(body,path); self.assertEqual(status,400,result); load.assert_not_called()

    def test_text_and_responses_positive_requests_keep_their_contract(self):
        for path,body in [('/v1/chat/completions',self.body()),('/v1/messages',self.body()),
                          ('/v1/responses',{'input':'hi','max_output_tokens':16})]:
            with self.subTest(path=path):
                status,result=self.post(body,path); self.assertEqual(status,200,result)


class OutputEncoding(unittest.TestCase):
    def test_unpaired_surrogate_in_model_json_is_a_structured_error(self):
        with self.assertRaises(structured.StructuredOutputError):
            structured.validated_json('{"value":"\\ud800"}', structured._ObjectOnly(), 'stop')

    def test_excessive_model_json_nesting_is_a_structured_error(self):
        with self.assertRaises(structured.StructuredOutputError):
            structured.validated_json('{"value":'+'['*4000+'0'+']'*4000+'}', structured._ObjectOnly(), 'stop')


class RefreshTransaction(unittest.TestCase):
    def test_failed_upgrade_state_commit_restores_configuration_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cfg=root/'strata-test.json'; state=root/'state.json'
            portable.save_json(cfg,{'args':['--custom','keep'],'exe':'old.exe'})
            portable.save_json(state,{'portable_fingerprint':{'version':'old'}})
            before={p:p.read_bytes() for p in (cfg,state)}
            real=portable.save_json
            def save(path,value):
                if path==state and value.get('portable_fingerprint',{}).get('version')=='new': raise OSError('state denied')
                real(path,value)
            with mock.patch.object(portable,'ROOT',root), mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable.upstream,'cuda_lib_dirs',return_value=[]), \
                 mock.patch.object(portable.upstream,'upgrade_config',side_effect=lambda p,c:c), \
                 mock.patch.object(portable,'save_json',side_effect=save):
                with self.assertRaises(OSError): portable.refresh_updated_config(cfg,portable.read_json(state),{'version':'new'})
            for p,content in before.items(): self.assertEqual(p.read_bytes(),content)


class ManagedProfiles(unittest.TestCase):
    def invoke(self,root,output, *, matching=False, context=32768, engine_args=None):
        base=root/'strata-iq3_s.json'; state=root/'portable-settings.json'
        config={'exe':'original.exe','args':engine_args or ['--native','fixture.gguf','--max-context','65536'],
                'host':'127.0.0.1','port':8084,'api_key':'fixture-secret'}
        portable.save_json(base,config); portable.save_json(state,{'portable_fingerprint':{'data':'original'}})
        before={p:p.read_bytes() for p in (base,state)}
        def setup():
            portable.upstream.write_setup_config(base,config)
            portable.upstream.save_settings({'setup':'changed'})
            portable.upstream.write_run_script('IQ3_S',base,8080,False)
            return 0
        def write_config(path,value,*args): portable.save_json(path,value)
        def save_settings(value): portable.save_json(state,value)
        def write_script(*args):
            path=root/'run-iq3_s.bat'; path.write_text('changed'); return path
        with mock.patch.object(portable,'ROOT',root), mock.patch.object(portable,'STATE',state), \
             mock.patch.object(portable,'environment_check'), mock.patch.object(portable,'isolate_setup'), \
             mock.patch.object(portable,'model_delivery',return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
             mock.patch.object(portable,'fingerprint',return_value={'data':'other'}), \
             mock.patch.object(portable,'same_machine',return_value=matching), \
             mock.patch.object(portable.upstream,'main',side_effect=setup), \
             mock.patch.object(portable.upstream,'write_setup_config',side_effect=write_config), \
             mock.patch.object(portable.upstream,'save_settings',side_effect=save_settings), \
             mock.patch.object(portable.upstream,'write_run_script',side_effect=write_script), \
             mock.patch.object(portable.upstream,'cuda_lib_dirs',return_value=[]), \
             mock.patch('sys.argv',['managed','--data-dir',str(root/'model'),'--output',str(output),'--vision','no','--context',str(context)]):
            result=managed_config.main()
        return result,before

    def test_other_model_profile_does_not_reconfigure_the_web_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'private/service.json'
            result,before=self.invoke(root,output)
            self.assertEqual(result,0); self.assertTrue(output.is_file())
            for path,content in before.items(): self.assertEqual(path.read_bytes(),content)
            self.assertFalse((root/'run-iq3_s.bat').exists())

    def test_invalid_context_is_rejected_before_environment_or_setup(self):
        for value in (0,-1,1023,131073):
            with self.subTest(context=value), mock.patch('sys.argv',['managed','--data-dir','unused','--output','unused','--context',str(value)]), \
                 mock.patch.object(portable,'environment_check') as environment, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result: managed_config.main()
                self.assertEqual(result.exception.code,2); environment.assert_not_called()

    def test_duplicate_batch_and_context_flags_are_removed_before_isolated_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'private/service.json'
            argv=['--native','fixture.gguf','--batch','4','--batch','8','--slots=2','--batch-groups','3',
                  '--max-context','65536','--max-context=131072','--custom','keep']
            self.invoke(root,output,matching=True,engine_args=argv)
            saved=portable.read_json(output)['args']
            self.assertEqual(saved,['--native','fixture.gguf','--custom','keep','--max-context','32768'])


class PreparedArtifacts(unittest.TestCase):
    def invoke(self,*,missing=False,create=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'app';root.mkdir();data=Path(tmp)/'data';pack=data/'packs/iq3_s'
            catalog={'repository':'fixture/model','official_revision':'rev','modelscope_revision':'rev','family':'qwen',
                     'model':'IQ3_S','files':[{'file':'main.gguf','size':1,'sha256':'0'*64}]}
            (root/'model-sources.json').write_text(json.dumps(catalog))
            names=('native_experts.txt','index.txt','dense.bin','tokenizer/vocab.json','tokenizer/chat_template.jinja')
            for name in names:
                path=pack/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            for name in ('experts.bin','dense.bin','dense.txt'):
                path=data/'mtp/rt'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            if missing: (pack/'dense.bin').unlink()
            def run(command,**kwargs):
                if create:
                    for name in names: (pack/name).write_text('rebuilt')
            with mock.patch.object(prepare_model,'ROOT',root), \
                 mock.patch.object(prepare_model.setup,'HF_REVISIONS',{'fixture/model':'rev'}), \
                 mock.patch.object(prepare_model,'download'), mock.patch.object(prepare_model.subprocess,'run',side_effect=run) as command, \
                 mock.patch('sys.argv',['prepare','--yes','--data-dir',str(data)]):
                if missing and not create:
                    with self.assertRaisesRegex(RuntimeError,'incomplete'): prepare_model.main()
                    self.assertFalse((data/'portable-model.json').exists())
                else:
                    prepare_model.main(); self.assertTrue((data/'portable-model.json').is_file())
            return command.call_args_list

    def test_missing_dense_pack_is_rebuilt_even_when_old_completion_markers_exist(self):
        commands=self.invoke(missing=True,create=True)
        self.assertEqual(len(commands),1); self.assertIn('iq_pack.py',str(commands[0].args[0]))

    def test_successful_subprocess_without_artifacts_cannot_publish_model_ready(self):
        self.invoke(missing=True)

    def test_complete_pack_is_reused_without_rebuilding(self):
        self.assertEqual(self.invoke(),[])


if __name__=='__main__': unittest.main()

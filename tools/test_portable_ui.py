"""Real loopback setup HTTP, offline model checks and owned subprocess lifetime."""
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import portable
from serve import portable_ui as ui


class SetupPage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Strata UI 中文 空格 ')
        self.root = Path(self.temp.name)/'app'
        self.root.mkdir()
        (self.root/'meta.json').write_text(json.dumps({'version':'0.1.42-t8.4'}),encoding='utf-8')
        (self.root/'model-sources.json').write_bytes((ui.ROOT/'model-sources.json').read_bytes())
        web = self.root/'serve/web'
        web.mkdir(parents=True)
        for name in ('portable-setup.html','portable-setup.css','portable-setup.js'):
            (web/name).write_bytes((ui.ROOT/'serve/web'/name).read_bytes())
        self.patches = [mock.patch.object(portable,'ROOT',self.root),
                        mock.patch.object(portable,'STATE',self.root/'portable-settings.json')]
        for patch in self.patches:
            patch.start()
        self.app = ui.ModelSetup(self.root)
        self.http = ui.create_server(self.app)
        self.thread = threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.fingerprint = {'app':str(self.root),'data':str(self.root.parent/'Strata-data'),
                            'gpu':[0],'ram':128,'version':'0.1.42-t8.4','edition':'Portable-NoModels'}
        self.gpu = mock.patch.object(portable,'fingerprint',return_value=self.fingerprint)
        self.gpu.start()

    def tearDown(self):
        self.app.close()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.gpu.stop()
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def request(self,path='/',body=None,headers=None):
        connection = HTTPConnection('127.0.0.1',self.http.server_port,timeout=8)
        options = {'X-Strata-Setup-Token':self.app.token}
        options.update(headers or {})
        if body is not None:
            options.setdefault('Content-Type','application/json')
        try:
            connection.request('POST' if body is not None else 'GET',path,
                               body=json.dumps(body).encode('utf-8') if isinstance(body,dict) else body,headers=options)
            response = connection.getresponse()
            data = response.read()
            if response.getheader('Content-Type','').startswith('application/json'):
                data = json.loads(data)
            return response.status,dict(response.getheaders()),data
        finally:
            connection.close()

    def test_restarted_settings_displays_only_matching_update_failure(self):
        directory=self.root/'.portable-update';directory.mkdir()
        receipt=directory/'gui-result.json'
        receipt.write_text(json.dumps({'state':'error','app_root':str(self.root.resolve()),'error':'fixture download failed'}))
        app=ui.ModelSetup(self.root)
        self.assertEqual(app.job['state'],'error')
        self.assertIn('fixture download failed',app.job['error'])
        receipt.write_text(json.dumps({'state':'error','app_root':str(self.root.parent/'other'),'error':'foreign failure'}))
        self.assertEqual(ui.ModelSetup(self.root).job['state'],'idle')
        receipt.write_text('{bad json')
        self.assertEqual(ui.ModelSetup(self.root).job['state'],'idle')

    def test_slow_restore_verification_keeps_status_available_and_preserves_launcher(self):
        from tools import portable_restore
        started=threading.Event();release=threading.Event()
        self.app.update['supported']=True
        def verify(*args):
            started.set();release.wait(2)
            raise ValueError('fixture backup verification failure')
        try:
            with mock.patch.object(portable_restore,'read_receipt',side_effect=verify):
                before=time.monotonic()
                status,_,_=self.request('/api/update/restore',{'id':'a'*32})
                self.assertEqual(status,202)
                self.assertLess(time.monotonic()-before,1)
                self.assertTrue(started.wait(1))
                status,_,snapshot=self.request('/api/status')
                self.assertEqual(status,200)
                self.assertEqual(snapshot['job']['state'],'updating')
                release.set()
                deadline=time.monotonic()+2
                while self.app.job['state']=='updating' and time.monotonic()<deadline:time.sleep(.02)
                self.assertEqual(self.app.job['state'],'error')
                self.assertIn('fixture backup verification failure',self.app.job['error'])
                self.assertFalse(self.app.closed.is_set())
        finally:release.set()

    def test_null_restore_id_cannot_start_a_normal_update(self):
        self.app.update.update(supported=True,state='ready',available=True)
        with mock.patch.object(self.app,'begin_update') as begin:
            status,_,_=self.request('/api/update/restore',{'id':None})
        self.assertEqual(status,400)
        begin.assert_not_called()

    def test_status_survives_unavailable_config_without_changing_network_or_exposing_key(self):
        self.app.current_config=self.root/'temporarily-unavailable.json'
        self.app.backend_config={'host':'0.0.0.0','api_key':'private-fixture-key-do-not-publish'}
        status,_,snapshot=self.request('/api/status')
        self.assertEqual(status,200)
        self.assertEqual(snapshot['network']['scope'],'lan')
        self.assertTrue(snapshot['network']['api_key_configured'])
        self.assertTrue(snapshot['configuration_error'])
        self.assertNotIn('private-fixture-key-do-not-publish',json.dumps(snapshot))
        self.assertFalse((self.root/'portable-settings.json').exists())
        with self.assertRaises(FileNotFoundError):self.app.network_settings()

    def model(self):
        data = self.root.parent/'Strata-data'
        (data/'models/IQ3_S').mkdir(parents=True,exist_ok=True)
        for name in ('a.gguf','b.gguf'):
            (data/'models/IQ3_S'/name).write_bytes(b'fixture shard')
        (data/'mtp/rt').mkdir(parents=True,exist_ok=True)
        for name in ('dense.bin','dense.txt','experts.bin'):
            (data/'mtp/rt'/name).write_bytes(b'auxiliary fixture')
        descriptor = {'family':'qwen','model':'IQ3_S','gguf_dir':'models/IQ3_S',
                      'required_files':['models/IQ3_S/a.gguf','models/IQ3_S/b.gguf'],
                      'files':[{'file':name,'size':len(b'fixture shard')} for name in ('a.gguf','b.gguf')]}
        (data/'portable-model.json').write_text(json.dumps(descriptor),encoding='utf-8')
        return data

    def payload(self):
        return {'path':str(self.model()),'port':ui.free_port(),'context':32768,'vision':'no','backend':'auto','native_video':False}

    def wait_job(self,states=('ready','error'),timeout=8):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            state = self.app.snapshot()['job']['state']
            if state in states:
                return state
            time.sleep(.03)
        self.fail('job did not finish: '+repr(self.app.job))

    def fake_popen(self,*,fail=False,delay=0,fail_backend=False,backend_model_id='fixture-live-api-id'):
        real = subprocess.Popen
        created = []
        def spawn(args,**kwargs):
            if any(Path(a).name == 'prepare_portable_model.py' for a in args):
                code = '''import json,sys,time
from pathlib import Path
data=Path(sys.argv[1]); time.sleep(float(sys.argv[2]))
if sys.argv[3]=='fail': raise SystemExit(9)
(data/'models/IQ3_S').mkdir(parents=True,exist_ok=True)
for name in ('a.gguf','b.gguf'): (data/'models/IQ3_S'/name).write_bytes(b'fixture shard')
(data/'mtp/rt').mkdir(parents=True,exist_ok=True)
for name in ('dense.bin','dense.txt','experts.bin'): (data/'mtp/rt'/name).write_bytes(b'auxiliary fixture')
(data/'portable-model.json').write_text(json.dumps({'family':'qwen','model':'IQ3_S','gguf_dir':'models/IQ3_S','required_files':['models/IQ3_S/a.gguf','models/IQ3_S/b.gguf']}),encoding='utf-8')
'''
                replaced = [sys.executable,'-c',code,args[args.index('--data-dir')+1],str(delay),'fail' if fail else 'ok']
            elif 'import' in args:
                code = '''import json,sys,time
from pathlib import Path
root=Path.cwd(); time.sleep(float(sys.argv[1]))
if sys.argv[2]=='fail': raise SystemExit(7)
port=int(sys.argv[3]); fingerprint=json.loads(sys.argv[4])
(root/'strata-iq3_s.json').write_text(json.dumps({'exe':'fixture.exe','args':[],'port':port}),encoding='utf-8')
state=json.loads((root/'portable-settings.json').read_text(encoding='utf-8')) if (root/'portable-settings.json').exists() else {}
state.update(portable_data_dir=fingerprint['data'],portable_config='strata-iq3_s.json',portable_fingerprint=fingerprint)
(root/'portable-settings.json').write_text(json.dumps(state),encoding='utf-8')
'''
                port = args[args.index('--port')+1]
                replaced = [sys.executable,'-c',code,str(delay),'fail' if fail else 'ok',port,json.dumps(self.fingerprint)]
            else:
                code = '''import json,os,sys
from http.server import HTTPServer,BaseHTTPRequestHandler
if sys.argv[2]=='fail': raise SystemExit(8)
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  body=json.dumps({'service':'strata','model':sys.argv[3],'portable_launcher_instance':os.environ['STRATA_PORTABLE_UI_ID']}).encode()
  self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
 def log_message(self,*args): pass
# Match serve.server.Server: Windows does not allow address reuse.
HTTPServer.allow_reuse_address = os.name != 'nt'
HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()
'''
                port = args[args.index('--port')+1]
                replaced = [sys.executable,'-c',code,port,'fail' if fail_backend else 'ok',backend_model_id]
            proc = real(replaced,**kwargs)
            created.append(proc)
            return proc
        return spawn,created

    def test_page_opens_without_any_model_or_model_process(self):
        status,headers,body = self.request()
        self.assertEqual(status,200)
        self.assertIn('选择模型，开始使用。'.encode(),body)
        self.assertIn(self.app.token.encode(),body)
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        snapshot = self.request('/api/status')[2]
        self.assertFalse(snapshot['configured'])
        self.assertFalse(snapshot['running'])
        self.assertIsNone(self.app.process)

    def test_status_does_not_publish_configuration_secrets(self):
        config = self.root/'strata-iq3_s.json'
        config.write_text(json.dumps({'exe':'fixture','args':[],'api_key':'private-key','port':8080}))
        self.app.current_config = config
        self.assertNotIn('private-key',json.dumps(self.request('/api/status')[2]))

    def test_rejected_small_posts_deliver_a_visible_error_without_applying_settings(self):
        for headers in ({'Origin':'https://other.example'},{'X-Strata-Setup-Token':'expired'}):
            for _ in range(5):
                status,_,body=self.request('/api/network',{'scope':'lan','api_key':'fixture-key-12345'},headers)
                self.assertEqual(status,403)
                self.assertIn('error',body)
        self.assertFalse(portable.STATE.exists())

    def test_asr_settings_save_does_not_start_or_switch_any_model(self):
        with mock.patch.object(ui.subprocess,'Popen',side_effect=AssertionError('model process')):
            status,_,result=self.request('/api/asr/settings',{'model_dir':'E:\\语音 模型','quantization':'Q4_K_M','device':'cpu'})
            self.assertEqual(status,200)
            self.assertEqual(result['settings']['model_dir'],'E:\\语音 模型')
            self.assertFalse(result['capabilities']['supported'])
            self.assertFalse(result['capabilities']['default_enabled'])
            self.assertIsNone(self.app.process)
            self.assertIsNone(self.app.current_config)
            self.assertEqual(self.request('/api/asr/settings')[2]['settings']['quantization'],'Q4_K_M')

    def test_asr_settings_body_budget_accepts_valid_long_paths_and_context(self):
        from serve.asr import config as speech_config
        payload={'model_dir':'D:\\'+'m'*4090,'vad_dir':'D:\\'+'v'*4090,'context':'x'*8192}
        with mock.patch.object(speech_config,'availability',return_value={'supported':False,'missing':['fixture']}):
            status,_,result=self.request('/api/asr/settings',payload)
        self.assertEqual(status,200)
        self.assertEqual(result['settings']['context'],payload['context'])
        self.assertIsNone(self.app.process)

    def test_variant_card_id_is_distinct_from_live_service_api_id(self):
        payload = self.payload()
        data = Path(payload['path'])
        descriptor = json.loads((data/'portable-model.json').read_text())
        descriptor.update(model_name='sc117-qwen3.8-flash-next-abliterated-iq3_s',
                          repository='SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF')
        (data/'portable-model.json').write_text(json.dumps(descriptor))
        summary = self.request('/api/check', {'path':str(data)})[2]['model']
        self.assertEqual(summary['label'], 'SC117 Uncensored · IQ3_S')
        self.assertEqual(summary['api_model_id'], descriptor['model_name'])
        spawn, created = self.fake_popen()
        with mock.patch.object(ui.subprocess, 'Popen', side_effect=spawn):
            self.assertEqual(self.request('/api/import', payload)[0], 202)
            self.assertEqual(self.wait_job(), 'ready')
        connection = self.request('/api/status')[2]['api_connection']
        self.assertEqual(connection['model_id'], 'fixture-live-api-id')
        self.assertEqual(connection['base_url'], f'http://127.0.0.1:{self.app.backend_port}/v1')
        self.assertEqual(connection['models_url'], connection['base_url']+'/models')
        self.app.backend_config['api_key'] = 'private-key'
        current = self.request('/api/status')[2]
        self.assertTrue(current['api_connection']['api_key_required'])
        self.assertNotIn('private-key', json.dumps(current))
        self.app.set_job('starting', 'switching')
        self.assertIsNone(self.request('/api/status')[2]['api_connection'])

    def test_invalid_descriptor_api_ids_are_rejected_before_import(self):
        data = self.model()
        descriptor = json.loads((data/'portable-model.json').read_text())
        for name in ('', ' leading', 'bad\nname', 'bad\x7fname', 'a'*201, None, 7):
            with self.subTest(name=name):
                (data/'portable-model.json').write_text(json.dumps({**descriptor, 'model_name':name}))
                self.assertEqual(self.request('/api/check', {'path':str(data)})[0], 400)
                self.assertIsNone(self.app.process)

    def test_connection_display_cannot_prevent_a_legacy_service_from_starting(self):
        payload = self.payload()
        spawn, created = self.fake_popen(backend_model_id='x'*201)
        with mock.patch.object(ui.subprocess, 'Popen', side_effect=spawn):
            self.assertEqual(self.request('/api/import', payload)[0], 202)
            self.assertEqual(self.wait_job(), 'ready')
        self.assertIsNone(self.request('/api/status')[2]['api_connection'])

    def update_release(self):
        name = 'Strata-T8-0.1.42-t8.7-Windows-x64-Portable-NoModels.zip'
        return {'tag_name':'v0.1.42-t8.7','assets':[{'name':name},{'name':name+'.sha256'}]}

    def wait_update(self):
        deadline = time.monotonic()+5
        while self.app.update['state'] == 'checking' and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertNotEqual(self.app.update['state'],'checking')

    def test_update_check_is_read_only_and_offline_errors_do_not_stop_service(self):
        with mock.patch('tools.portable_update.latest_release',return_value=self.update_release()) as latest, \
             mock.patch.object(self.app,'stop_backend') as stop:
            self.assertEqual(self.request('/api/update/check',{})[0],202)
            self.wait_update()
            update = self.request('/api/status')[2]['update']
            self.assertTrue(update['available'])
            latest.assert_called_once_with('T8mars/Strata-T8',timeout=5)
            stop.assert_not_called()
        with mock.patch('tools.portable_update.latest_release',side_effect=OSError('offline')), \
             mock.patch.object(self.app,'stop_backend') as stop:
            self.app.check_updates(); self.wait_update()
            self.assertEqual(self.app.update['state'],'error')
            self.assertFalse(self.app.update['available'])
            self.assertEqual(self.app.job['state'],'idle')
            stop.assert_not_called()

    def test_update_requires_token_empty_body_and_a_supported_ready_release(self):
        self.assertEqual(self.request('/api/update/check',{}, {'X-Strata-Setup-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/update/start',{}, {'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.request('/api/update/start',{'url':'https://other.example'})[0],404)
        with mock.patch.object(self.app,'stop_backend') as stop:
            self.assertEqual(self.request('/api/update/start',{})[0],400)
            self.app.update.update(supported=True)
            self.assertEqual(self.request('/api/update/start',{})[0],400)
            stop.assert_not_called()

    def test_update_missing_asset_or_invalid_version_cannot_be_started(self):
        for release in ({'tag_name':'v0.1.42-t8.7','assets':[]}, {'tag_name':'bogus','assets':[]}):
            with mock.patch('tools.portable_update.latest_release',return_value=release):
                self.app.check_updates(); self.wait_update()
                self.assertEqual(self.app.update['state'],'error')
                self.assertFalse(self.app.update['available'])

    def test_update_handoff_is_outside_package_and_only_stops_owned_backend(self):
        (self.root/'tools').mkdir()
        (self.root/'tools/gui_portable_update.ps1').write_text('# fixture helper')
        self.app.update.update(supported=True,state='ready',available=True)
        def acknowledge(args, **kwargs):
            receipt = Path(args[args.index('-StatusFile')+1])
            receipt.write_text(json.dumps({'state':'waiting_parent',
                'nonce':args[args.index('-HandoffNonce')+1], 'app_root':args[args.index('-AppRoot')+1], 'parent_id':os.getpid()}))
            return mock.Mock(poll=lambda: None)
        with mock.patch.object(self.app,'stop_backend') as stop, mock.patch.object(ui.subprocess,'Popen',side_effect=acknowledge) as launch, \
             mock.patch('serve.winjob.contain') as contain:
            self.assertEqual(self.request('/api/update/start',{})[0],202)
            self.assertTrue(self.app.closed.wait(5))
            args = launch.call_args.args[0]
            helper = Path(args[args.index('-File')+1])
            self.assertFalse(helper.resolve().is_relative_to(self.root))
            self.assertEqual(helper.read_text(),'# fixture helper')
            self.assertEqual(Path(args[args.index('-AppRoot')+1]).resolve(),self.root.resolve())
            stop.assert_called_once()
            contain.assert_not_called()
            helper.unlink(); (helper.parent/'status.json').unlink(); helper.parent.rmdir()

    def test_update_helper_failure_keeps_launcher_and_running_backend(self):
        (self.root/'tools').mkdir()
        (self.root/'tools/gui_portable_update.ps1').write_text('# fixture helper')
        self.app.update.update(supported=True,state='ready',available=True)
        with mock.patch.object(self.app,'stop_backend') as stop, \
             mock.patch.object(ui.subprocess,'Popen',return_value=mock.Mock(poll=lambda:1)) as launch:
            self.assertEqual(self.request('/api/update/start',{})[0],202)
            self.wait_job(states=('error',))
            self.assertFalse(self.app.closed.is_set())
            stop.assert_not_called()
            self.assertIn('提前退出',self.app.job['error'])
            self.assertEqual(self.request('/api/status')[0],200)
            args=launch.call_args.args[0]
            helper=Path(args[args.index('-File')+1])
            self.assertTrue(helper.parent.name.startswith('Strata-T8-ui-update-'))
            helper.unlink(); helper.parent.rmdir()

    def test_updating_blocks_overlapping_model_mutations(self):
        self.app.job['state']='updating'
        self.assertEqual(self.request('/api/import',self.payload())[0],400)
        self.assertEqual(self.request('/api/stop',{})[0],400)

    def test_backend_stop_timeout_becomes_visible_update_error_without_closing_launcher(self):
        (self.root/'tools').mkdir()
        (self.root/'tools/gui_portable_update.ps1').write_text('# fixture helper')
        self.app.update.update(supported=True,state='ready',available=True)
        process=mock.Mock(poll=lambda:None)
        with mock.patch.object(ui,'wait_update_helper'), \
             mock.patch.object(ui.subprocess,'Popen',return_value=process) as launch, \
             mock.patch.object(self.app,'stop_backend',side_effect=subprocess.TimeoutExpired('owned backend',10)):
            self.assertEqual(self.request('/api/update/start',{})[0],202)
            self.wait_job(states=('error',))
            self.assertFalse(self.app.closed.is_set())
            process.terminate.assert_called_once()
            self.assertIn('timed out',self.app.job['error'])
            self.assertEqual(self.request('/api/status')[0],200)
            args=launch.call_args.args[0];helper=Path(args[args.index('-File')+1])
            self.assertTrue(helper.parent.name.startswith('Strata-T8-ui-update-'))
            helper.unlink();helper.parent.rmdir()

    def test_static_files_have_local_content_security_headers(self):
        for path in ('/setup.js','/setup.css'):
            with self.subTest(path=path):
                status,headers,body = self.request(path)
                self.assertEqual(status,200)
                self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
                self.assertTrue(body)
        self.assertEqual(self.request('/../portable-settings.json')[0],404)

    def test_read_and_write_api_require_a_session_token(self):
        for path,body in (('/api/status',None),('/api/models',None),('/api/check',{'path':'x'})):
            self.assertEqual(self.request(path,body,{'X-Strata-Setup-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/status',headers={'X-Strata-Setup-Token':'é'})[0],403)

    def test_rebinding_host_and_foreign_origin_are_rejected(self):
        self.assertEqual(self.request('/',headers={'Host':'evil.example:'+str(self.http.server_port)})[0],403)
        self.assertEqual(self.request('/api/check',{'path':'x'},{'Origin':'https://evil.example'})[0],403)

    def test_bad_token_is_rejected_before_a_stalled_body(self):
        with socket.create_connection(('127.0.0.1',self.http.server_port),timeout=2) as sock:
            sock.sendall(f'POST /api/import HTTP/1.1\r\nHost: 127.0.0.1:{self.http.server_port}\r\nContent-Type: application/json\r\nContent-Length: 1000\r\nX-Strata-Setup-Token: bad\r\n\r\n'.encode())
            self.assertIn(b'403',sock.recv(1024).split(b'\r\n')[0])

    def test_oversized_content_and_transfer_encoding_are_rejected(self):
        self.assertEqual(self.request('/api/check',b'x',{'Content-Length':'999999'})[0],413)
        self.assertEqual(self.request('/api/check',b'{}',{'Transfer-Encoding':'chunked'})[0],400)
        self.assertEqual(self.request('/api/check',b'{}',{'Content-Type':'text/plain'})[0],415)

    def test_duplicate_and_nonfinite_json_are_not_accepted(self):
        for body in (b'{"path":"a","path":"b"}',b'{"context":NaN}',b'[]',b'{'):
            self.assertEqual(self.request('/api/check',body)[0],400)

    def test_empty_and_control_character_paths_are_rejected(self):
        for path in ('','   ','""','x\x00y','x\ny'):
            self.assertEqual(self.request('/api/check',{'path':path})[0],400)

    def test_prepared_model_is_detected_and_validated_offline(self):
        data = self.model()
        with mock.patch('urllib.request.urlopen',side_effect=AssertionError('No downloads')):
            result = self.request('/api/check',{'path':str(data)})
            self.assertEqual(result[0],200)
            self.assertEqual(result[2]['model']['quantization'],'IQ3_S')
            candidates = self.request('/api/models')[2]['models']
            self.assertIn(str(data.resolve()),[m['path'] for m in candidates])

    def test_missing_or_truncated_main_and_mtp_files_are_clear_errors(self):
        for relative in ('models/IQ3_S/a.gguf','mtp/rt/dense.bin'):
            data = self.model()
            (data/relative).write_bytes(b'')
            status,headers,result = self.request('/api/check',{'path':str(data)})
            self.assertEqual(status,400)
            self.assertIn('不完整',result['error'])

    def test_invalid_options_fail_before_configuration_or_process_start(self):
        payload = self.payload()
        for values in ({'context':True},{'context':10**20},{'port':0},{'backend':'shell'},
                       {'vision':'gpu'},{'native_video':True},{'command':'delete'}):
            with self.subTest(values=values),mock.patch.object(self.app,'_spawn') as spawn:
                self.assertEqual(self.request('/api/import',{**payload,**values})[0],400)
                spawn.assert_not_called()

    def test_occupied_port_never_stops_an_external_service(self):
        payload = self.payload()
        with socket.socket() as external:
            external.bind(('127.0.0.1',0)); external.listen()
            payload['port'] = external.getsockname()[1]
            self.assertEqual(self.request('/api/import',payload)[0],400)
            self.assertEqual(external.getsockname()[1],payload['port'])
            self.assertIsNone(self.app.process)

    def test_save_switches_an_active_owned_backend_without_touching_an_external_listener(self):
        payload = self.payload()
        spawn,created = self.fake_popen()
        with socket.socket() as external, mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            external.bind(('127.0.0.1',0)); external.listen()
            self.request('/api/import',payload)
            self.assertEqual(self.wait_job(),'ready')
            old = self.app.process
            second = self.root.parent/'Strata-data-sc117'
            shutil.copytree(Path(payload['path']),second)
            self.fingerprint['data'] = str(second)
            self.assertEqual(self.request('/api/import',{**payload,'path':str(second)})[0],202)
            self.assertEqual(self.wait_job(),'ready',self.app.job)
            self.assertEqual(self.app.model['path'],str(second.resolve()))
            self.assertIsNotNone(old.poll())
            self.assertIsNot(self.app.process,old)
            self.assertIsNone(self.app.process.poll())
            self.assertGreater(external.fileno(),-1)
            self.assertEqual(len(created),4)

    def test_invalid_switch_port_keeps_the_active_owned_backend_running(self):
        payload = self.payload()
        spawn,created = self.fake_popen()
        with socket.socket() as external, mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            external.bind(('127.0.0.1',0)); external.listen()
            self.request('/api/import',payload)
            self.assertEqual(self.wait_job(),'ready')
            old = self.app.process
            self.assertEqual(self.request('/api/import',{**payload,'port':external.getsockname()[1]})[0],400)
            self.assertIs(self.app.process,old)
            self.assertIsNone(old.poll())
            self.assertEqual(self.app.job['state'],'ready')

    def test_switch_reports_actual_api_port_when_windows_retains_old_port(self):
        payload = self.payload()
        spawn, _ = self.fake_popen()
        with mock.patch.object(ui.subprocess, 'Popen', side_effect=spawn):
            self.assertEqual(self.request('/api/import', payload)[0], 202)
            self.assertEqual(self.wait_job(), 'ready')
            old = self.app.process
            old_port = self.app.backend_port
            choose = ui.free_port
            def retained(preferred=8080, *, strict=False, exclude=()):
                if preferred == old_port:
                    self.assertFalse(strict, 'owned-port restart should permit a safe alternative')
                    return choose(preferred, exclude=[*exclude, old_port])
                return choose(preferred, strict=strict, exclude=exclude)
            with mock.patch.object(ui, 'free_port', side_effect=retained):
                self.assertEqual(self.request('/api/import', {**payload, 'port':old_port})[0], 202)
                self.assertEqual(self.wait_job(), 'ready', self.app.job)
            self.assertIsNotNone(old.poll())
            port = self.app.backend_port
            self.assertNotEqual(port, old_port)
            self.assertEqual(portable.read_config(self.app.current_config)['port'], port)
            self.assertEqual(self.app.snapshot()['api_connection']['base_url'], f'http://127.0.0.1:{port}/v1')
            self.assertIn(str(old_port), self.app.job['message'])
            self.assertIn(str(port), self.app.job['message'])

    def test_known_sc117_delivery_has_a_clear_uncensored_label(self):
        data = self.model()
        descriptor = data/'portable-model.json'
        value = json.loads(descriptor.read_text())
        value['repository']='SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF'
        descriptor.write_text(json.dumps(value))
        self.assertEqual(self.request('/api/check',{'path':str(data)})[2]['model']['label'],'SC117 Uncensored · IQ3_S')

    def test_owned_configuration_and_ready_backend_then_stop(self):
        payload = self.payload()
        spawn,created = self.fake_popen()
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.assertEqual(self.request('/api/import',payload)[0],202)
            self.assertEqual(self.wait_job(),'ready',self.app.job)
            current = self.request('/api/status')[2]
            self.assertTrue(current['running'])
            self.assertEqual(current['chat_url'],f"http://127.0.0.1:{payload['port']}/")
            self.assertEqual(self.request('/api/stop',{})[0],202)
            self.wait_job(('idle',))
            self.assertTrue(all(p.poll() is not None for p in created))

    def test_configuration_failure_retains_existing_user_files(self):
        payload = self.payload()
        original = b'{"personal":"keep this"}'
        (self.root/'portable-settings.json').write_bytes(original)
        spawn,created = self.fake_popen(fail=True)
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.request('/api/import',payload)
            self.assertEqual(self.wait_job(),'error')
            self.assertEqual((self.root/'portable-settings.json').read_bytes(),original)
            self.assertEqual(len(created),1)

    def test_backend_failure_is_visible_and_owned_child_ends(self):
        payload = self.payload()
        spawn,created = self.fake_popen(fail_backend=True)
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.request('/api/import',payload)
            self.assertEqual(self.wait_job(),'error')
            self.assertIn('服务未能启动',self.app.job['error'])
            self.assertTrue(all(p.poll() is not None for p in created))

    def test_duplicate_click_cannot_start_two_configurations(self):
        payload = self.payload()
        spawn,created = self.fake_popen(delay=.3)
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.assertEqual(self.request('/api/import',payload)[0],202)
            self.assertEqual(self.request('/api/import',payload)[0],400)
            self.assertEqual(self.wait_job(),'ready')
            self.assertEqual(len(created),2)

    def test_close_during_configuration_does_not_start_a_backend(self):
        payload = self.payload()
        spawn,created = self.fake_popen(delay=20)
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.request('/api/import',payload)
            deadline = time.monotonic()+3
            while self.app.configure_process is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.app.close()
            time.sleep(.1)
            self.assertEqual(len(created),1)
            self.assertIsNotNone(created[0].poll())
            self.assertIsNone(self.app.process)

    def test_launcher_data_override_is_cleared_after_explicit_gui_selection(self):
        payload = self.payload()
        self.app.requested = str(self.root/'wrong old path')
        spawn,created = self.fake_popen()
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.request('/api/import',payload)
            self.assertEqual(self.wait_job(),'ready')
            self.assertIsNone(self.app.requested)

    def test_folder_selection_requires_authorized_post_and_is_serial(self):
        with mock.patch.object(ui,'choose_folder',return_value=str(self.root)) as picker:
            self.assertEqual(self.request('/api/browse',{}, {'X-Strata-Setup-Token':'bad'})[0],403)
            picker.assert_not_called()
            self.assertEqual(self.request('/api/browse',{})[2]['path'],str(self.root))
            self.app.folder_lock.acquire()
            try:
                self.assertEqual(self.request('/api/browse',{})[0],400)
            finally:
                self.app.folder_lock.release()

    def test_model_preparation_requires_explicit_consent_before_creating_a_folder(self):
        destination = self.root.parent/'new model'
        with mock.patch.object(self.app,'_spawn') as spawn:
            for consent in (False,1,'yes'):
                self.assertEqual(self.request('/api/prepare',{'path':str(destination),'accept_download':consent})[0],400)
            self.assertFalse(destination.exists())
            spawn.assert_not_called()

    def test_preparation_refuses_program_dependencies_and_unrelated_user_files(self):
        destination = self.root.parent/'personal files'
        destination.mkdir(); (destination/'notes.txt').write_text('preserve')
        with mock.patch.object(self.app,'_spawn') as spawn:
            for path in (self.root,self.root/'runtime',destination):
                self.assertEqual(self.request('/api/prepare',{'path':str(path),'accept_download':True})[0],400)
            spawn.assert_not_called()
        self.assertEqual((destination/'notes.txt').read_text(),'preserve')

    def test_official_preparation_does_not_mix_custom_model_weights(self):
        destination = self.model()
        path = destination/'portable-model.json'
        descriptor = json.loads(path.read_text())
        descriptor['repository']='SC117/custom-GGUF'
        path.write_text(json.dumps(descriptor))
        original = path.read_bytes()
        self.assertEqual(self.request('/api/prepare',{'path':str(destination),'accept_download':True})[0],400)
        self.assertEqual(path.read_bytes(),original)
        self.assertFalse((destination/'.strata-model-preparation.json').exists())

    def test_preparation_rejects_malformed_or_oversized_descriptors_without_spawning(self):
        destination = self.model()
        descriptor = destination/'portable-model.json'
        with mock.patch.object(self.app,'_spawn') as spawn:
            for content in ('[]', ' '* (1024*1024+1)):
                descriptor.write_text(content)
                self.assertEqual(self.request('/api/prepare',{'path':str(destination),'accept_download':True})[0],400)
            spawn.assert_not_called()
        self.assertFalse((destination/'.strata-model-preparation.json').exists())

    def test_unrelated_json_service_cannot_be_treated_as_our_identity(self):
        connection = mock.Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'[]'
        with mock.patch.object(ui,'HTTPConnection',return_value=connection):
            with self.assertRaises(ui.SetupError):
                ui.json_reply(8080,'/api/identity')
        connection.close.assert_called_once()

    def test_preparation_uses_owned_worker_then_prompts_save_without_starting_model(self):
        destination = self.root.parent/'fresh official model'
        spawn,created = self.fake_popen()
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.assertEqual(self.request('/api/prepare',{'path':str(destination),'accept_download':True})[0],202)
            self.assertEqual(self.wait_job(('prepared','error')),'prepared',self.app.job)
            status = self.request('/api/status')[2]
            self.assertEqual(status['prepared_model']['path'],str(destination.resolve()))
            self.assertFalse(status['running'])
            self.assertEqual(len(created),1)
            self.assertIsNotNone(created[0].poll())

    def test_failed_preparation_keeps_resume_marker_and_no_model_is_started(self):
        destination = self.root.parent/'resume official model'
        spawn,created = self.fake_popen(fail=True)
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.request('/api/prepare',{'path':str(destination),'accept_download':True})
            self.assertEqual(self.wait_job(),'error')
            self.assertTrue((destination/'.strata-model-preparation.json').is_file())
            self.assertIsNone(self.app.process)

    def test_source_mismatch_rejects_an_unfinished_preparation(self):
        destination = self.root.parent/'other source'
        destination.mkdir()
        marker = destination/'.strata-model-preparation.json'
        marker.write_text(json.dumps({'repository':'other','catalog_sha256':'0'*64}))
        original = marker.read_bytes()
        self.assertEqual(self.request('/api/prepare',{'path':str(destination),'accept_download':True})[0],400)
        self.assertEqual(marker.read_bytes(),original)

    def test_default_browser_start_routes_to_model_page_before_reading_a_model(self):
        with mock.patch('sys.argv',['portable.py']),mock.patch.object(portable,'environment_check'), \
             mock.patch('portable_update.check_update'), \
             mock.patch('serve.portable_ui.run',return_value=0) as run, \
             mock.patch.object(portable,'model_delivery') as delivery,mock.patch('os.chdir'):
            self.assertEqual(portable.main(),0)
            run.assert_called_once()
            delivery.assert_not_called()

    def test_default_browser_start_checks_updates_without_waiting_for_model_setup(self):
        with mock.patch('sys.argv',['portable.py']),mock.patch.object(portable,'environment_check'), \
             mock.patch('portable_update.check_update') as update, \
             mock.patch('serve.portable_ui.run',return_value=0) as run, mock.patch('os.chdir'), \
             mock.patch.object(portable.threading,'Thread') as worker:
            worker.return_value.start.side_effect = lambda: worker.call_args.kwargs['target']()
            self.assertEqual(portable.main(),0)
            update.assert_called_once_with()
            self.assertTrue(worker.call_args.kwargs['daemon'])
            run.assert_called_once()

    def test_settings_action_and_invalid_ui_port(self):
        with mock.patch('sys.argv',['portable.py','settings','--no-browser']),mock.patch.object(portable,'environment_check'), \
             mock.patch('serve.portable_ui.run',return_value=0) as run,mock.patch('os.chdir'):
            self.assertEqual(portable.main(),0)
            self.assertTrue(run.call_args.args[0].no_browser)
        with mock.patch('sys.argv',['portable.py','--ui-port','0']),mock.patch.object(portable,'environment_check') as env:
            with self.assertRaises(SystemExit): portable.main()
            env.assert_not_called()


if __name__=='__main__': unittest.main()

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

    def fake_popen(self,*,fail=False,delay=0,fail_backend=False):
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
(root/'portable-settings.json').write_text(json.dumps({'portable_data_dir':fingerprint['data'],'portable_config':'strata-iq3_s.json','portable_fingerprint':fingerprint}),encoding='utf-8')
'''
                port = args[args.index('--port')+1]
                replaced = [sys.executable,'-c',code,str(delay),'fail' if fail else 'ok',port,json.dumps(self.fingerprint)]
            else:
                code = '''import json,os,sys
from http.server import HTTPServer,BaseHTTPRequestHandler
if sys.argv[2]=='fail': raise SystemExit(8)
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  body=json.dumps({'service':'strata','portable_launcher_instance':os.environ['STRATA_PORTABLE_UI_ID']}).encode()
  self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
 def log_message(self,*args): pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),H).serve_forever()
'''
                port = args[args.index('--port')+1]
                replaced = [sys.executable,'-c',code,port,'fail' if fail_backend else 'ok']
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

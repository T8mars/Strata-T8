"""Seventh audit: delivered shard contracts, launcher transactions and vision delivery."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import portable
from tools import managed_config, prepare_portable_vision as vision


class ImportedShardContract(unittest.TestCase):
    def fixture(self, root):
        gguf = root/'models/IQ3_S'; gguf.mkdir(parents=True)
        (gguf/'main.gguf').write_bytes(b'model!!')
        for name in ('experts.bin','dense.bin','dense.txt'):
            path = root/'mtp/rt'/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'mtp')
        model = {'family':'qwen','model':'IQ3_S','gguf_dir':'models/IQ3_S',
                 'required_files':['models/IQ3_S/main.gguf'],
                 'files':[{'file':'main.gguf','size':7,'sha256':'0'*64}]}
        return model, gguf

    def invalid(self, change):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); model, gguf = self.fixture(root); change(model, gguf)
            portable.save_json(root/'portable-model.json',model)
            with self.assertRaisesRegex(RuntimeError,'incomplete|catalog|descriptor|shard'): portable.model_delivery(root)

    def test_r10_import_rejects_nonempty_truncated_shard_from_old_ready_descriptor(self):
        self.invalid(lambda m,g:(g/'main.gguf').write_bytes(b'x'))

    def test_r10_import_rejects_oversized_shard(self):
        self.invalid(lambda m,g:(g/'main.gguf').write_bytes(b'too long model'))

    def test_r10_shard_omitted_from_required_files_is_still_checked(self):
        def change(model,gguf): model['required_files'] = []; (gguf/'main.gguf').unlink()
        self.invalid(change)

    def test_r10_wrong_catalog_entry_types_do_not_bypass_size_contract(self):
        for files in ([],{},[None],[{'file':'main.gguf','size':True}],[{'file':'../elsewhere.gguf','size':7}],
                      [{'file':'main.gguf','size':7},{'file':'MAIN.GGUF','size':7}]):
            with self.subTest(files=files): self.invalid(lambda m,g:m.update(files=files))

    def test_r10_valid_and_legacy_deliveries_keep_offline_compatibility(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); model,_ = self.fixture(root)
            for legacy in (False,True):
                if legacy: model.pop('files')
                portable.save_json(root/'portable-model.json',model)
                with mock.patch('urllib.request.urlopen',side_effect=AssertionError('unexpected network')):
                    self.assertEqual(portable.model_delivery(root)['model'],'IQ3_S')


class ManagedReuseIsolation(unittest.TestCase):
    def invoke(self, *, fail=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/'app'; root.mkdir(); data = Path(td)/'data'; data.mkdir()
            base = root/'strata-iq3_s.json'; state = root/'portable-settings.json'; output = root/'managed/service.json'
            original = {'args':['--native','fixture.gguf','--max-context','65536','--custom','keep'],
                        'exe':'old.exe','host':'0.0.0.0','api_key':'fixture-secret','port':8095,
                        'before_load':['do not inherit'], 'min_free_vram_mib':1234,'parallel':4,
                        'sampling':{'temperature':.3}, 'idle_unload_s':60}
            portable.save_json(base,original); portable.save_json(state,{'portable_fingerprint':{'data':'same'}})
            before = {p:p.read_bytes() for p in (base,state)}
            original_save = portable.save_json
            def save(path,value):
                if fail: raise OSError('fixture output locked')
                original_save(path,value)
            with mock.patch.object(portable,'ROOT',root),mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable,'environment_check'),mock.patch.object(portable,'isolate_setup'), \
                 mock.patch.object(portable,'fingerprint',return_value={'data':'same'}), \
                 mock.patch.object(portable,'model_delivery',return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
                 mock.patch.object(portable.upstream,'cuda_lib_dirs',return_value=[]), \
                 mock.patch.object(managed_config,'fresh_config',side_effect=AssertionError('unexpected setup')), \
                 mock.patch.object(managed_config.portable,'save_json',side_effect=save), \
                 mock.patch('sys.argv',['managed','--data-dir',str(data),'--output',str(output),'--vision','no']):
                if fail:
                    with self.assertRaisesRegex(OSError,'locked'): managed_config.main()
                else: self.assertEqual(managed_config.main(),0)
            for path,content in before.items(): self.assertEqual(path.read_bytes(),content)
            return portable.read_json(output) if not fail else None

    def test_r11_cached_profile_replaces_web_security_and_lifecycle_defaults(self):
        result = self.invoke()
        self.assertEqual(result['host'],'127.0.0.1'); self.assertNotIn('api_key',result)
        self.assertTrue(result['lazy_load']); self.assertEqual(result['parallel'],1)
        self.assertEqual(result['min_free_vram_mib'],0); self.assertIsNone(result['before_load'])
        self.assertEqual(result['idle_unload_s'],0)

    def test_r11_cached_profile_keeps_custom_engine_sampling_and_port(self):
        result = self.invoke(); self.assertEqual(result['sampling'],{'temperature':.3}); self.assertEqual(result['port'],8095)
        self.assertEqual(result['args'],['--native','fixture.gguf','--custom','keep','--max-context','32768'])

    def test_r11_failed_managed_output_does_not_change_web_installation(self): self.invoke(fail=True)


class GeneratedLauncherTransaction(unittest.TestCase):
    def invoke(self, failure=None, *, existing=True, link=False, execute=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = root/'strata-iq3_s.json'; state = root/'portable-settings.json'
            suffix = '.bat' if portable.upstream.WIN else '.sh'; script = root/('run-iq3_s'+suffix)
            portable.save_json(cfg,{'args':['--native','old.gguf'],'port':8084,'api_key':'fixture-secret'})
            portable.save_json(state,{'custom':'keep'}); before = {p:p.read_bytes() for p in (cfg,state)}
            external = root/'external-script'; external.write_bytes(b'keep external')
            if link: script.symlink_to(external)
            elif existing: script.write_bytes(b'previous launcher bytes'); before[script] = script.read_bytes()
            def setup():
                portable.save_json(cfg,{'args':['--native','new.gguf'],'port':8080})
                portable.upstream.write_run_script('IQ3_S',cfg,8080)
                return 2 if failure == 'setup' else 0
            original_save = portable.save_json
            def save(path,value):
                if path == state and failure == 'state': raise OSError('fixture state write failed')
                original_save(path,value)
            with mock.patch.object(portable,'ROOT',root),mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable.upstream,'ROOT',root), \
                 mock.patch.object(portable,'model_delivery',return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
                 mock.patch.object(portable,'fingerprint',return_value={'version':'new'}), \
                 mock.patch.object(portable.upstream,'main',side_effect=setup) as call, \
                 mock.patch.object(portable,'attach_vision',side_effect=RuntimeError('fixture vision failed')), \
                 mock.patch.object(portable,'save_json',side_effect=save), mock.patch('sys.argv',['fixture']):
                if link:
                    with self.assertRaisesRegex(RuntimeError,'[Ll]ink|script'): portable.configure(root/'data')
                    call.assert_not_called(); self.assertEqual(external.read_bytes(),b'keep external')
                    script.unlink(); return
                if failure in ('vision','state'):
                    with self.assertRaises((RuntimeError,OSError)): portable.configure(root/'data',vision='gpu' if failure=='vision' else None)
                else:
                    result,_ = portable.configure(root/'data'); self.assertEqual(result,2 if failure=='setup' else 0)
            if failure:
                for path,content in before.items(): self.assertEqual(path.read_bytes(),content)
                if not existing: self.assertFalse(script.exists())
            else:
                self.assertIn('"--port" "8084"',script.read_text(encoding='utf-8'))
                self.assertEqual(portable.read_json(cfg)['port'],8084)
                if execute:
                    helper = root/'serve/server.py'; helper.parent.mkdir()
                    helper.write_text('import json, sys\nfrom pathlib import Path\n'
                                      'Path("received.json").write_text(json.dumps(sys.argv), encoding="utf-8")\n',encoding='utf-8')
                    command = ['cmd.exe','/d','/c',str(script)] if portable.upstream.WIN else ['sh',str(script)]
                    result = subprocess.run(command,cwd=root,capture_output=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                    received=json.loads((root/'received.json').read_text(encoding='utf-8'))
                    self.assertEqual(received[received.index('--port')+1],'8084')

    def test_r12_successful_reconfigure_launcher_uses_preserved_port(self): self.invoke()
    def test_r12_vision_failure_restores_previous_launcher(self): self.invoke('vision')
    def test_r12_state_failure_restores_previous_launcher(self): self.invoke('state')
    def test_r12_nonzero_setup_removes_new_incomplete_launcher(self): self.invoke('setup',existing=False)
    def test_r12_linked_launcher_is_rejected_before_setup(self): self.invoke(link=True)
    def test_r12_generated_launcher_passes_preserved_port_in_real_shell(self): self.invoke(execute=True)


class VisionCatalogBoundaries(unittest.TestCase):
    def fixture(self, root):
        payload = b'small vision fixture'
        entry = {'family':'qwen','repository':'fixture/vision','modelscope_revision':'domestic-rev',
                 'official_revision':'official-rev','file':'mmproj-fixture.gguf',
                 'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        folder = root/'vision/weights'; folder.mkdir(parents=True); portable.save_json(root/'vision/catalog.json',entry)
        (folder/entry['file']).write_bytes(payload)
        return entry,payload,folder

    def test_r13_vision_catalog_cannot_select_weight_outside_weights_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); entry,payload,folder=self.fixture(root); (root/'outside.gguf').write_bytes(payload)
            entry['file']='../../outside.gguf'; portable.save_json(root/'vision/catalog.json',entry)
            with self.assertRaisesRegex((RuntimeError,ValueError),'[Cc]atalog|[Pp]ath|[Ff]ilename'): vision.verify(root)

    def test_r13_invalid_vision_catalog_types_are_rejected_before_download(self):
        for key,value in (('file','NUL.gguf'),('file','../outside.gguf'),('size',True),('size',0),('sha256',[]),('family',[])):
            with self.subTest(key=key,value=value),tempfile.TemporaryDirectory() as td:
                root=Path(td); entry,_,_=self.fixture(root); entry[key]=value
                portable.save_json(root/'vision/catalog.json',entry)
                with mock.patch.object(vision,'download') as transfer,mock.patch('sys.argv',['vision','--root',str(root)]):
                    with self.assertRaises((RuntimeError,ValueError)): vision.main()
                transfer.assert_not_called()

    def test_r13_linked_vision_weight_is_rejected_even_with_expected_digest(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'app'; entry,payload,folder=self.fixture(root)
            external=Path(td)/'external.gguf'; external.write_bytes(payload)
            path=folder/entry['file']; path.unlink(); path.symlink_to(external)
            with self.assertRaisesRegex((RuntimeError,ValueError),'[Pp]ath|outside|linked'): vision.verify(root)
            self.assertEqual(external.read_bytes(),payload)

    def test_r13_linked_download_sidecar_is_rejected_without_touching_external_file(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'app'; entry,_,folder=self.fixture(root); (folder/entry['file']).unlink()
            external=Path(td)/'external-ranges.json'; external.write_bytes(b'keep external')
            link=folder/(entry['file']+'.ranges.json.tmp'); link.symlink_to(external)
            with mock.patch.object(vision,'download') as transfer,mock.patch('sys.argv',['vision','--root',str(root)]):
                with self.assertRaisesRegex((RuntimeError,ValueError),'[Pp]ath|outside|linked'): vision.main()
            transfer.assert_not_called(); self.assertEqual(external.read_bytes(),b'keep external')

    def test_r13_existing_valid_vision_verify_uses_no_network(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); entry,_,folder=self.fixture(root)
            with mock.patch('urllib.request.urlopen',side_effect=AssertionError('unexpected network')):
                self.assertEqual(vision.verify(root),folder/entry['file'])


class RealVisionSourceFallback(unittest.TestCase):
    fixture = VisionCatalogBoundaries.fixture
    def test_r14_real_range_download_recovers_from_wrong_domestic_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); entry,payload,folder=self.fixture(root); (folder/entry['file']).unlink(); hits=[]
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    hits.append(self.path); body=b'x'*len(payload) if self.path=='/domestic' else payload
                    self.send_response(206); self.send_header('Content-Range',f'bytes 0-{len(body)-1}/{len(body)}')
                    self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
                def log_message(self,*args): pass
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler); worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
            origin=f'http://127.0.0.1:{server.server_port}'
            try:
                with mock.patch.object(vision,'sources',return_value=[origin+'/domestic',origin+'/mirror',origin+'/official']), \
                     mock.patch('sys.argv',['vision','--root',str(root),'--workers','1']),contextlib.redirect_stdout(io.StringIO()):
                    vision.main()
            finally: server.shutdown(); server.server_close(); worker.join()
            self.assertEqual(hits,['/domestic','/mirror']); self.assertEqual((folder/entry['file']).read_bytes(),payload)
            self.assertEqual(vision.verify(root),folder/entry['file'])

    def test_r14_vision_sources_keep_domestic_priority_and_fixed_revisions(self):
        with tempfile.TemporaryDirectory() as td:
            entry,_,_=self.fixture(Path(td)); urls=vision.sources(entry)
            self.assertIn('modelscope.cn/',urls[0]); self.assertIn('Revision=domestic-rev',urls[0])
            self.assertIn('hf-mirror.com/',urls[1]); self.assertIn('/resolve/official-rev/',urls[1])
            self.assertIn('huggingface.co/',urls[2]); self.assertIn(entry['file'],urls[2])


if __name__=='__main__': unittest.main()

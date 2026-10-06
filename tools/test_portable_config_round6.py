"""Sixth audit: completed settings, isolated destinations and prepared-file boundaries."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import portable
from tools import managed_config, prepare_portable_model as prepare_model


class CompletedSettings(unittest.TestCase):
    def configure(self, requested, *, changed=False, succeeds=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = root/'strata-iq3_s.json'; state = root/'portable-settings.json'
            original = {'exe':'fixture.exe', 'args':['--max-context','32768'], 'port':8080}
            portable.save_json(cfg, original)
            portable.save_json(state, {'portable_fingerprint':{'data':'fixture'}, 'custom':'keep'})
            before = {p:p.read_bytes() for p in (cfg,state)}
            def setup():
                if changed:
                    generated = {**original, 'args':['--max-context',str(requested.get('context',32768))]}
                    if 'backend' in requested: generated['backend'] = requested['backend']
                    if 'port' in requested: generated['port'] = requested['port']
                    portable.save_json(cfg, generated)
                return 0
            with mock.patch.object(portable,'ROOT',root), mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable,'model_delivery',return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
                 mock.patch.object(portable,'fingerprint',return_value={'data':'fixture'}), \
                 mock.patch.object(portable.upstream,'main',side_effect=setup), mock.patch('sys.argv',['test']):
                if succeeds:
                    self.assertEqual(portable.configure(root/'data',**requested)[0],0)
                    return portable.read_json(cfg)
                with self.assertRaisesRegex(RuntimeError,'requested|configuration'): portable.configure(root/'data',**requested)
                for p,content in before.items(): self.assertEqual(p.read_bytes(),content)

    def test_r10_no_op_cannot_ignore_requested_context(self): self.configure({'context':65536})
    def test_r10_no_op_cannot_ignore_requested_backend(self): self.configure({'backend':'hip'})
    def test_r10_no_op_cannot_ignore_requested_port(self): self.configure({'port':8093})
    def test_r10_existing_requested_values_and_changed_setup_are_accepted(self):
        self.configure({'context':32768,'backend':'cuda','port':8080},succeeds=True)
        result = self.configure({'context':65536,'backend':'hip','port':8093},changed=True,succeeds=True)
        self.assertEqual(result['args'],['--max-context','65536']); self.assertEqual(result['backend'],'hip')


class IsolatedOutput(unittest.TestCase):
    def invoke(self, kind, *, succeeds=False, alias=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/'app'; root.mkdir(); data = Path(td)/'data'; data.mkdir()
            base = root/'strata-iq3_s.json'; state = root/'portable-settings.json'
            portable.save_json(base,{'args':['--native','fixture.gguf'], 'port':8080})
            portable.save_json(state,{'portable_fingerprint':{'data':'fixture'}})
            artifact = data/'models/main.gguf'; artifact.parent.mkdir(); artifact.write_bytes(b'keep model')
            application = root/'serve/server.py'; application.parent.mkdir(); application.write_bytes(b'keep application')
            launcher = root/'portable.py'; launcher.write_bytes(b'keep launcher')
            reserved = []
            for relative in ('runtime/python/python.exe','engine/strata.exe','vision/catalog.json','LICENSE','capabilities.json'):
                path = root/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'keep reserved')
                reserved.append(path)
            portable.save_json(root/'PACKAGE-MANIFEST.json',{'files':[{'path':'capabilities.json'}]})
            output = {'base':base,'state':state,'model':artifact,'profile':root/'managed/service.json',
                      'root-profile':root/'custom-profile.json','application':application,'launcher':launcher,
                      'runtime':reserved[0],'engine':reserved[1],'vision':reserved[2],'license':reserved[3],'manifest':reserved[4],
                      'directory':root}[kind]
            if kind == 'root-profile': portable.save_json(output,{'previous':'profile'})
            link = None
            if alias:
                link = Path(td)/'alias'
                if os.name == 'nt': junction(link,root)
                else: link.symlink_to(root,target_is_directory=True)
                output = link/output.relative_to(root)
            before = {p:p.read_bytes() for p in (base,state,artifact,application,launcher,*reserved)}
            with mock.patch.object(portable,'ROOT',root), mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable,'environment_check') as environment, mock.patch.object(portable,'isolate_setup'), \
                 mock.patch.object(portable,'model_delivery',return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
                 mock.patch.object(portable,'fingerprint',return_value={'data':'fixture'}), \
                 mock.patch.object(portable.upstream,'cuda_lib_dirs',return_value=[]), \
                 mock.patch('sys.argv',['managed','--data-dir',str(data),'--output',str(output),'--vision','no']):
                if succeeds:
                    self.assertEqual(managed_config.main(),0)
                    self.assertTrue(output.is_file()); self.assertTrue(portable.read_json(output)['lazy_load'])
                else:
                    with self.assertRaisesRegex((RuntimeError,ValueError),'output|Output|profile'): managed_config.main()
                    environment.assert_not_called()
            for p,content in before.items(): self.assertEqual(p.read_bytes(),content)
            if link is not None:
                if os.name == 'nt': link.rmdir()
                else: link.unlink()

    def test_r11_output_cannot_replace_primary_web_configuration(self):
        for alias in (False,True):
            with self.subTest(alias=alias): self.invoke('base',alias=alias)
    def test_r11_output_cannot_replace_portable_settings(self): self.invoke('state')
    def test_r11_directory_output_is_diagnosed_before_environment_check(self): self.invoke('directory')
    def test_r11_output_cannot_replace_a_main_model_or_application_file(self):
        for kind in ('model','application','launcher','runtime','engine','vision','license','manifest'):
            with self.subTest(kind=kind): self.invoke(kind)
    def test_r11_independent_profiles_keep_all_original_bytes(self):
        self.invoke('profile',succeeds=True); self.invoke('root-profile',succeeds=True)


PACK_FILES = ('native_experts.txt','index.txt','dense.bin','tokenizer/vocab.json','tokenizer/chat_template.jinja')
MTP_FILES = ('experts.bin','dense.bin','dense.txt')


class PreparationFixture:
    def fixture(self, base):
        root = base/'app'; root.mkdir(); data = base/'data'; data.mkdir()
        catalog = {'repository':'fixture/model','official_revision':'rev','modelscope_revision':'rev',
                   'family':'qwen','model':'IQ3_S','files':[{'file':'main.gguf','size':7,'sha256':'0'*64}]}
        (root/'model-sources.json').write_text(json.dumps(catalog))
        for prefix,names in (('packs/iq3_s',PACK_FILES),('mtp/rt',MTP_FILES)):
            for name in names:
                path = data/prefix/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'fixture')
        return root,data,catalog

    def invoke(self, root, data, *, download=None, run=None, succeeds=False):
        def obtain(url,path,size,digest,**kwargs):
            path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'model!!')
        with mock.patch.object(prepare_model,'ROOT',root), \
             mock.patch.object(prepare_model.setup,'HF_REVISIONS',{'fixture/model':'rev'}), \
             mock.patch.object(prepare_model,'download',side_effect=download or obtain) as transfer, \
             mock.patch.object(prepare_model.subprocess,'run',side_effect=run) as command, \
             mock.patch('sys.argv',['prepare','--yes','--data-dir',str(data)]):
            if succeeds: prepare_model.main()
            else:
                with self.assertRaisesRegex((RuntimeError,ValueError),'incomplete|outside|invalid|Invalid|path|filename'):
                    prepare_model.main()
            return transfer.call_count,command.call_count



class ModelPreparation(PreparationFixture, unittest.TestCase):
    def late_change(self, action):
        with tempfile.TemporaryDirectory() as td:
            root,data,catalog = self.fixture(Path(td)); (data/'packs/iq3_s/dense.bin').unlink()
            descriptor = data/'portable-model.json'; descriptor.write_bytes(b'{"previous":"keep"}')
            def tool(command,**kwargs):
                (data/'packs/iq3_s/dense.bin').write_bytes(b'rebuilt')
                main = data/'models/IQ3_S/main.gguf'
                if action == 'delete': main.unlink()
                else: main.write_bytes(b'x')
            self.invoke(root,data,run=tool)
            self.assertEqual(descriptor.read_bytes(),b'{"previous":"keep"}')

    def test_r12_deleted_gguf_during_pack_cannot_publish_ready(self): self.late_change('delete')
    def test_r12_truncated_gguf_during_pack_cannot_publish_ready(self): self.late_change('truncate')
    def test_r12_missing_download_artifact_cannot_publish_ready(self):
        with tempfile.TemporaryDirectory() as td:
            root,data,catalog = self.fixture(Path(td))
            self.invoke(root,data,download=lambda *a,**k:None)
            self.assertFalse((data/'portable-model.json').exists())
    def test_r12_complete_shards_and_pack_are_published_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as td:
            root,data,catalog = self.fixture(Path(td))
            self.assertEqual(self.invoke(root,data,succeeds=True),(1,0))
            descriptor = json.loads((data/'portable-model.json').read_text())
            for relative in descriptor['required_files']: self.assertTrue((data/relative).is_file())


def junction(link,target):
    # A junction is created inside a temporary fixture; no external user path is involved.
    def quote(path): return "'"+str(path).replace("'","''")+"'"
    command = f"New-Item -ItemType Junction -Path {quote(link)} -Target {quote(target)} | Out-Null"
    result = subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True)
    if result.returncode: raise AssertionError(result.stderr.decode(errors='replace'))


class PreparationDestinations(PreparationFixture, unittest.TestCase):
    def invalid_catalog(self, filename):
        with tempfile.TemporaryDirectory() as td:
            root,data,catalog = self.fixture(Path(td)); catalog['files'][0]['file'] = str(Path(td)/'outside.gguf') if filename == '@absolute@' else filename
            (root/'model-sources.json').write_text(json.dumps(catalog))
            calls,_ = self.invoke(root,data)
            self.assertEqual(calls,0); self.assertFalse((data/'portable-model.json').exists())

    def test_r13_catalog_cannot_escape_with_parent_or_absolute_filename(self):
        for filename in ('../../../outside.gguf','@absolute@','..\\outside.gguf','NUL.gguf'):
            with self.subTest(filename=filename): self.invalid_catalog(filename)

    def test_r13_case_aliased_catalog_shards_are_refused_before_download(self):
        with tempfile.TemporaryDirectory() as td:
            root,data,catalog = self.fixture(Path(td))
            catalog['files'].append({**catalog['files'][0],'file':'MAIN.GGUF'})
            (root/'model-sources.json').write_text(json.dumps(catalog))
            calls,_ = self.invoke(root,data); self.assertEqual(calls,0)

    @unittest.skipUnless(os.name=='nt','Windows directory junctions')
    def test_r13_gguf_directory_junction_cannot_write_outside_model_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root,data,catalog = self.fixture(base); outside = base/'outside'; outside.mkdir()
            (data/'models').mkdir(); link = data/'models/IQ3_S'; junction(link,outside)
            try:
                calls,_ = self.invoke(root,data); self.assertEqual(calls,0)
                self.assertEqual(list(outside.iterdir()),[])
            finally: link.rmdir()

    @unittest.skipUnless(os.name=='nt','Windows directory junctions')
    def test_r13_pack_parent_junction_is_refused_before_earlier_downloads(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); root,data,catalog = self.fixture(base); outside = base/'outside'; outside.mkdir()
            import shutil
            pack = data/'packs/iq3_s'; shutil.rmtree(pack)
            junction(pack,outside)
            try:
                calls,commands = self.invoke(root,data); self.assertEqual((calls,commands),(0,0))
                self.assertEqual(list(outside.iterdir()),[])
            finally: pack.rmdir()

    def test_r13_linked_download_sidecar_is_refused_before_download(self):
        with tempfile.TemporaryDirectory() as td:
            root,data,catalog = self.fixture(Path(td))
            external = Path(td)/'external-stamp.json'; external.write_bytes(b'keep external')
            folder = data/'models/IQ3_S'; folder.mkdir(parents=True)
            content = b'model!!'; (folder/'main.gguf').write_bytes(content)
            catalog['files'][0]['sha256'] = hashlib.sha256(content).hexdigest()
            (root/'model-sources.json').write_text(json.dumps(catalog))
            link = folder/'main.gguf.verified.json'; link.symlink_to(external)
            from tools.portable_download import download
            try:
                calls,commands = self.invoke(root,data,download=download); self.assertEqual((calls,commands),(0,0))
                self.assertEqual(external.read_bytes(),b'keep external')
            finally: link.unlink()


class GeneratedConfiguration(unittest.TestCase):
    def invoke(self,config):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); cfg=root/'strata-iq3_s.json'; state=root/'portable-settings.json'
            state.write_bytes(b'{"user":"keep"}'); before=state.read_bytes()
            def setup(): portable.save_json(cfg,config); return 0
            with mock.patch.object(portable,'ROOT',root),mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable,'model_delivery',return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
                 mock.patch.object(portable.upstream,'main',side_effect=setup), \
                 mock.patch.object(portable,'fingerprint',return_value={'data':'fixture'}), mock.patch('sys.argv',['test']):
                with self.assertRaisesRegex(RuntimeError,'configuration'): portable.configure(root/'data')
            self.assertFalse(cfg.exists()); self.assertEqual(state.read_bytes(),before)

    def test_r14_generated_object_without_engine_arguments_cannot_complete(self): self.invoke({'port':8080})
    def test_r14_generated_executable_and_library_types_are_checked(self):
        for config in ({'args':[],'exe':[]},{'args':[],'lib_dirs':False},{'args':[],'cwd':3}):
            with self.subTest(config=config): self.invoke(config)
    def test_r14_generated_network_settings_are_checked(self):
        for key,value in (('port',True),('port',65536),('host',[]),('api_key',{})):
            with self.subTest(key=key,value=value): self.invoke({'args':[],key:value})
    def test_r14_generated_backend_is_a_supported_string(self):
        for backend in ([],False,'metal'):
            with self.subTest(backend=backend): self.invoke({'args':[],'backend':backend})



if __name__=='__main__': unittest.main()

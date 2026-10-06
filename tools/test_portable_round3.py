"""Third audit: reconfiguration transactions, archive ambiguity and update path conflicts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request
import zipfile

import portable
from tools import bootstrap_portable as bootstrap, portable_download as download, portable_update as update
from tools import prepare_portable_model as prepare_model
from tools import test_portable_update as fixtures


class ConfigureTransactions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root/'strata-iq3_s.json'
        self.state = self.root/'portable-settings.json'
        self.original = {'args': ['--native', 'main.gguf'], 'port': 8084, 'host': '127.0.0.1', 'api_key': 'fixture-secret',
                         'vision': {'gpu': False, 'max_tokens': 240, 'encode_timeout_s': 91, 'threads': 3}}
        portable.save_json(self.cfg, self.original)
        portable.save_json(self.state, {'portable_config': self.cfg.name, 'custom': 'keep'})
        self.before = {p: p.read_bytes() for p in (self.cfg, self.state)}
    def tearDown(self): self.tmp.cleanup()

    def invoke(self, *, failure=None, setup_result=0, port=None, vision=None):
        def setup():
            portable.save_json(self.cfg, {'args': ['--native', 'new.gguf'], 'port': port or 8080})
            portable.save_json(self.state, {'setup_changed': True})
            return setup_result
        def attach(cfg, model, mode, tokens):
            if failure == 'vision': raise RuntimeError('vision hash failed')
            cfg['vision']['gpu'] = mode == 'gpu'
            return cfg
        real_save = portable.save_json
        def save(path, value):
            if failure == 'state' and path == self.state and 'portable_fingerprint' in value:
                raise OSError('state save denied')
            real_save(path, value)
        with mock.patch.object(portable, 'ROOT', self.root), mock.patch.object(portable, 'STATE', self.state), \
             mock.patch.object(portable, 'model_delivery', return_value={'family': 'qwen', 'model': 'IQ3_S', 'gguf_dir': 'models'}), \
             mock.patch.object(portable.upstream, 'main', side_effect=setup), \
             mock.patch.object(portable, 'fingerprint', return_value={'version': 'new'}), \
             mock.patch.object(portable, 'attach_vision', side_effect=attach), \
             mock.patch.object(portable, 'save_json', side_effect=save), mock.patch.object(portable.sys, 'argv', []):
            return portable.configure(self.root/'model', context=32768, port=port, vision=vision)

    def test_context_reconfiguration_keeps_network_and_authentication(self):
        self.invoke()
        saved = portable.read_json(self.cfg)
        for key in ('port', 'host', 'api_key'): self.assertEqual(saved[key], self.original[key])
        self.assertEqual(saved['args'], ['--native', 'new.gguf'])
        self.assertEqual(portable.read_json(self.state)['custom'], 'keep')

    def test_explicit_port_overrides_previous_port_without_dropping_key(self):
        self.invoke(port=8093)
        self.assertEqual(portable.read_json(self.cfg)['port'], 8093)
        self.assertEqual(portable.read_json(self.cfg)['api_key'], self.original['api_key'])

    def test_automatic_vision_reconfiguration_keeps_cpu_mode_and_custom_limits(self):
        self.invoke(vision='auto')
        self.assertEqual(portable.read_json(self.cfg)['vision'], self.original['vision'])

    def test_failed_vision_validation_restores_both_files_exactly(self):
        with self.assertRaisesRegex(RuntimeError, 'hash failed'): self.invoke(failure='vision', vision='gpu')
        for p, content in self.before.items(): self.assertEqual(p.read_bytes(), content)

    def test_failed_state_commit_restores_run_configuration_and_state(self):
        with self.assertRaisesRegex(OSError, 'save denied'): self.invoke(failure='state')
        for p, content in self.before.items(): self.assertEqual(p.read_bytes(), content)

    def test_failed_upstream_setup_restores_previous_files(self):
        self.assertEqual(self.invoke(setup_result=2)[0], 2)
        for p, content in self.before.items(): self.assertEqual(p.read_bytes(), content)

    def test_first_install_failure_removes_new_partial_configuration(self):
        self.cfg.unlink(); self.state.unlink()
        with self.assertRaises(OSError): self.invoke(failure='state')
        self.assertFalse(self.cfg.exists()); self.assertFalse(self.state.exists())

    def test_rollback_restores_state_even_if_the_config_is_locked(self):
        original = portable.atomic_bytes
        def restore(path, content):
            if path == self.cfg: raise OSError('sharing violation')
            original(path, content)
        with mock.patch.object(portable, 'atomic_bytes', side_effect=restore):
            with self.assertRaisesRegex(RuntimeError, 'rollback incomplete.*strata-iq3_s'):
                self.invoke(setup_result=2)
        self.assertEqual(self.state.read_bytes(), self.before[self.state])


class ConfigurationPaths(unittest.TestCase):
    def test_bad_state_types_have_a_user_readable_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/'portable-settings.json'
            for value in ([], {'portable_data_dir': []}, {'portable_data_dir': 0}, {'portable_data_dir': 'a\x00b'}):
                state.write_text(json.dumps(value))
                with mock.patch.object(portable, 'STATE', state), self.assertRaisesRegex(RuntimeError, 'portable-settings'):
                    portable.read_state()

    def test_config_cannot_select_other_files_or_escape_application(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(portable, 'ROOT', Path(tmp)):
            for name in (None, '../outside.json', 'C:/external.json', 'meta.json', 'sub/strata-iq3_s.json'):
                with self.subTest(name=name), self.assertRaises(RuntimeError): portable.config_path({'portable_config': name})
            self.assertEqual(portable.config_path({'portable_config': 'strata-iq3_s.json'}), Path(tmp)/'strata-iq3_s.json')

    def test_bad_engine_or_vision_types_fail_before_native_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'config.json'
            for value in ([], {'args': 'wrong'}, {'args': [None]}, {'vision': ['wrong']}):
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(RuntimeError, 'run configuration'): portable.read_config(path)

    def test_legacy_null_vision_remains_a_valid_disabled_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'; path.write_text('{"args":[],"vision":null}')
            self.assertIsNone(portable.read_config(path)['vision'])

    def test_no_models_refresh_disables_only_bundled_vision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'meta.json').write_text(json.dumps({'edition': 'Portable-NoModels'}))
            for bundled in (True, False):
                path = root/'strata-test.json'
                config = {'args': ['--vision', '--native', 'main.gguf'], 'vision': {'bundled': bundled, 'mmproj': 'custom.gguf'}}
                portable.save_json(path, config)
                with mock.patch.object(portable, 'ROOT', root), mock.patch.object(portable, 'STATE', root/'state.json'), \
                     mock.patch.object(portable.upstream, 'cuda_lib_dirs', return_value=[]), \
                     mock.patch.object(portable.upstream, 'upgrade_config', side_effect=lambda p,c:c):
                    portable.refresh_updated_config(path, {}, {'version': 'new'})
                saved = portable.read_json(path)
                self.assertEqual('vision' in saved, not bundled)
                self.assertEqual('--vision' in saved['args'], not bundled)


class EditionTransitions(unittest.TestCase):
    def start(self, previous_edition, current_edition, *, disabled=False, failed=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cfg=root/'strata-test.json'; state=root/'state.json'
            original={'args':['--native','main.gguf'],'port':8084,'api_key':'fixture-secret'}
            if previous_edition=='VisionReady-NoMainModel':
                original['vision']={'bundled':True}; original['args'].append('--vision')
            portable.save_json(cfg,original)
            previous={'version':'0.1.39-t8.9','edition':previous_edition,'data':str(root/'model')}
            current=dict(previous,edition=current_edition)
            portable.save_json(state,{'portable_config':cfg.name,'portable_fingerprint':previous})
            (root/'meta.json').write_text(json.dumps({'edition':current_edition}))
            before=state.read_bytes()
            def attach(config,*args):
                if failed: raise RuntimeError('encoder failed verification')
                config['vision']={'bundled':True,'gpu':True}; config['args'].append('--vision'); return config
            argv=['portable','start','--no-browser']+(['--vision','no'] if disabled else [])
            with mock.patch.object(portable,'ROOT',root), mock.patch.object(portable,'STATE',state), \
                 mock.patch.object(portable,'environment_check'), mock.patch.object(portable,'isolate_setup'), \
                 mock.patch.object(portable,'data_path',return_value=root/'model'), \
                 mock.patch.object(portable,'model_delivery',return_value={'family':'qwen'}), \
                 mock.patch.object(portable,'fingerprint',return_value=current), \
                 mock.patch.object(portable,'configure') as configure, \
                 mock.patch.object(portable,'attach_vision',side_effect=attach) as vision, \
                 mock.patch.object(portable.upstream,'cuda_lib_dirs',return_value=[]), \
                 mock.patch.object(portable.upstream,'upgrade_config',side_effect=lambda p,c:c), \
                 mock.patch.object(portable.threading,'Thread'), mock.patch.object(portable.os,'chdir'), \
                 mock.patch.object(portable.subprocess,'call',return_value=0) as server, mock.patch('sys.argv',argv):
                if failed:
                    with self.assertRaisesRegex(RuntimeError,'verification'): portable.main()
                    self.assertEqual(state.read_bytes(),before); server.assert_not_called()
                else:
                    self.assertEqual(portable.main(),0)
                    self.assertEqual(portable.read_json(state)['portable_fingerprint']['edition'],current_edition)
                configure.assert_not_called()
                saved=portable.read_json(cfg)
                self.assertEqual(saved['port'],8084); self.assertEqual(saved['api_key'],'fixture-secret')
                return saved,vision.call_count

    def test_same_version_downgrade_disables_bundled_vision_without_reconfiguration(self):
        saved,calls=self.start('VisionReady-NoMainModel','Portable-NoModels')
        self.assertNotIn('vision',saved); self.assertNotIn('--vision',saved['args']); self.assertEqual(calls,0)

    def test_same_version_install_vision_enables_the_shipped_encoder(self):
        saved,calls=self.start('Portable-NoModels','VisionReady-NoMainModel')
        self.assertTrue(saved['vision']['gpu']); self.assertEqual(calls,1)

    def test_explicit_no_vision_is_kept_during_edition_switch(self):
        saved,calls=self.start('Portable-NoModels','VisionReady-NoMainModel',disabled=True)
        self.assertNotIn('vision',saved); self.assertEqual(calls,0)

    def test_failed_encoder_check_keeps_previous_edition_for_retry(self):
        saved,calls=self.start('Portable-NoModels','VisionReady-NoMainModel',failed=True)
        self.assertNotIn('vision',saved); self.assertEqual(calls,1)


def junction(link, target):
    result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
    if result.returncode: raise AssertionError(result.stderr.decode(errors='replace'))


@unittest.skipUnless(os.name == 'nt', 'Windows directory junctions')
class LinkedDelivery(unittest.TestCase):
    def test_mtp_cannot_resolve_outside_the_delivered_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); data = base/'data'; outside = base/'outside'
            (data/'models').mkdir(parents=True); outside.mkdir()
            for name in ('experts.bin', 'dense.bin', 'dense.txt'): (outside/name).write_bytes(b'fixture')
            (data/'mtp').mkdir(); junction(data/'mtp/rt', outside)
            (data/'portable-model.json').write_text(json.dumps({'family': 'qwen', 'model': 'IQ3_S', 'gguf_dir': 'models'}))
            try:
                with self.assertRaisesRegex(RuntimeError, 'MTP'): portable.model_delivery(data)
                self.assertEqual((outside/'experts.bin').read_bytes(), b'fixture')
            finally: (data/'mtp/rt').rmdir()


class PreparedPack(unittest.TestCase):
    def test_missing_chat_template_or_empty_completion_marker_rebuilds_the_pack(self):
        for missing in ('tokenizer/chat_template.jinja', 'native_experts.txt'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)/'app'; root.mkdir(); data = Path(tmp)/'data'
                catalog = {'repository': 'fixture/model', 'official_revision': 'rev', 'modelscope_revision': 'rev',
                           'family': 'qwen', 'model': 'IQ3_S', 'files': [{'file': 'main.gguf', 'size': 1, 'sha256': '0'*64}]}
                (root/'model-sources.json').write_text(json.dumps(catalog))
                pack = data/'packs/iq3_s'
                for name in ('native_experts.txt', 'index.txt', 'dense.bin', 'tokenizer/vocab.json', 'tokenizer/chat_template.jinja'):
                    p = pack/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('fixture')
                (pack/missing).write_bytes(b'')
                for name in ('experts.bin', 'dense.bin', 'dense.txt'):
                    p = data/'mtp/rt'/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('fixture')
                def rebuild(command, **kwargs):
                    (pack/missing).write_text('rebuilt')
                def downloaded(url, path, size, *args, **kwargs):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'x'*size)
                with mock.patch.object(prepare_model, 'ROOT', root), \
                     mock.patch.object(prepare_model.setup, 'HF_REVISIONS', {'fixture/model': 'rev'}), \
                     mock.patch.object(prepare_model, 'download', side_effect=downloaded), mock.patch.object(prepare_model.subprocess, 'run', side_effect=rebuild) as run, \
                     mock.patch('sys.argv', ['prepare', '--yes', '--data-dir', str(data)]):
                    prepare_model.main()
                self.assertEqual(len(run.call_args_list), 1)
                self.assertIn('iq_pack.py', str(run.call_args.args[0]))


class ArchivePreflight(unittest.TestCase):
    def test_unsafe_or_ambiguous_bootstrap_archives_change_nothing(self):
        invalid = [['safe.txt', 'SAFE.txt'], ['safe.txt', 'NUL.txt'], ['safe.txt', 'nested/a.'],
                   ['safe.txt', 'nested/a\x01b'], ['safe.txt', 'a', 'a/b'], ['safe.txt', '../escape']]
        for names in invalid:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); archive=root/'test.zip'; target=root/'out'
                with zipfile.ZipFile(archive,'w') as z:
                    for name in names: z.writestr(name,'fixture')
                with self.assertRaises(ValueError): bootstrap.extract(archive,target)
                self.assertFalse(target.exists())

    def test_zip_symlink_is_rejected_before_safe_file_is_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); archive=root/'test.zip'; target=root/'out'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('safe.txt','fixture')
                link=zipfile.ZipInfo('link'); link.external_attr=0o120777 << 16; z.writestr(link,'safe.txt')
            with self.assertRaisesRegex(ValueError,'Linked'): bootstrap.extract(archive,target)
            self.assertFalse(target.exists())

    def test_existing_parent_file_is_found_before_any_archive_member_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); target=root/'out'; target.mkdir(); (target/'nested').write_text('original')
            archive=root/'test.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('safe.txt','fixture'); z.writestr('nested/file.txt','fixture')
            with self.assertRaisesRegex(ValueError,'Conflicting'): bootstrap.extract(archive,target)
            self.assertFalse((target/'safe.txt').exists()); self.assertEqual((target/'nested').read_text(),'original')

    def test_official_cached_engine_and_python_archives_pass_preflight(self):
        # Full vendor downloads are not needed: validate all cached member metadata only.
        source = Path(__file__).resolve().parents[1]
        for name in ('python-3.12.10-embed-amd64.zip', 'strata-windows-x64.zip', 'strata-windows-x64-hip.zip'):
            path=source/'.portable-build'/name
            if path.exists():
                with tempfile.TemporaryDirectory() as tmp, mock.patch.object(zipfile.ZipFile,'extractall') as extract:
                    bootstrap.extract(path,Path(tmp)); extract.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows directory junctions')
    def test_existing_extraction_junction_is_never_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); target=root/'out'; target.mkdir(); outside=root/'outside'; outside.mkdir()
            junction(target/'nested',outside)
            archive=root/'test.zip'
            with zipfile.ZipFile(archive,'w') as z: z.writestr('nested/file.txt','fixture')
            try:
                with self.assertRaises(ValueError): bootstrap.extract(archive,target)
                self.assertEqual(list(outside.iterdir()),[])
            finally: (target/'nested').rmdir()


class StrictMetadata(unittest.TestCase):
    def test_malformed_entries_and_boolean_sizes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); original=fixtures.package(root, {'a':'x'})
            for value in ([], dict(original,files=None), dict(original,files=[None]),
                          dict(original,files=[dict(original['files'][0],size=True)]),
                          dict(original,files=[dict(original['files'][0],sha256=None)])):
                with self.subTest(value=value), self.assertRaises(ValueError): update.validate_manifest(root,value,verify=False)

    def test_weight_roles_require_boolean_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); manifest=fixtures.package(root,{'a':'x'})
            manifest['weights']={'main':0,'mtp':0,'vision':0}
            with self.assertRaisesRegex(ValueError,'roles'): update.validate_manifest(root,manifest,verify=False)

    def test_file_and_parent_file_cannot_share_a_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); manifest=fixtures.package(root,{'a':'x'})
            manifest['files'].append(dict(manifest['files'][0],path='a/b'))
            with self.assertRaisesRegex(ValueError,'Conflicting'): update.validate_manifest(root,manifest,verify=False)

    def test_control_characters_are_refused_by_release_paths(self):
        for name in ('a\x00b','a\x01b','a\x7fb'):
            with self.assertRaises(ValueError): update.safe_path(Path('.'),name)

    def test_invalid_download_parallelism_is_rejected_before_creating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'new/file'
            for workers, chunk in ((True,1),(1.2,1),(1,True),(1,1.5),(None,1)):
                with self.assertRaises(ValueError): download.download('https://unused',target,1,'0'*64,workers,chunk)
            self.assertFalse(target.parent.exists())


class ExistingUpdatePlan(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    fixture = fixtures.ReleaseValidation.fixture

    def test_failed_new_prepare_preserves_a_previous_verified_plan(self):
        root,release,fetch=self.fixture(); control=root/'.portable-update'; control.mkdir()
        path=control/'plan.json'; previous=b'{"previous_verified_plan":true}'; path.write_bytes(previous)
        stage=self.base/'stage'; stage.mkdir()
        def fail(url,destination=None,limit=None):
            if destination: raise OSError('offline')
            return fetch(url,destination,limit)
        with mock.patch.object(update,'fetch',side_effect=fail), mock.patch.object(update,'running_processes',return_value=[]), \
             mock.patch.object(update.tempfile,'mkdtemp',return_value=str(stage)):
            with self.assertRaises(OSError): update.prepare(root,release)
        self.assertEqual(path.read_bytes(),previous); self.assertFalse(stage.exists())

    def test_noop_clears_a_stale_plan_for_a_different_pending_edition(self):
        root,release,fetch=self.fixture(); control=root/'.portable-update'; control.mkdir()
        path=control/'plan.json'; path.write_text('{"edition":"VisionReady-NoMainModel"}')
        release=dict(release,tag_name='v0.1.39-t8.1')
        with mock.patch.object(update,'running_processes',return_value=[]), mock.patch.object(update,'fetch') as fetch:
            self.assertIsNone(update.prepare(root,release,edition='Portable-NoModels'))
        fetch.assert_not_called(); self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows directory junctions')
    def test_control_directory_junction_cannot_modify_an_external_plan(self):
        root,release,fetch=self.fixture(); outside=self.base/'external'; outside.mkdir()
        (outside/'plan.json').write_text('external plan'); junction(root/'.portable-update',outside)
        try:
            with mock.patch.object(update,'fetch',side_effect=OSError('simulated offline')), \
                 mock.patch.object(update,'running_processes',return_value=[]), self.assertRaisesRegex(ValueError,'control path'):
                update.prepare(root,release)
            self.assertEqual((outside/'plan.json').read_text(),'external plan')
        finally: (root/'.portable-update').rmdir()


@unittest.skipUnless(os.name == 'nt', 'real PowerShell replacement')
class UpdateDirectoryPreflight(unittest.TestCase):
    setUp = fixtures.WindowsApply.setUp
    tearDown = fixtures.WindowsApply.tearDown
    plan = fixtures.WindowsApply.plan
    apply = fixtures.WindowsApply.apply

    def test_overlapping_backup_or_stage_changes_nothing(self):
        for field in ('backup','stage'):
            with self.subTest(field=field):
                root,incoming,path=self.plan()
                plan=json.loads(path.read_text()); plan[field]=str(root/'nested')
                if field=='stage': (root/'nested').mkdir()
                path.write_text(json.dumps(plan))
                result=self.apply(path)
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual((root/'app.py').read_text(),'old')
                # Isolate the next subtest without deleting outside the fixture directory.
                import shutil
                shutil.rmtree(root); shutil.rmtree(incoming)

    def test_blocked_destination_is_found_before_any_backup_is_created(self):
        root,_,path=self.plan({'blocked.py':'new'}); (root/'blocked.py').mkdir()
        result=self.apply(path)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.base/'backup').exists())
        self.assertEqual((root/'app.py').read_text(),'old')

    def test_backup_parent_junction_is_refused_without_moving_files(self):
        root,_,path=self.plan(); outside=self.base/'external'; outside.mkdir(); link=self.base/'linked'
        junction(link,outside)
        plan=json.loads(path.read_text()); plan['backup']=str(link/'backup'); path.write_text(json.dumps(plan))
        try:
            result=self.apply(path); self.assertNotEqual(result.returncode,0)
            self.assertEqual(list(outside.iterdir()),[]); self.assertEqual((root/'app.py').read_text(),'old')
        finally: link.rmdir()


class StructuredHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from serve.frontend import ChatTemplate
        from serve.server import ByteTokenizer, MockEngine, Service, serve
        tok = ByteTokenizer()
        cls.engine = MockEngine(tok, '</think>\n\n{"value":{"$ref":"https://fixture.invalid/data"}}', max_context=16384)
        cls.server = serve(Service(cls.engine, tok, ChatTemplate(Path(__file__).resolve().parents[1]/'serve/chat_template.jinja')), port=0)
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
    def post(self, schema):
        request = urllib.request.Request(self.url+'/v1/chat/completions', data=json.dumps({
            'model':'strata', 'messages':[{'role':'user','content':'fixture'}], 'max_tokens':256,
            'response_format':{'type':'json_schema','json_schema':{'name':'fixture','schema':schema}}
        }).encode(), headers={'Content-Type':'application/json'})
        try:
            response=urllib.request.urlopen(request,timeout=10)
        except urllib.error.HTTPError as error:
            response=error
        with response: return response.status,json.loads(response.read())

    def test_reference_inside_constant_is_ordinary_json_data(self):
        schema={'type':'object','required':['value'],'properties':{'value':{'const':{'$ref':'https://fixture.invalid/data'}}}}
        status,body=self.post(schema)
        self.assertEqual(status,200,body)
        self.assertEqual(json.loads(body['choices'][0]['message']['content'])['value']['$ref'],'https://fixture.invalid/data')

    def test_missing_local_reference_is_rejected_before_engine_generation(self):
        with mock.patch.object(self.engine,'generate', wraps=self.engine.generate) as generate:
            status,body=self.post({'type':'object','properties':{'value':{'$ref':'#/$defs/missing'}}})
        self.assertEqual(status,400,body); generate.assert_not_called()

    def test_indirect_reference_cannot_execute_a_remote_reference_hidden_in_constant(self):
        schema={'type':'object','properties':{'value':{'$ref':'#/$defs/container/const'}},
                '$defs':{'container':{'const':{'$ref':'https://fixture.invalid/schema'}}}}
        status,body=self.post(schema)
        self.assertEqual(status,400,body)

    def test_local_cycle_is_a_controlled_error_and_the_service_stays_healthy(self):
        status,body=self.post({'type':'object','$ref':'#'})
        self.assertEqual(status,502,body); self.assertEqual(body['error']['code'],'structured_output_failed')
        with urllib.request.urlopen(self.url+'/health',timeout=5) as response:
            self.assertEqual(json.loads(response.read())['status'],'ok')

    def test_draft4_draft7_and_2020_local_definitions_and_literal_data_remain_valid(self):
        for dialect in ('http://json-schema.org/draft-04/schema#', 'http://json-schema.org/draft-07/schema#',
                        'https://json-schema.org/draft/2020-12/schema'):
            schema={'$schema':dialect,'type':'object','required':['value'],
                    'properties':{'value':{'$ref':'#/definitions/value'}},
                    'definitions':{'value':{'enum':[{'$ref':'https://fixture.invalid/data'}]}}}
            with self.subTest(dialect=dialect):
                status,body=self.post(schema); self.assertEqual(status,200,body)

    def test_local_anchor_and_recursive_dynamic_anchor_remain_valid(self):
        schemas=[{'type':'object','properties':{'value':{'$ref':'#value'}},
                  '$defs':{'value':{'$anchor':'value','type':'object'}}},
                 {'type':'object','$dynamicAnchor':'node','properties':{'value':{'$dynamicRef':'#node'}}}]
        for schema in schemas:
            with self.subTest(schema=schema):
                status,body=self.post(schema); self.assertEqual(status,200,body)

    def test_old_drafts_keep_unknown_dynamic_reference_annotations(self):
        for dialect, annotation in (('http://json-schema.org/draft-04/schema#', False),
                                    ('http://json-schema.org/draft-07/schema#', None)):
            schema={'$schema':dialect,'type':'object','$dynamicRef':annotation}
            with self.subTest(dialect=dialect):
                status,body=self.post(schema); self.assertEqual(status,200,body)

    def test_2019_recursive_refs_are_preflighted_as_active_keywords(self):
        schema={'$schema':'https://json-schema.org/draft/2019-09/schema', 'type':'object',
                'properties':{'value':{'$recursiveRef':'https://fixture.invalid/schema'}}}
        status,body=self.post(schema); self.assertEqual(status,400,body)


if __name__ == '__main__': unittest.main()

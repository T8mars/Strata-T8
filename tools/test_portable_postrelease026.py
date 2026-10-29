"""Post-release checks for independently managed model profiles."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import portable
from tools import managed_config, portable_deepseek as ds
from tools import test_portable_deepseek as ds_fixture


class ManagedEngineSelection(unittest.TestCase):
    def test_selected_engine_and_matching_runtime_are_preserved(self):
        for backend, selected, toolkit in (
                ('cuda', 'engine-cuda12', 12), ('cuda', 'engine', 13),
                ('hip', 'engine-hip-rdna2', None), ('hip', 'engine-hip-rdna3', None)):
            with self.subTest(backend=backend, selected=selected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state = root/'portable-settings.json'
                base = root/'strata-iq3_s.json'
                output = root/'managed/service.json'
                cfg = {'backend':backend, 'args':['--native', 'main.gguf'], 'toolkit':toolkit}
                portable.save_json(base, cfg)
                portable.save_json(state, {'portable_fingerprint':{'data':'fixture'}})
                before = {p:p.read_bytes() for p in (base, state)}
                engine = root/selected
                with mock.patch.object(portable, 'ROOT', root), mock.patch.object(portable, 'STATE', state), \
                        mock.patch.object(portable, 'environment_check'), mock.patch.object(portable, 'isolate_setup'), \
                        mock.patch.object(portable, 'model_delivery', return_value={'family':'qwen','model':'IQ3_S','gguf_dir':'models'}), \
                        mock.patch.object(portable, 'fingerprint', return_value={'data':'fixture'}), \
                        mock.patch.object(portable, 'configured_engine', return_value=engine) as select, \
                        mock.patch.object(portable.upstream, 'config_toolkit', return_value=toolkit), \
                        mock.patch.object(portable.upstream, 'cuda_lib_dirs', return_value=[root/'cuda-runtime']) as cuda, \
                        mock.patch.object(portable.upstream, 'hip_lib_dirs', return_value=[engine]) as hip, \
                        mock.patch('sys.argv', ['managed','--data-dir',str(root/'data'),'--output',str(output),'--vision','no']):
                    self.assertEqual(managed_config.main(), 0)
                actual = portable.read_json(output)
                self.assertEqual(actual['exe'], str(engine/'strata.exe'))
                select.assert_called_once()
                if backend == 'cuda':
                    cuda.assert_called_once_with(toolkit)
                    self.assertIn(str(engine), actual['lib_dirs'])
                else:
                    hip.assert_called_once_with(engine)
                for path, content in before.items():
                    self.assertEqual(path.read_bytes(), content)

    def test_new_engine_directories_cannot_be_profile_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with mock.patch.object(portable, 'ROOT', root), mock.patch.object(portable, 'STATE', root/'portable-settings.json'):
                for name in ('engine-cuda12','engine-dsv41','engine-hip-rdna2','engine-hip-rdna3'):
                    with self.subTest(name=name):
                        self.assertTrue(managed_config.protected_output(root/name/'BUILD.json', root/'model'))


class ManagedDeepSeek(unittest.TestCase):
    setUp = ds_fixture.PortablePack.setUp

    def invoke(self, *, vision='no', context=32768, failure=False):
        (self.root/'engine-dsv41').mkdir()
        (self.root/'engine-dsv41/strata-dsv41.exe').write_bytes(b'fixture')
        state = self.root/'portable-settings.json'
        output = self.root/'managed/service.json'
        with mock.patch.object(portable, 'ROOT', self.root), mock.patch.object(portable, 'STATE', state), \
                mock.patch.object(portable, 'fingerprint', return_value={'fixture':True}), \
                mock.patch.object(portable, 'environment_check'), mock.patch.object(portable, 'isolate_setup'):
            _, web = ds.configure(portable, self.pack, 4096, speed='faster')
            original = portable.read_json(web)
            original.update(api_key='private-web-key', host='0.0.0.0', before_load=['do-not-run'])
            original['args'] += ['--cpu-threads','7']
            portable.save_json(web, original)
            before = {p:p.read_bytes() for p in (web,state)}
            with mock.patch('sys.argv', ['managed','--data-dir',str(self.pack),'--output',str(output),
                                         '--vision',vision,'--context',str(context)]):
                if failure:
                    with self.assertRaisesRegex(RuntimeError, 'text-only'):
                        managed_config.main()
                    self.assertFalse(output.exists())
                    result = None
                else:
                    self.assertEqual(managed_config.main(), 0)
                    result = portable.read_json(output)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)
            return result

    def test_text_profile_uses_deepseek_engine_without_changing_web_state(self):
        cfg = self.invoke()
        self.assertEqual(cfg['arch'], 'deepseek41')
        self.assertEqual(cfg['exe'], str(self.root/'engine-dsv41/strata-dsv41.exe'))
        self.assertEqual(cfg['lib_dirs'], [str(self.root/'engine-dsv41')])
        self.assertTrue(portable.context_matches(cfg, 8192))
        self.assertEqual(cfg['args'][cfg['args'].index('--experts')+1], '5')
        self.assertEqual(cfg['args'][cfg['args'].index('--vision')+1], 'off')
        self.assertIn('--cpu-threads', cfg['args'])
        self.assertNotIn('api_key', cfg)
        self.assertEqual(cfg['host'], '127.0.0.1')
        self.assertIsNone(cfg['before_load'])

    def test_explicit_vision_is_rejected_without_writes(self):
        self.invoke(vision='gpu', failure=True)

    def test_fresh_text_profile_never_creates_web_configuration_or_state(self):
        (self.root/'engine-dsv41').mkdir()
        (self.root/'engine-dsv41/strata-dsv41.exe').write_bytes(b'fixture')
        state = self.root/'portable-settings.json'
        output = self.root/'managed/service.json'
        with mock.patch.object(portable, 'ROOT', self.root), mock.patch.object(portable, 'STATE', state), \
                mock.patch.object(portable, 'environment_check'), mock.patch.object(portable, 'isolate_setup'), \
                mock.patch('sys.argv', ['managed','--data-dir',str(self.pack),'--output',str(output),
                                       '--vision','no','--context','2048']):
            self.assertEqual(managed_config.main(), 0)
        cfg = portable.read_json(output)
        self.assertTrue(portable.context_matches(cfg, 2048))
        self.assertEqual(cfg['args'][cfg['args'].index('--experts')+1], '6')
        self.assertFalse(state.exists())
        self.assertEqual(list(self.root.glob('strata-deepseek41-*.json')), [])

    def test_failed_managed_write_does_not_create_web_files(self):
        (self.root/'engine-dsv41').mkdir()
        (self.root/'engine-dsv41/strata-dsv41.exe').write_bytes(b'fixture')
        state = self.root/'portable-settings.json'
        output = self.root/'managed/service.json'
        with mock.patch.object(portable, 'ROOT', self.root), mock.patch.object(portable, 'STATE', state), \
                mock.patch.object(portable, 'environment_check'), mock.patch.object(portable, 'isolate_setup'), \
                mock.patch.object(portable, 'save_json', side_effect=OSError('write failed')) as save, \
                mock.patch('sys.argv', ['managed','--data-dir',str(self.pack),'--output',str(output),'--vision','no']):
            with self.assertRaisesRegex(OSError, 'write failed'):
                managed_config.main()
        self.assertEqual(save.call_args.args[0], output.resolve())
        self.assertFalse(output.exists())
        self.assertFalse(state.exists())
        self.assertEqual(list(self.root.glob('strata-deepseek41-*.json')), [])


if __name__ == '__main__':
    unittest.main()

"""Normal startup may prepare local data but cannot fetch MTP in a child process."""
import unittest
from pathlib import Path
import tempfile
from unittest import mock
import portable


class OfflineLauncher(unittest.TestCase):
    def test_older_windows_is_rejected_before_runtime_or_model_loading(self):
        with mock.patch.object(portable.os, 'name', 'nt'), \
             mock.patch.object(portable.sys, 'getwindowsversion', return_value=mock.Mock(build=17763), create=True):
            with self.assertRaisesRegex(RuntimeError, 'Windows 10 1903'):
                portable.environment_check()

    def guards(self):
        names = ['settings_path', 'other_installs', 'data_folder', 'get_llama_cpp', 'pip_install', 'download', 'get_prebuilt_hip', 'run']
        return mock.patch.multiple(portable.upstream, **{name: mock.DEFAULT for name in names})

    def test_corrupt_delivery_cannot_trigger_child_model_download(self):
        with self.guards() as originals, mock.patch('urllib.request.urlopen'):
            portable.isolate_setup()
            with self.assertRaisesRegex(RuntimeError, 'PREPARE-MODEL'):
                portable.upstream.run(['python', str(portable.ROOT/'tools/mtp_fetch.py'), 'fetch', '--out', 'missing'])
            originals['run'].assert_not_called()

    def test_local_preparation_is_allowed_and_network_guard_is_restored(self):
        with self.guards() as originals, mock.patch('urllib.request.urlopen'):
            originals['run'].return_value = 'prepared'
            portable.isolate_setup()
            command = ['python', str(portable.ROOT/'tools/iq_pack.py'), '--gguf', 'local.gguf']
            self.assertEqual(portable.upstream.run(command, check=True), 'prepared')
            originals['run'].assert_called_once_with(command, check=True)
            with self.assertRaisesRegex(RuntimeError, 'Offline package'):
                portable.upstream.download('https://example.invalid/model', 'model.gguf')


class UpdateConfiguration(unittest.TestCase):
    def test_only_version_changes_are_ignored(self):
        before = {'app': 'app', 'data': 'model', 'gpu': [0], 'ram': 128, 'version': 'old'}
        after = dict(before, version='new')
        self.assertTrue(portable.same_machine(before, after))
        for field, value in [('app', 'moved'), ('data', 'other'), ('gpu', [1]), ('ram', 64)]:
            self.assertFalse(portable.same_machine(before, dict(after, **{field: value})))
        self.assertFalse(portable.same_machine(None, after))

    def test_updated_runtime_keeps_user_settings_for_both_backends(self):
        for backend in ['cuda', 'hip']:
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg = root/'strata-test.json'
                config = {'exe': 'old.exe', 'cwd': 'old', 'lib_dirs': ['old/lib'], 'backend': backend,
                          'args': ['--max-context', '32768', '--pool-workers', '7', '--kv', 'int8', '--custom', 'value'],
                          'port': 8081, 'host': '127.0.0.1', 'api_key': 'test-secret', 'sampling': {'temperature': .2},
                          'tokenizer': 'separate/model/tokenizer', 'mcp_servers': {'example': {'command': 'test'}}}
                portable.save_json(cfg, config)
                state = {'portable_fingerprint': {'version': 'old'}, 'portable_data_dir': 'separate/model'}
                with mock.patch.object(portable, 'ROOT', root), mock.patch.object(portable, 'STATE', root/'state.json'), \
                     mock.patch.object(portable.upstream, 'cuda_lib_dirs', return_value=[root/'cuda']), \
                     mock.patch.object(portable.upstream, 'hip_lib_dirs', return_value=[root/'hip']), \
                     mock.patch.object(portable.upstream, 'upgrade_config', side_effect=lambda p, c: c) as upgrade:
                    portable.refresh_updated_config(cfg, state, {'version': 'new'})
                saved = portable.read_json(cfg)
                for key in set(config)-{'exe', 'cwd', 'lib_dirs'}:
                    self.assertEqual(saved[key], config[key])
                engine = 'engine-hip' if backend == 'hip' else 'engine'
                self.assertEqual(saved['exe'], str(root/engine/'strata.exe'))
                self.assertEqual(saved['lib_dirs'], [str(root/backend)])
                self.assertEqual(portable.read_json(root/'state.json')['portable_fingerprint']['version'], 'new')
                upgrade.assert_called_once()

    def test_first_start_after_update_does_not_rerun_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root/'strata-test.json'
            portable.save_json(cfg, {'port': 8081})
            portable.save_json(root/'state.json', {'portable_config': cfg.name, 'portable_fingerprint': {'gpu': [0], 'version': 'old'}})
            with mock.patch.object(portable, 'ROOT', root), mock.patch.object(portable, 'STATE', root/'state.json'), \
                 mock.patch('sys.argv', ['portable.py', 'start', '--no-browser']), mock.patch('os.chdir'), \
                 mock.patch.object(portable, 'environment_check'), mock.patch.object(portable, 'isolate_setup'), \
                 mock.patch.object(portable, 'data_path', return_value=root/'model'), mock.patch.object(portable, 'model_delivery'), \
                 mock.patch.object(portable, 'fingerprint', return_value={'gpu': [0], 'version': 'new'}), \
                 mock.patch.object(portable, 'configure') as configure, mock.patch.object(portable, 'refresh_updated_config') as refresh, \
                 mock.patch('threading.Thread'), mock.patch('subprocess.call', return_value=0) as start:
                self.assertEqual(portable.main(), 0)
                configure.assert_not_called()
                refresh.assert_called_once()
                self.assertIn('8081', start.call_args.args[0])


if __name__ == '__main__': unittest.main()

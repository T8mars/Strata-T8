"""Managed variant identity and saved-option boundaries from the second audit."""
from pathlib import Path
import tempfile
import socket
import unittest
from types import SimpleNamespace
from unittest import mock

import portable
from tools import managed_config
from serve import portable_ui as ui
from tools import test_portable_ui as ui_fixture


class ManagedModelIdentity(unittest.TestCase):
    def invoke(self, *, cached, variant=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            base = root/'strata-iq3_s.json'
            state = root/'portable-settings.json'
            output = root/'managed/service.json'
            config = {'args':['--native','fixture.gguf','--max-context','32768'],
                      'backend':'cuda', 'model_name':'qwen3.8-flash-next-iq3_s'}
            descriptor = {'family':'qwen','model':'IQ3_S','gguf_dir':'models'}
            if variant:
                descriptor['model_name'] = 'sc117-qwen3.8-flash-next-abliterated-iq3_s'
            portable.save_json(base, config)
            portable.save_json(state, {'portable_fingerprint':{'data':'fixture'}})
            before = {path:path.read_bytes() for path in (base, state)}
            with mock.patch.object(portable, 'ROOT', root), mock.patch.object(portable, 'STATE', state), \
                    mock.patch.object(portable, 'environment_check'), mock.patch.object(portable, 'isolate_setup'), \
                    mock.patch.object(portable, 'model_delivery', return_value=descriptor), \
                    mock.patch.object(portable, 'fingerprint', return_value={'data':'fixture'}), \
                    mock.patch.object(portable, 'same_machine', return_value=cached), \
                    mock.patch.object(managed_config, 'fresh_config', return_value=dict(config)), \
                    mock.patch.object(portable, 'configured_engine', return_value=root/'engine'), \
                    mock.patch.object(portable.upstream, 'cuda_lib_dirs', return_value=[]), \
                    mock.patch('sys.argv', ['managed','--data-dir',str(root/'model'),
                                           '--output',str(output),'--vision','no']):
                self.assertEqual(managed_config.main(), 0)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)
            return portable.read_json(output)

    def test_variant_model_id_is_consistent_for_fresh_and_cached_profiles(self):
        for cached in (False, True):
            with self.subTest(cached=cached):
                config = self.invoke(cached=cached)
                self.assertEqual(config['model_name'], 'sc117-qwen3.8-flash-next-abliterated-iq3_s')

    def test_official_descriptor_without_override_keeps_existing_id(self):
        self.assertEqual(self.invoke(cached=True, variant=False)['model_name'], 'qwen3.8-flash-next-iq3_s')


class GuiLaunchOverrides(unittest.TestCase):
    setUp = ui_fixture.SetupPage.setUp
    tearDown = ui_fixture.SetupPage.tearDown
    request = ui_fixture.SetupPage.request
    model = ui_fixture.SetupPage.model
    payload = ui_fixture.SetupPage.payload
    wait_job = ui_fixture.SetupPage.wait_job
    fake_popen = ui_fixture.SetupPage.fake_popen

    def test_gui_port_selection_replaces_original_launch_override(self):
        payload = self.payload()
        original_port = ui.free_port(exclude=[payload['port'], self.http.server_port])
        launch_options = SimpleNamespace(port=original_port, context=2048, backend='cuda', vision='cpu', vision_tokens=513)
        self.app.options = launch_options
        spawn, _ = self.fake_popen()
        with mock.patch.object(ui.subprocess, 'Popen', side_effect=spawn):
            self.assertEqual(self.request('/api/import', payload)[0], 202)
            self.assertEqual(self.wait_job(), 'ready')
        self.assertEqual(self.app.backend_port, payload['port'])
        # A browser refresh must reflect the successfully saved form rather
        # than the process's original context / encoder flags.
        cfg = portable.read_config(self.app.current_config)
        cfg.update(args=['--max-context','65536'], backend='hip')
        portable.save_json(self.app.current_config, cfg)
        defaults = self.app.defaults()
        self.assertEqual(defaults['context'], 65536)
        self.assertEqual(defaults['backend'], 'hip')
        self.assertEqual(defaults['vision'], 'no')
        self.assertEqual(self.app.options.vision_tokens, 513)
        self.assertEqual(launch_options.context, 2048)

    def test_failed_import_preserves_launch_overrides(self):
        self.app.options = SimpleNamespace(port=None, context=2048, vision='cpu')
        spawn, _ = self.fake_popen(fail=True)
        with mock.patch.object(ui.subprocess, 'Popen', side_effect=spawn):
            self.assertEqual(self.request('/api/import', self.payload())[0], 202)
            self.assertEqual(self.wait_job(), 'error')
        self.assertEqual(self.app.defaults()['context'], 2048)
        self.assertEqual(self.app.defaults()['vision'], 'cpu')

    def test_initial_launch_keeps_explicit_port_and_hidden_budget(self):
        payload = self.payload()
        payload['context'] = 2048
        options = SimpleNamespace(port=payload['port'], context=2048, vision_tokens=513)
        self.app.options = options
        spawn, _ = self.fake_popen()
        with mock.patch.object(ui.subprocess, 'Popen', side_effect=spawn):
            self.app.begin(payload, launch=True)
            self.assertEqual(self.wait_job(), 'ready')
        self.assertIs(self.app.options, options)
        self.assertEqual(self.app.backend_port, payload['port'])
        self.assertEqual(self.app.defaults()['context'], 2048)
        self.assertEqual(self.app.options.vision_tokens, 513)

    def test_initial_explicit_port_does_not_fall_back_if_taken_during_configuration(self):
        payload = self.payload()
        self.app.options = SimpleNamespace(port=payload['port'])
        start = self.app._start_backend
        def occupy_then_start():
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', payload['port']))
                listener.listen()
                start()
        spawn, _ = self.fake_popen()
        with mock.patch.object(ui.subprocess, 'Popen', side_effect=spawn), \
                mock.patch.object(self.app, '_start_backend', side_effect=occupy_then_start):
            self.app.begin(payload, launch=True)
            self.assertEqual(self.wait_job(), 'error')
        self.assertIsNone(self.app.process)
        self.assertIn(str(payload['port']), self.app.job['error'])


if __name__ == '__main__':
    unittest.main()

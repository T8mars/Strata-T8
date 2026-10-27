"""Second audit: failed assembly retries and effective setup/API settings."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

import portable
from serve import portable_ui as ui
from tools import test_portable_ui as setup_fixture
from tools import test_portable_release_parts as part_fixture
from tools import portable_release_parts as parts


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell helper')
class AssemblyRecovery(unittest.TestCase):
    setUp = part_fixture.Multipart.setUp

    def run_join(self):
        script = self.archive.with_name(parts.names(self.archive)[1])
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                              capture_output=True, timeout=30)

    def test_failed_whole_archive_check_cleans_partial_and_can_retry(self):
        metadata = self.archive.with_name(parts.names(self.archive)[0])
        original = metadata.read_bytes()
        self.record['archive']['sha256'] = '0'*64
        metadata.write_text(json.dumps(self.record), encoding='utf-8')
        self.archive.unlink()
        failed = self.run_join()
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.archive.parent.glob('*.assembling*')), [])
        metadata.write_bytes(original)
        result = self.run_join()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.archive.read_bytes(), self.data)

    def test_unrelated_old_partial_does_not_block_or_get_deleted(self):
        old = self.archive.with_name(self.archive.name+'.assembling')
        old.write_bytes(b'private interrupted file')
        self.archive.unlink()
        result = self.run_join()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.archive.read_bytes(), self.data)
        self.assertEqual(old.read_bytes(), b'private interrupted file')


class SetupSettings(unittest.TestCase):
    setUp = setup_fixture.SetupPage.setUp
    tearDown = setup_fixture.SetupPage.tearDown
    request = setup_fixture.SetupPage.request
    model = setup_fixture.SetupPage.model
    payload = setup_fixture.SetupPage.payload
    wait_job = setup_fixture.SetupPage.wait_job
    fake_popen = setup_fixture.SetupPage.fake_popen

    def test_context_equals_and_cli_speed_are_visible_in_settings(self):
        cfg = self.root/'strata-test.json'
        portable.save_json(cfg, {'args':['--max-context=2048', '--experts', '6']})
        self.app.current_config = cfg
        self.app.options = SimpleNamespace(speed='faster')
        result = self.app.defaults()
        self.assertEqual(result['context'], 2048)
        self.assertEqual(result['speed'], 'faster')

    def test_api_hint_uses_effective_server_auth_instead_of_saved_config(self):
        spawn, _ = self.fake_popen()
        read = ui.json_reply
        def status(*args, **kwargs):
            return {**read(*args, **kwargs), 'api_key':True}
        with mock.patch.object(ui.subprocess, 'Popen', side_effect=spawn), \
                mock.patch.object(ui, 'json_reply', side_effect=status):
            self.assertEqual(self.request('/api/import', self.payload())[0], 202)
            self.assertEqual(self.wait_job(), 'ready')
        self.assertFalse(self.app.backend_config.get('api_key'))
        self.assertTrue(self.app.snapshot()['api_connection']['api_key_required'])

    def test_explicit_browser_start_applies_family_specific_settings(self):
        model = self.model()
        cfg = self.root/'strata-test.json'
        portable.save_json(cfg, {'args':['--max-context', '2048'], 'backend':'cuda'})
        self.app.current_config = cfg
        for family, overrides in (('qwen', {'context':65536}), ('deepseek41', {'speed':'faster'})):
            with self.subTest(family=family):
                self.app.closed.clear()
                self.app.set_job('idle', 'fixture')
                summary = {'family':family, 'path':str(model), 'label':family}
                self.app.model = summary
                options = SimpleNamespace(action='start', no_browser=True, **overrides)
                self.app.options = options
                accepted = []
                def begin(data=None, *, existing=False):
                    if existing:
                        accepted.append({'existing':True})
                    else:
                        accepted.append(self.app._options(data)[0])
                with mock.patch.object(ui, 'ModelSetup', return_value=self.app), \
                        mock.patch.object(ui, 'model_summary', return_value=summary), \
                        mock.patch.object(ui, 'json_reply', side_effect=OSError('unused port')), \
                        mock.patch.object(ui, 'create_server', return_value=mock.Mock()), \
                        mock.patch.object(self.app, 'check_updates'), \
                        mock.patch.object(self.app, 'begin', side_effect=begin), \
                        mock.patch.object(self.app.closed, 'wait', return_value=True), \
                        mock.patch.object(ui.signal, 'signal'):
                    self.assertEqual(ui.run(options), 0)
                self.assertEqual(self.app.job['state'], 'idle', self.app.job)
                self.assertEqual(len(accepted), 1)
                self.assertNotIn('existing', accepted[0])
                for key, value in overrides.items():
                    self.assertEqual(accepted[0][key], value)
                if family == 'qwen':
                    self.assertNotIn('speed', accepted[0])


if __name__ == '__main__':
    unittest.main()

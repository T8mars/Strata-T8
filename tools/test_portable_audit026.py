"""Regression cases reproduced in the 0.2.6 twenty-round local audit."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import portable
from serve import portable_ui as ui
from tools import portable_deepseek as ds, portable_restore as restore
from tools import test_portable_deepseek as ds_fixtures


class DeepSeekSettings(unittest.TestCase):
    setUp = ds_fixtures.PortablePack.setUp

    def configuration(self):
        (self.root/'engine-dsv41').mkdir(exist_ok=True)
        (self.root/'engine-dsv41/strata-dsv41.exe').write_bytes(b'fixture')
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(portable, 'ROOT', self.root))
        stack.enter_context(mock.patch.object(portable, 'STATE', self.root/'portable-settings.json'))
        stack.enter_context(mock.patch.object(portable, 'fingerprint', return_value={'version': 'fixture'}))
        return ds.configure(portable, self.pack, context=2048)[1]

    def test_speed_change_preserves_context_when_not_requested(self):
        cfg = self.configuration()
        for speed in ('faster', 'full', None):
            with self.subTest(speed=speed):
                ds.configure(portable, self.pack, speed=speed)
                actual = portable.read_config(cfg)
                self.assertTrue(portable.context_matches(actual, 2048), actual['args'])

    def test_reconfigure_keeps_text_only_arguments_and_custom_flags(self):
        cfg = self.configuration()
        current = portable.read_config(cfg)
        current['args'] += ['--vision=on', '--cpu-threads', '7']
        portable.save_json(cfg, current)
        ds.configure(portable, self.pack, vision='no')
        args = portable.read_config(cfg)['args']
        self.assertNotIn('--vision=on', args)
        self.assertEqual(args[args.index('--vision')+1], 'off')
        self.assertIn('--cpu-threads', args)

    def test_headless_disable_vision_keeps_valued_deepseek_flag(self):
        cfg = self.configuration()
        original_cwd = Path.cwd()
        self.addCleanup(os.chdir, original_cwd)
        with mock.patch.object(portable.sys, 'argv', ['portable.py', 'start', '--no-browser', '--vision', 'no']), \
                mock.patch.object(portable, 'environment_check'), mock.patch.object(portable, 'isolate_setup'), \
                mock.patch.object(portable.threading, 'Thread'), \
                mock.patch.object(portable.subprocess, 'call', return_value=0) as launch:
            self.assertEqual(portable.main(), 0)
        launch.assert_called_once()
        args = portable.read_config(cfg)['args']
        self.assertIn('--vision', args)
        self.assertEqual(args[args.index('--vision')+1], 'off')
        self.assertNotIn('--native-video', args)

    def test_invalid_native_video_is_rejected_before_stopping_current_service(self):
        self.configuration()
        (self.root/'meta.json').write_text('{"version":"0.2.6-t8.1"}', encoding='utf-8')
        app = ui.ModelSetup(self.root)
        self.addCleanup(app.close)
        with mock.patch.object(app, 'stop_backend') as stop:
            with self.assertRaisesRegex(ui.SetupError, 'DeepSeek|文本'):
                app._options({'path': str(self.pack), 'vision': 'auto', 'native_video': True})
            stop.assert_not_called()


class RestoreHistory(unittest.TestCase):
    def test_bad_receipts_do_not_hide_valid_backup_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root/'PACKAGE-MANIFEST.json').write_bytes(b'fixture')
            history = root/'.portable-update/history'
            history.mkdir(parents=True)
            for i, value in enumerate(([], None, 42, 'bad', {}, {'id': 'wrong'})):
                (history/(f'{i:032x}.json')).write_text(json.dumps(value), encoding='utf-8')
            identity = 'f'*32
            good = {'id': identity, 'root': str(root), 'from_version': '0.1.45-t8.1',
                    'to_version': '0.2.6-t8.1', 'installed_manifest_sha256': 'other'}
            (history/(identity+'.json')).write_text(json.dumps(good), encoding='utf-8')
            self.assertEqual(restore.list_backups(root)['backups'], [{
                'id': identity, 'from_version': good['from_version'],
                'to_version': good['to_version'], 'available': False}])


if __name__ == '__main__':
    unittest.main()

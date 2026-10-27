"""Third audit: cross-family defaults and explicit launcher intent."""
from types import SimpleNamespace
import unittest
from unittest import mock

import portable
from serve import portable_ui as ui
from tools import test_portable_ui as fixture


class LauncherSettings(unittest.TestCase):
    setUp = fixture.SetupPage.setUp
    tearDown = fixture.SetupPage.tearDown

    def saved_qwen(self):
        cfg = self.root/'strata-qwen.json'
        portable.save_json(cfg, {'args':['--max-context', '32768'], 'backend':'cuda',
                                 'vision':{'gpu':True, 'native_video':True}})
        self.app.current_config = cfg
        self.app.model = {'family':'deepseek41', 'path':str(self.root.parent/'ds-pack')}
        self.app.options = SimpleNamespace(data_dir=self.app.model['path'])

    def test_new_deepseek_path_does_not_inherit_incompatible_qwen_settings(self):
        self.saved_qwen()
        with mock.patch.object(ui, 'model_summary', return_value=self.app.model):
            accepted, _ = self.app._options(self.app.defaults())
        self.assertEqual(accepted['vision'], 'no')
        self.assertFalse(accepted['native_video'])
        self.assertEqual(accepted['backend'], 'cuda')
        self.assertLessEqual(accepted['context'], 8192)

    def test_explicit_unsupported_deepseek_options_fail_before_stopping_service(self):
        self.saved_qwen()
        for options in ({'vision':'gpu'}, {'native_video':True}, {'backend':'hip'}):
            with self.subTest(options=options):
                self.app.options = SimpleNamespace(vision='no', native_video=False, backend='cuda')
                for name, value in options.items():
                    setattr(self.app.options, name, value)
                self.app.set_job('idle', 'fixture')
                with mock.patch.object(ui, 'model_summary', return_value=self.app.model), \
                        mock.patch.object(self.app, 'stop_backend') as stop, \
                        mock.patch.object(ui.threading, 'Thread') as worker:
                    with self.assertRaises(ui.SetupError):
                        self.app.begin(self.app.defaults())
                    stop.assert_not_called()
                    worker.assert_not_called()
                    self.assertEqual(self.app.job['state'], 'idle')

    def test_qwen_saved_options_are_preserved(self):
        self.saved_qwen()
        self.app.model['family'] = 'qwen'
        self.app.options = SimpleNamespace()
        result = self.app.defaults()
        self.assertEqual(result['context'], 32768)
        self.assertEqual(result['backend'], 'cuda')
        self.assertEqual(result['vision'], 'gpu')
        self.assertTrue(result['native_video'])

    def existing_identity(self):
        return {'service':'strata-portable-ui', 'app_root':str(self.app.root), 'version':self.app.version}

    def test_existing_launcher_cannot_silently_ignore_new_options(self):
        for override in ({'data_dir':'different-model'}, {'context':2048}, {'speed':'faster'},
                         {'port':8099}, {'native_video':False}, {'vision_tokens':1024}):
            with self.subTest(override=override), \
                    mock.patch.object(ui, 'ModelSetup', return_value=self.app), \
                    mock.patch.object(ui, 'json_reply', return_value=self.existing_identity()), \
                    mock.patch.object(ui, 'create_server') as create, \
                    mock.patch.object(ui.webbrowser, 'open') as browser:
                with self.assertRaisesRegex(ui.SetupError, '已运行'):
                    ui.run(SimpleNamespace(action='start', no_browser=False, **override))
                create.assert_not_called()
                browser.assert_not_called()

    def test_plain_second_launch_reuses_current_settings_page(self):
        with mock.patch.object(ui, 'ModelSetup', return_value=self.app), \
                mock.patch.object(ui, 'json_reply', return_value=self.existing_identity()), \
                mock.patch.object(ui, 'create_server') as create, \
                mock.patch.object(ui.webbrowser, 'open') as browser:
            self.assertEqual(ui.run(SimpleNamespace(action='start', no_browser=False)), 0)
            create.assert_not_called()
            browser.assert_called_once()
            self.assertTrue(browser.call_args.args[0].endswith('?enter=1'))


if __name__ == '__main__':
    unittest.main()

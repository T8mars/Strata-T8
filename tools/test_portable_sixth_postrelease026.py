"""Optional console update checks must not prevent first-run model settings."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import portable


class OptionalConsoleUpdate(unittest.TestCase):
    def test_resource_failure_in_optional_console_check_keeps_first_run_usable(self):
        for creation in (True, False):
            for kind in (OSError, RuntimeError, MemoryError):
                with self.subTest(construction=creation, error=kind.__name__), \
                        mock.patch('sys.argv',['portable.py','start']), \
                        mock.patch.object(portable, 'environment_check'), mock.patch('os.chdir'), \
                        mock.patch('portable_update.check_update') as update, \
                        mock.patch('serve.portable_ui.run',return_value=0) as run, \
                        mock.patch.object(portable, 'model_delivery') as delivery, \
                        mock.patch.object(portable.threading, 'Thread') as worker, redirect_stderr(io.StringIO()):
                    error = kind('fixture optional console check cannot start')
                    if creation:worker.side_effect = error
                    else:worker.return_value.start.side_effect = error
                    self.assertEqual(portable.main(), 0)
                    run.assert_called_once()
                    delivery.assert_not_called()
                    update.assert_not_called()

    def test_optional_check_failure_also_preserves_headless_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fingerprint = {'data':'fixture','gpu':[0],'version':'fixture'}
            portable.save_json(root/'strata-test.json', {'port':8087,'host':'127.0.0.1','args':[]})
            state = root/'portable-settings.json'
            portable.save_json(state, {'portable_config':'strata-test.json','portable_fingerprint':fingerprint})
            with mock.patch.object(portable, 'ROOT', root), mock.patch.object(portable, 'STATE', state), \
                    mock.patch('sys.argv',['portable.py','start','--no-browser']), mock.patch('os.chdir'), \
                    mock.patch.object(portable, 'environment_check'), mock.patch.object(portable, 'isolate_setup'), \
                    mock.patch.object(portable, 'data_path', return_value=root/'model'), \
                    mock.patch.object(portable, 'model_delivery'), \
                    mock.patch.object(portable, 'fingerprint', return_value=fingerprint), \
                    mock.patch('portable_update.check_update') as update, \
                    mock.patch.object(portable.threading, 'Thread', side_effect=RuntimeError('fixture check allocation failed')), \
                    mock.patch.object(portable.subprocess, 'call', return_value=0) as start, redirect_stderr(io.StringIO()):
                self.assertEqual(portable.main(), 0)
                update.assert_not_called()
                self.assertIn('8087', start.call_args.args[0])


if __name__ == '__main__':
    unittest.main()

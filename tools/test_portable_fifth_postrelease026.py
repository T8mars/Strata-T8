"""Background worker start failures must not leave settings permanently busy."""
import unittest
import threading
from unittest import mock

from serve import portable_ui as ui
from tools import test_portable_ui as fixture


class WorkerStartRecovery(unittest.TestCase):
    setUp = fixture.SetupPage.setUp
    tearDown = fixture.SetupPage.tearDown
    model = fixture.SetupPage.model
    payload = fixture.SetupPage.payload
    request = fixture.SetupPage.request
    wait_job = fixture.SetupPage.wait_job

    def test_failed_job_thread_start_restores_actionable_state(self):
        operations = {
            'import': lambda: self.app.begin(self.payload()),
            'stop': self.app.stop,
            'network': lambda: self.app.save_network({'scope':'local'}),
            'prepare': lambda: self.app.prepare({'path':str(self.root/'my-models'),'accept_download':True}),
            'update': self.app.begin_update,
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                self.app.set_job('idle', 'fixture')
                self.app.update.update(supported=True,state='ready',available=True)
                with mock.patch.object(ui.threading, 'Thread') as worker:
                    worker.return_value.start.side_effect = RuntimeError('fixture cannot start new thread')
                    with self.assertRaises((RuntimeError,ui.SetupError)):
                        operation()
                self.assertEqual(self.app.job['state'], 'error', f'{name} left busy after no worker was started')
                self.assertIn('cannot start new thread', self.app.job['error'])
                self.assertIsNone(self.app.process)
                self.assertIsNone(self.app.configure_process)
                self.assertFalse(self.app.closed.is_set())
                self.assertEqual(self.request('/api/status')[0], 200)

    def test_optional_update_checker_start_failure_keeps_settings_usable(self):
        failures = []
        with mock.patch.object(ui.threading, 'Thread') as worker:
            worker.return_value.start.side_effect = RuntimeError('fixture cannot start checker')
            try:self.app.check_updates()
            except RuntimeError as error:failures.append(str(error))
        self.assertEqual(failures, [], 'optional update checker prevented the launcher from continuing')
        self.assertEqual(self.app.update['state'], 'error')
        self.assertEqual(self.app.job['state'], 'idle')
        self.assertEqual(self.request('/api/status')[0], 200)
        with mock.patch.object(ui.threading, 'Thread') as worker:
            self.app.check_updates()
        worker.return_value.start.assert_called_once()
        self.assertEqual(self.app.update['state'], 'checking')

    def test_constructor_failures_leave_a_retryable_job(self):
        for error in (OSError('fixture thread allocation failed'), MemoryError('fixture thread allocation failed')):
            with self.subTest(error=type(error).__name__):
                self.app.set_job('idle', 'fixture')
                with mock.patch.object(ui.threading, 'Thread', side_effect=error):
                    with self.assertRaises(ui.SetupError):
                        self.app.stop()
                self.assertEqual(self.app.job['state'], 'error')
                self.assertIn('thread allocation failed', self.app.job['error'])
        self.assertEqual(self.request('/api/stop', {})[0], 202)
        self.assertEqual(self.wait_job(states=('idle',)), 'idle')

    def test_http_import_reports_worker_failure_without_disabling_the_page(self):
        payload = self.payload()
        start = threading.Thread.start
        def fail_configuration_worker(thread):
            if getattr(thread, '_target', None) == self.app._configure_and_start:
                raise RuntimeError('fixture HTTP worker cannot start')
            return start(thread)
        with mock.patch.object(threading.Thread, 'start', new=fail_configuration_worker):
            status, _, value = self.request('/api/import', payload)
        self.assertEqual(status, 400)
        self.assertIn('后台任务', value['error'])
        self.assertEqual(self.request('/api/status')[2]['job']['state'], 'error')
        self.assertIsNone(self.app.configure_process)
        self.assertEqual(self.request('/api/stop', {})[0], 202)
        self.assertEqual(self.wait_job(states=('idle',)), 'idle')


if __name__ == '__main__':
    unittest.main()

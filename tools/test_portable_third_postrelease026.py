"""Owned process stop failures must leave settings responsive and retryable."""
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from serve import portable_ui as ui
from tools import test_portable_ui as fixture


class StopFailureRecovery(unittest.TestCase):
    setUp = fixture.SetupPage.setUp
    tearDown = fixture.SetupPage.tearDown
    request = fixture.SetupPage.request
    model = fixture.SetupPage.model
    payload = fixture.SetupPage.payload
    wait_job = fixture.SetupPage.wait_job

    def action(self, operation, exception):
        failures = []
        self.app.set_job('idle', 'fixture')
        self.app.process = SimpleNamespace(poll=lambda: None)
        original = self.app.process
        payload = self.payload()
        try:
            with mock.patch.object(self.app, 'stop_backend', side_effect=exception), \
                    mock.patch.object(threading, 'excepthook', side_effect=lambda args: failures.append(args.exc_value)), \
                    mock.patch.object(self.app, '_spawn') as spawn, \
                    mock.patch.object(self.app, '_start_backend') as start:
                if operation == 'stop':
                    response = self.request('/api/stop', {})
                elif operation == 'import':
                    response = self.request('/api/import', payload)
                else:
                    response = self.request('/api/network', {'scope':'lan','api_key':'fixture-private-key-1234'})
                self.assertEqual(response[0], 202)
                deadline = time.monotonic()+2
                while self.app.job['state'] not in ('error', 'idle') and not failures and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertEqual(self.app.job['state'], 'error', f'{operation}: {failures}')
                self.assertEqual(failures, [])
                self.assertIn('owned backend', self.app.job['error'])
                self.assertIs(self.app.process, original, 'uncertain process ownership must not be discarded')
                spawn.assert_not_called()
                start.assert_not_called()
                self.assertFalse(self.app.closed.is_set())
                self.assertEqual(self.request('/api/status')[0], 200)
                self.assertFalse(self.app.portable.STATE.exists())
        finally:
            self.app.process = None

    def test_stop_failure_is_visible_and_retains_process_for_retry(self):
        for error in (OSError('owned backend access denied'), subprocess.TimeoutExpired('owned backend', 10)):
            with self.subTest(error=type(error).__name__):
                self.action('stop', error)

    def test_model_switch_stop_timeout_does_not_leave_configuring_forever(self):
        self.action('import', subprocess.TimeoutExpired('owned backend', 10))

    def test_network_change_stop_timeout_survives_cleanup_failure(self):
        self.action('network', subprocess.TimeoutExpired('owned backend', 10))

    def test_failed_stop_can_be_retried_from_the_same_page(self):
        self.app.process = SimpleNamespace(poll=lambda: None)
        attempts = []
        def stop():
            attempts.append(1)
            if len(attempts) == 1:
                raise subprocess.TimeoutExpired('owned backend', 10)
            self.app.process = None
        try:
            with mock.patch.object(self.app, 'stop_backend', side_effect=stop):
                self.assertEqual(self.request('/api/stop', {})[0], 202)
                self.assertEqual(self.wait_job(states=('error',)), 'error')
                self.assertEqual(self.request('/api/stop', {})[0], 202)
                self.assertEqual(self.wait_job(states=('idle',)), 'idle')
                self.assertIsNone(self.app.process)
                self.assertEqual(len(attempts), 2)
        finally:
            self.app.process = None


if __name__ == '__main__':
    unittest.main()

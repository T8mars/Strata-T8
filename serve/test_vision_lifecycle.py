"""Actual child-process cancellation and companion-resource regression tests, without a GPU."""
import base64
import io
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from serve.server import Vision, RequestCancelled, EngineStarting, EngineStuck, vision_temp_dir
from serve.test_lifecycle import Lifecycle


class VisionProcesses(unittest.TestCase):
    def test_temp_with_spaces_and_unicode_has_safe_native_path(self):
        with tempfile.TemporaryDirectory(prefix='Vision 中文 space ') as root:
            old_mkdtemp = tempfile.mkdtemp
            def make(*args, **kwargs):
                if kwargs.get('dir') is None:
                    kwargs['dir'] = root
                return old_mkdtemp(*args, **kwargs)
            with mock.patch('serve.server.tempfile.mkdtemp', side_effect=make):
                path = vision_temp_dir()
            self.addCleanup(shutil.rmtree, path, True)
            self.assertTrue(str(path).isascii())
            self.assertFalse(any(c.isspace() for c in str(path)))
            (path/'test.sve').write_bytes(b'rows')

    def make(self, code):
        obj = Vision({'exe': 'unused', 'mmproj': 'unused', 'model': 'unused'}, lazy=True)
        self.addCleanup(shutil.rmtree, obj.dir, True)
        self.addCleanup(obj.close, True)
        obj.spawn = ([sys.executable, '-u', '-c', code], None, None)
        return obj

    def test_lazy_vision_starts_nothing(self):
        obj = self.make('raise RuntimeError("must not run")')
        self.assertFalse(obj.alive())
        self.assertIsNone(obj.proc)

    def test_start_can_cancel_and_does_not_leave_a_child(self):
        obj = self.make('import time; time.sleep(60)')
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set)
        timer.start()
        self.addCleanup(timer.cancel)
        before = time.monotonic()
        with self.assertRaises(RequestCancelled):
            obj.restart(cancel)
        self.assertLess(time.monotonic()-before, 5)
        self.assertIsNone(obj.proc)

    def test_missing_ready_has_bounded_timeout(self):
        obj = self.make('import time; time.sleep(60)')
        obj.start_timeout_s = .2
        with self.assertRaises(EngineStarting):
            obj.restart()
        self.assertIsNone(obj.proc)

    def test_encoding_cancellation_cleans_files_and_can_reload(self):
        obj = self.make('import sys,time; print("READY",flush=True); sys.stdin.readline(); time.sleep(60)')
        obj.restart()
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set)
        timer.start()
        self.addCleanup(timer.cancel)
        image = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()
        with self.assertRaises(RequestCancelled):
            obj.encode(image, cancel)
        self.assertIsNone(obj.proc)
        self.assertEqual(list(obj.dir.iterdir()), [])
        obj.spawn = ([sys.executable, '-u', '-c', 'import sys; print("READY",flush=True); sys.stdin.readline()'], None, None)
        obj.restart()
        self.assertTrue(obj.alive())
        obj.unload()
        self.assertIsNone(obj.proc)

    def test_stuck_close_keeps_process_for_retry(self):
        obj = self.make('')
        proc = mock.Mock()
        proc.stdin, proc.stdout = io.StringIO(), io.StringIO()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired('vision', 20)
        obj.proc = proc
        with self.assertRaises(EngineStuck):
            obj.close()
        self.assertIs(obj.proc, proc)
        self.assertEqual(proc.wait.call_count, 2)
        proc.poll.return_value = 0


class CompanionCleanup(unittest.TestCase):
    setUp = Lifecycle.setUp
    tearDown = Lifecycle.tearDown
    def vision(self):
        obj = mock.Mock(spec=['alive', 'unload'])
        obj.alive.return_value = True
        self.svc.vision = obj
        return obj

    def test_dead_language_engine_still_unloads_vision(self):
        obj = self.vision()
        self.assertFalse(self.engine.alive())
        self.assertEqual(self.svc.unload(), 'unloaded')
        obj.unload.assert_called_once()
        self.assertEqual(self.engine.closes, 1)

    def test_language_shutdown_failure_still_closes_vision(self):
        obj = self.vision()
        self.engine.unload = mock.Mock(side_effect=EngineStuck('engine still exiting'))
        with self.assertRaises(EngineStuck):
            self.svc.unload()
        obj.unload.assert_called_once()

    def test_cancelled_waiter_does_not_start_engine(self):
        cancel = threading.Event()
        cancel.set()
        with self.svc.fifo, self.assertRaises(RequestCancelled):
            self.svc.load(cancel)
        self.assertEqual(self.engine.starts, 0)

    def test_cancelled_generation_waiter_compensates_queue(self):
        cancel = threading.Event()
        self.svc.fifo.acquire()
        self.addCleanup(self.svc.fifo.release)
        timer = threading.Timer(.2, cancel.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(RequestCancelled):
            list(self.svc.run([1], False, [], 32, {}, cancel))
        self.assertEqual(self.svc.status['queued'], 0)
        self.assertFalse(self.svc.status['busy'])
        self.assertEqual(self.engine.starts, 0)

    def test_failed_load_still_closes_vision_when_engine_close_fails(self):
        obj = self.vision()
        self.engine.restart = mock.Mock(side_effect=EngineStarting('loading failed'))
        self.engine.close = mock.Mock(side_effect=EngineStuck('engine exit failed'))
        with self.assertRaises(EngineStuck):
            self.svc.ensure_loaded()
        obj.unload.assert_called_once()

    def test_http_disconnect_cancels_loading_for_all_generation_protocols(self):
        bodies = {'/v1/load': {}, '/v1/chat/completions': {'messages':[{'role':'user','content':'hi'}]},
                  '/v1/messages': {'messages':[{'role':'user','content':'hi'}], 'max_tokens':64},
                  '/v1/responses': {'input':'hi'}}
        for path, body in bodies.items():
            entered, stopped = threading.Event(), threading.Event()
            def restart():
                entered.set()
                try:
                    if not self.engine.load_cancel.wait(5):
                        raise RuntimeError('client disconnect was not propagated')
                    raise RequestCancelled('cancelled')
                finally:
                    stopped.set()
            self.engine.restart = restart
            connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=8)
            connection.request('POST', path, json.dumps(body), {'Content-Type':'application/json'})
            self.assertTrue(entered.wait(3), path)
            connection.sock.shutdown(2)
            connection.close()
            self.assertTrue(stopped.wait(3), path)
            # Wait for the handler's final resource settlement before the next protocol.
            end = time.monotonic()+3
            while self.engine.load_cancel is not None and time.monotonic()<end:
                time.sleep(.01)
            self.assertIsNone(self.engine.load_cancel, path)
            self.assertFalse(self.engine.alive(), path)


if __name__ == '__main__':
    unittest.main()

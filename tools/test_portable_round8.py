"""Eighth audit: real stream deadlines, startup cleanup and concurrent vision requests."""
import base64
from email.message import Message
import io
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from serve import server
from serve.frontend import ChatTemplate

ROOT = Path(__file__).resolve().parents[1]


class ChunkDeadlines(unittest.TestCase):
    def expired_stream(self, first, rest, content_length=False):
        received, sent = socket.socketpair()
        self.addCleanup(received.close)
        self.addCleanup(sent.close)
        reader = received.makefile('rb')
        self.addCleanup(reader.close)
        handler = server.make_handler(None).__new__(server.make_handler(None))
        handler.connection, handler.rfile = received, reader
        handler.BODY_SECONDS = .18
        handler.headers = Message()
        handler.headers['Content-Length'] = '8'
        response = []
        handler._json = lambda status, body: response.append(status)
        stop = threading.Event()

        def send():
            try:
                sent.sendall(first)
                for part in rest:
                    if stop.wait(.06):
                        return
                    sent.sendall(part)
            except OSError:
                pass

        writer = threading.Thread(target=send, daemon=True)
        writer.start()
        started = time.monotonic()
        try:
            if content_length:
                self.assertIsNone(handler._read_body(1024))
                self.assertEqual(response, [400])
            else:
                with self.assertRaises(server.BadBody) as caught:
                    handler._read_chunked(1024, deadline=started + .18)
                self.assertEqual(caught.exception.status, 400)
            self.assertLess(time.monotonic() - started, .4)
        finally:
            stop.set()
            writer.join(1)

    def test_r12_chunk_payload_cannot_extend_the_absolute_deadline(self):
        self.expired_stream(b'8\r\n', [b'a'] * 8 + [b'\r\n0\r\n\r\n'])

    def test_r12_chunk_size_line_cannot_extend_the_absolute_deadline(self):
        self.expired_stream(b'', [b'0'] * 8 + [b'1\r\na\r\n0\r\n\r\n'])

    def test_r13_trailer_line_cannot_extend_the_absolute_deadline(self):
        self.expired_stream(b'0\r\n', [b'X', b':', b' '] + [b'a'] * 8 + [b'\r\n\r\n'])

    def test_r12_content_length_payload_cannot_extend_the_absolute_deadline(self):
        self.expired_stream(b'', [b'a'] * 8, content_length=True)


class StartupOwnership(unittest.TestCase):
    def check_ready(self, ready, error, args=()):
        children = []

        def spawn(what, argv, **options):
            options.pop('cwd', None)
            child = subprocess.Popen([sys.executable, '-u', '-c',
                                      'import sys;print(' + repr(ready) +
                                      ',flush=True);sys.stdin.readline()'], **options)
            children.append(child)
            return child

        try:
            with mock.patch.object(server, 'popen', side_effect=spawn):
                with self.assertRaises(error):
                    server.StrataEngine('test-engine', list(args), start_timeout_s=1)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll(), 'failed startup left its owned process alive')
            self.assertTrue(children[0].stdin.closed)
            self.assertTrue(children[0].stdout.closed)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdin.close()
                child.stdout.close()

    def test_r14_malformed_ready_does_not_leave_an_owned_child(self):
        self.check_ready('READY invalid', ValueError)

    def test_r14_missing_ready_context_does_not_leave_an_owned_child(self):
        self.check_ready('READY', (ValueError, IndexError))

    def test_r14_zero_context_does_not_leave_an_owned_child(self):
        self.check_ready('READY 0', RuntimeError)

    def test_r14_invalid_info_after_ready_still_closes_the_owned_child(self):
        self.check_ready('INFO batch_slots=invalid\nREADY 4096', ValueError)

    def test_r14_invalid_batch_arguments_after_ready_still_close_the_owned_child(self):
        for args in (['--batch', 'invalid'], ['--batch-groups', 'invalid']):
            with self.subTest(args=args):
                self.check_ready('READY 4096', ValueError, args)

    def test_r14_first_lazy_start_failure_is_not_masked_and_can_recover(self):
        children = []

        def spawn(what, argv, **options):
            options.pop('cwd', None)
            line = 'READY invalid' if not children else 'READY 4096 stop'
            child = subprocess.Popen([sys.executable, '-u', '-c',
                'import sys;print(' + repr(line) + ',flush=True);sys.stdin.readline()'], **options)
            children.append(child)
            return child

        engine = server.StrataEngine('test-engine', [], lazy=True, start_timeout_s=1)
        try:
            with mock.patch.object(server, 'popen', side_effect=spawn):
                with self.assertRaises(ValueError):
                    engine.restart(tries=1)
                self.assertFalse(engine.starting)
                self.assertIsNone(engine.proc)
                self.assertIsNotNone(children[0].poll())
                engine.restart(tries=1)
            self.assertTrue(engine.alive())
            self.assertEqual(engine.max_context, 4096)
            self.assertEqual(len(children), 2)
        finally:
            engine.close()
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdin.close()
                child.stdout.close()

    def test_r14_nonfinite_environment_timeout_uses_the_default(self):
        for value in ('nan', 'inf', '-inf', 'not-a-number'):
            with self.subTest(value=value), mock.patch.dict(os.environ, {'AUDIT_TIMEOUT': value}):
                self.assertEqual(server._timeout_env('AUDIT_TIMEOUT', 300), 300)

    def test_r14_explicit_zero_environment_timeout_still_disables_it(self):
        with mock.patch.dict(os.environ, {'AUDIT_TIMEOUT': '0'}):
            self.assertIsNone(server._timeout_env('AUDIT_TIMEOUT', 300))


class ConcurrentEmbeddings(unittest.TestCase):
    def test_r15_second_request_cannot_evict_images_before_the_first_copies_them(self):
        reached, release = threading.Event(), threading.Event()
        enabled = threading.Event()

        class PausingEngine(server.MockEngine):
            def __getattribute__(self, key):
                if key == 'max_context' and enabled.is_set() and threading.current_thread().name == 'request-a':
                    reached.set()
                    if not release.wait(5):
                        raise TimeoutError('request-a was not released')
                return super().__getattribute__(key)

        with tempfile.TemporaryDirectory() as td:
            vision = server.Vision.__new__(server.Vision)
            vision.dir = Path(td)
            vision.lock, vision.lines = threading.RLock(), queue.Queue()
            vision.cache, vision.encode_timeout_s = {}, 1
            vision.stopped, vision.proc = False, mock.Mock()
            vision.proc.poll.return_value = None

            class EncoderInput:
                def write(self, line):
                    _, image, output = line.split()
                    (vision.dir / output).write_bytes((vision.dir / image).read_bytes())
                    vision.lines.put('OK 1\n')

                def flush(self):
                    pass

            vision.proc.stdin = EncoderInput()
            tok = server.ByteTokenizer()
            svc = server.Service(PausingEngine(tok, 'ok', max_context=100000), tok,
                                 ChatTemplate(ROOT / 'serve/chat_template.jinja'), vision=vision)

            def messages(values):
                return [{'role': 'user', 'content': [
                    {'type': 'image', 'source': 'data:image/png;base64,' + base64.b64encode(value).decode()}
                    for value in values]}]

            results, errors = [], []

            def first():
                try:
                    svc.prepare(messages([b'first-image']), None, {}, 16)
                    results.append(svc.embeddings.path.read_bytes())
                except Exception as error:
                    errors.append(error)
                finally:
                    svc.discard_embeddings()

            enabled.set()
            thread = threading.Thread(target=first, name='request-a')
            with mock.patch.object(server.Vision, 'normalize', side_effect=lambda data: data):
                thread.start()
                try:
                    self.assertTrue(reached.wait(3), 'first request did not finish encoding')
                    svc.prepare(messages([str(i).encode() for i in range(65)]), None, {}, 16)
                    svc.discard_embeddings()
                finally:
                    release.set()
                    thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results, [b'first-image'])
            self.assertEqual(list(vision.dir.glob('req-*.sve')), [])


if __name__ == '__main__':
    unittest.main()

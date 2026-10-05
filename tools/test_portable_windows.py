"""HTTP rejection stays prompt even when the client declares a body but stalls."""
from pathlib import Path
import socket
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, MockEngine, Service, serve


class EarlyRejection(unittest.TestCase):
    def setUp(self):
        tok = ByteTokenizer()
        svc = Service(MockEngine(tok, '</think>\n\nok', max_context=4096), tok,
                      ChatTemplate(ROOT/'serve/chat_template.jinja'))
        svc.api_key = 'test-secret'
        self.http = serve(svc, port=0)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()

    def rejection(self, length):
        port = self.http.server_address[1]
        with socket.create_connection(('127.0.0.1', port), timeout=2) as sock:
            request = f'POST /v1/chat/completions HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {length}\r\n\r\n'
            start = time.monotonic()
            sock.sendall(request.encode('ascii'))
            received = b''
            while b'\r\n\r\n' not in received:
                received += sock.recv(4096)
            self.assertIn(b'401', received.split(b'\r\n')[0])
            self.assertLess(time.monotonic()-start, 1.5)

    def test_incomplete_small_body_cannot_delay_refusal(self):
        self.rejection(100)

    def test_oversized_body_is_not_waited_for(self):
        self.rejection(10**9)


if __name__ == '__main__':
    unittest.main()

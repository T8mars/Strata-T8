"""Lazy portable startup must discover new native capabilities before using them."""
import http.client
import json
from pathlib import Path
import unittest

from serve import server
from serve.frontend import ChatTemplate


class LazyBiasCapability(unittest.TestCase):
    def setUp(self):
        tok = server.ByteTokenizer()

        class LazyEngine(server.MockEngine):
            ready = False
            unloaded = True
            restarts = 0
            info = {}

            def alive(self):
                return self.ready

            def restart(self):
                self.restarts += 1
                self.ready = True
                self.info = {'logit_bias': 1}

            def close(self):
                self.ready = False

        self.engine = LazyEngine(tok, 'ok', max_context=4096)
        service = server.Service(self.engine, tok, ChatTemplate(Path(__file__).parent/'chat_template.jinja'))
        self.httpd = server.serve(service, port=0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, bias):
        c = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=10)
        try:
            c.request('POST', '/v1/chat/completions', json.dumps({'messages':[{'role':'user','content':'hi'}],
                'max_tokens':4,'chat_template_kwargs':{'enable_thinking':False},'logit_bias':bias}),
                {'Content-Type':'application/json'})
            response = c.getresponse()
            return response.status, json.loads(response.read())
        finally:
            c.close()

    def test_first_valid_bias_request_loads_and_uses_the_new_capability(self):
        status, body = self.request({'111': -2})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['choices'][0]['message']['content'], 'ok')
        self.assertEqual(self.engine.restarts, 1)

    def test_malformed_bias_still_fails_before_any_load(self):
        status, body = self.request(True)
        self.assertEqual(status, 400, body)
        self.assertEqual(self.engine.restarts, 0)


if __name__ == '__main__':
    unittest.main()

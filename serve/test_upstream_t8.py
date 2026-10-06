"""Merged upstream behavior at T8's strict, managed-service API boundaries."""
import copy
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

from serve.frontend import (ChatTemplate, OutputParser, anthropic_to_messages,
                            openai_to_messages, tool_arguments)
from serve.responses import request_tools
from serve.server import (ByteTokenizer, MockEngine, RequestCancelled, Service,
                          EngineStuck, run_with_mcp, serve, shutdown_service)
from serve.structured import StructuredOutputError, _ObjectOnly, prepare_format, validated_json

ROOT = Path(__file__).resolve().parents[1]
CALL = '<tool_call>\n<function=ns__read>\n<parameter=value>\n123\n</parameter>\n</function>\n</tool_call>'
TOOLS = [{'type': 'namespace', 'name': 'ns', 'tools': [
    {'type': 'function', 'name': 'read', 'parameters': {
        'type': 'object', 'properties': {'value': {'type': 'string'}}}}]}]


class StrictCompatibility(unittest.TestCase):
    def history(self, dialect, arguments, compatibility):
        if dialect == 'openai':
            message = {'role': 'assistant', 'content': '', 'tool_calls': [
                {'function': {'name': 'f', 'arguments': arguments}}]}
            convert = openai_to_messages
        else:
            message = {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'name': 'f', 'input': arguments}]}
            convert = anthropic_to_messages
        return convert({'messages': [{'role': 'user', 'content': 'q'}, message],
                        'strata_history_compatibility': compatibility})[0]

    def test_cut_history_requires_explicit_compatibility(self):
        for dialect in ('openai', 'anthropic'):
            with self.subTest(dialect=dialect):
                with self.assertRaises(ValueError):
                    self.history(dialect, '{cut', False)
                messages = self.history(dialect, '{cut', True)
                self.assertEqual(messages[1]['tool_calls'][0]['function']['arguments'], {'arguments': '{cut'})

    def test_compatibility_keeps_duplicate_nonfinite_unicode_and_depth_checks(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '{"x":"\\ud800"}',
                    '{"x":' + '[' * 129 + '0' + ']' * 129 + '}'):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                tool_arguments(raw)
        for dialect in ('openai', 'anthropic'):
            with self.subTest(dialect=dialect), self.assertRaises(ValueError):
                self.history(dialect, {'x': float('inf')}, True)

    def test_compatibility_never_repairs_bad_object_contents_or_incomplete_finish(self):
        for text, finish in (('```json\n{"x":NaN}\n```', 'stop'),
                             ('Prefix {"x":1,"x":2}', 'stop'), ('Prefix {}', 'length')):
            with self.subTest(text=text, finish=finish), self.assertRaises(StructuredOutputError):
                validated_json(text, _ObjectOnly(), finish, compatibility=True)

    def test_legacy_reference_cannot_ignore_the_object_contract(self):
        schema = {'$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object',
                  '$ref': '#/definitions/array', 'definitions': {'array': {'type': 'array'}}}
        with self.assertRaisesRegex(ValueError, 'only JSON objects'):
            prepare_format({'type': 'json_schema', 'json_schema': {'name': 'legacy', 'schema': schema}}, [])


class NamespaceAliases(unittest.TestCase):
    def parse(self, tools, text, reasoning=False):
        parser = OutputParser(thinking=reasoning, tools=tools, stream_tools=True)
        events = []
        for char in text:
            events += parser.feed(char)
        events += parser.finish('stop')
        return [event.call for event in events if event.kind == 'tool_call']

    def test_alias_retains_string_schema_and_reasoning_rescue(self):
        tools, names, _ = request_tools({'tools': TOOLS})
        for reasoning in (False, True):
            with self.subTest(reasoning=reasoning):
                calls = self.parse(tools, CALL, reasoning)
                self.assertEqual([(call.name, call.arguments) for call in calls], [('ns__read', {'value': '123'})])
        self.assertEqual(names['ns__read'], ('ns', 'read', 'function'))
        self.assertEqual(len(tools), 1)

    def test_real_name_wins_in_both_orders(self):
        real = {'type': 'function', 'name': 'ns__read', 'parameters': {
            'type': 'object', 'properties': {'value': {'type': 'integer'}}}}
        for definitions in ([real, *TOOLS], [*TOOLS, real]):
            with self.subTest(definitions=definitions):
                tools, names, _ = request_tools({'tools': definitions})
                self.assertEqual(names['ns__read'], (None, 'ns__read', 'function'))
                self.assertEqual(self.parse(tools, CALL)[0].arguments, {'value': 123})

    def test_ambiguous_flat_alias_is_not_offered(self):
        tools, names, _ = request_tools({'tools': [
            {'type': 'namespace', 'name': 'a__b', 'tools': [{'name': 'c'}]},
            {'type': 'namespace', 'name': 'a', 'tools': [{'name': 'b__c'}]}]})
        self.assertNotIn('a__b__c', names)
        self.assertNotIn('a__b__c', tools.aliases)
        self.assertEqual(set(names), {'a__b.c', 'a.b__c'})


class ManagedInteractions(unittest.TestCase):
    def service(self, text):
        tok = ByteTokenizer()
        return Service(MockEngine(tok, text, max_context=16384), tok,
                       ChatTemplate(ROOT / 'serve' / 'chat_template.jinja'))

    def test_failed_language_shutdown_still_cleans_vision_and_mcp(self):
        httpd, engine, vision, hub = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
        engine.close.side_effect = EngineStuck('native process is still exiting')
        with self.assertRaises(EngineStuck):
            shutdown_service(httpd, engine, vision, hub)
        httpd.shutdown.assert_called_once()
        httpd.server_close.assert_called_once()
        vision.shutdown.assert_called_once()
        hub.close.assert_called_once()

    def test_quoted_xml_never_reaches_mcp_execution(self):
        call = CALL.replace('ns__read', 'fake__read')
        for text in ('```xml\n' + call + '\n```', '`' + call + '`'):
            with self.subTest(text=text[:20]):
                svc = self.service(text)
                tools = [{'name': 'fake__read', 'parameters': {'type': 'object'}}]
                messages = [{'role': 'user', 'content': 'show syntax'}]
                ids, thinking, maximum = svc.prepare(messages, tools, {'enable_thinking': False}, 1024)
                hub = mock.Mock(settings={'max_rounds': 1})
                items = list(run_with_mcp(svc, hub, messages, tools, {}, ids, thinking, maximum, 1024, {},
                                         threading.Event(), {'fake__read'}))
                hub.call.assert_not_called()
                self.assertFalse(any(kind == 'mcp' for kind, _ in items))

    def test_cancelled_tool_image_does_not_wait_on_fifo_or_start_vision(self):
        svc = self.service('ok')
        svc.vision = mock.Mock()
        cancel = threading.Event()
        cancel.set()
        messages = [{'role': 'tool', 'content': [{'type': 'image', 'source': 'x.png'}]}]
        with svc.fifo, self.assertRaises(RequestCancelled):
            svc.prepare(messages, None, {}, cancel=cancel)
        svc.vision.encode.assert_not_called()
        svc.vision.restart.assert_not_called()

    def test_structured_compatibility_is_explicit_on_chat_and_responses_http(self):
        svc = self.service('Prefix {"answer":4} suffix')
        httpd = serve(svc, port=0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        for route, base in (
            ('/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'q'}],
                                    'response_format': {'type': 'json_object'}, 'reasoning_effort': 'none'}),
            ('/v1/responses', {'input': 'q', 'text': {'format': {'type': 'json_object'}},
                               'reasoning': {'effort': 'none'}})):
            for stream in (False, True):
                for compatibility in (False, True):
                    req = {**copy.deepcopy(base), 'stream': stream, 'strata_json_compatibility': compatibility}
                    with self.subTest(route=route, stream=stream, compatibility=compatibility):
                        connection = http.client.HTTPConnection('127.0.0.1', httpd.server_address[1], timeout=10)
                        try:
                            connection.request('POST', route, body=json.dumps(req),
                                               headers={'Content-Type': 'application/json'})
                            response = connection.getresponse()
                            raw = response.read().decode()
                            self.assertEqual(response.status, 200 if stream or compatibility else 502)
                            self.assertEqual('structured_output_failed' in raw, not compatibility)
                            if compatibility:
                                self.assertIn('answer', raw)
                            else:
                                self.assertNotIn('Prefix', raw)
                        finally:
                            connection.close()


if __name__ == '__main__':
    unittest.main()

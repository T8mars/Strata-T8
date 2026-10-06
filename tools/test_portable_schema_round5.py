"""Fifth audit R01-R03: schema dialect boundaries and reference semantics."""
import http.client
import json
from pathlib import Path
import unittest
from unittest import mock

from serve import structured
from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, MockEngine, Service, serve

DRAFT4 = 'http://json-schema.org/draft-04/schema#'
DRAFT7 = 'http://json-schema.org/draft-07/schema#'
DRAFT2020 = 'https://json-schema.org/draft/2020-12/schema'


def response_format(schema):
    return {'type': 'json_schema', 'json_schema': {'name': 'fixture', 'schema': schema}}


def compound_tuple():
    return {'$schema': DRAFT2020, '$id': 'https://fixture.invalid/root', 'type': 'object',
            'required': ['value'], 'properties': {'value': {
                '$schema': DRAFT7, '$id': 'https://fixture.invalid/legacy', 'type': 'array',
                'items': [{'type': 'integer'}, {'type': 'string'}], 'additionalItems': False}}}


def inherited_annotation():
    return {'$schema': DRAFT2020, '$id': 'https://fixture.invalid/root', 'type': 'object',
            'required': ['value'], 'properties': {'value': {
                '$schema': DRAFT7, '$id': 'https://fixture.invalid/legacy', 'type': 'object',
                'required': ['answer'], 'properties': {'answer': {
                    'type': 'integer', '$dynamicRef': 'https://fixture.invalid/annotation'}}}}}


@unittest.skipUnless(structured.jsonschema_modules(), 'jsonschema is not installed')
class SchemaDialects(unittest.TestCase):
    def test_root_malformed_and_unknown_dialects_are_value_errors(self):
        for dialect in ([], {}, None, 7, True, '', 'https://fixture.invalid/unknown-draft'):
            with self.subTest(dialect=dialect), self.assertRaises(ValueError):
                structured.prepare_format(response_format({'type': 'object', '$schema': dialect}), [])

    def test_embedded_resource_malformed_dialect_is_a_value_error(self):
        for dialect in ([], None, 'https://fixture.invalid/unknown-draft'):
            schema = compound_tuple()
            schema['properties']['value']['$schema'] = dialect
            with self.subTest(dialect=dialect), self.assertRaises(ValueError):
                structured.prepare_format(response_format(schema), [])

    def test_schema_keyword_inside_literal_data_keeps_its_data_meaning(self):
        value = {'$schema': [], '$ref': 'https://fixture.invalid/data'}
        schema = {'type': 'object', 'required': ['value'], 'properties': {'value': {'const': value}}}
        _, validator = structured.prepare_format(response_format(schema), [])
        self.assertEqual(json.loads(structured.validated_json(json.dumps({'value': value}), validator, 'stop')),
                         {'value': value})

    def test_compound_tuple_is_checked_with_its_declared_draft(self):
        _, validator = structured.prepare_format(response_format(compound_tuple()), [])
        self.assertEqual(structured.validated_json('{"value":[4,"ok"]}', validator, 'stop'), '{"value":[4,"ok"]}')
        with self.assertRaises(structured.StructuredOutputError):
            structured.validated_json('{"value":["wrong","ok"]}', validator, 'stop')

    def test_child_dialect_inherits_to_grandchild_schema(self):
        _, validator = structured.prepare_format(response_format(inherited_annotation()), [])
        self.assertEqual(structured.validated_json('{"value":{"answer":4}}', validator, 'stop'),
                         '{"value":{"answer":4}}')
        with self.assertRaises(structured.StructuredOutputError):
            structured.validated_json('{"value":{"answer":"wrong"}}', validator, 'stop')

    def test_old_draft_ignores_reference_sibling_assertions(self):
        for dialect in (DRAFT4, DRAFT7):
            schema = {'$schema': dialect, 'type': 'object', '$ref': '#/definitions/target',
                      'definitions': {'target': {'type': 'object'}},
                      'properties': {'value': {'$ref': 'https://fixture.invalid/ignored'}}}
            with self.subTest(dialect=dialect):
                _, validator = structured.prepare_format(response_format(schema), [])
                self.assertEqual(structured.validated_json('{"value":4}', validator, 'stop'), '{"value":4}')

    def test_reference_to_ignored_sibling_is_still_preflighted_when_it_becomes_active(self):
        for dialect in (DRAFT4, DRAFT7):
            schema = {'$schema': dialect, 'type': 'object', '$ref': '#/properties/value',
                      'properties': {'value': {'$ref': 'https://fixture.invalid/active'}}}
            with self.subTest(dialect=dialect), self.assertRaises(ValueError):
                structured.prepare_format(response_format(schema), [])

    def test_modern_draft_keeps_reference_sibling_assertions_active(self):
        schema = {'$schema': DRAFT2020, 'type': 'object', '$ref': '#/$defs/target',
                  '$defs': {'target': {'type': 'object'}},
                  'properties': {'value': {'$ref': 'https://fixture.invalid/active'}}}
        with self.assertRaises(ValueError):
            structured.prepare_format(response_format(schema), [])

    def test_old_parent_can_contain_a_modern_resource(self):
        schema = {'$schema': DRAFT7, '$id': 'https://fixture.invalid/old-root', 'type': 'object',
                  'required': ['value'], 'properties': {'value': {
                      '$schema': DRAFT2020, '$id': 'https://fixture.invalid/new-child', 'type': 'array',
                      'prefixItems': [{'type': 'integer'}], 'items': False}}}
        _, validator = structured.prepare_format(response_format(schema), [])
        self.assertEqual(structured.validated_json('{"value":[4]}', validator, 'stop'), '{"value":[4]}')
        with self.assertRaises(structured.StructuredOutputError):
            structured.validated_json('{"value":[4,5]}', validator, 'stop')

    def test_invalid_container_and_child_shapes_remain_rejected(self):
        for field, value in (('properties', []), ('allOf', 4), ('items', [])):
            schema = {'$schema': DRAFT2020, 'type': 'object', field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                structured.prepare_format(response_format(schema), [])
        schema = compound_tuple()
        schema['properties']['value']['items'][0]['type'] = 'invalid'
        with self.assertRaises(ValueError):
            structured.prepare_format(response_format(schema), [])

    def test_old_mixed_dependencies_keep_property_arrays_and_schema_assertions(self):
        for dialect in (DRAFT4, DRAFT7):
            for schema_first in (False, True):
                dependencies = {'schema': {'required': ['answer']}, 'property': ['answer']}
                if not schema_first:
                    dependencies = dict(reversed(list(dependencies.items())))
                schema = {'$schema': dialect, 'type': 'object', 'dependencies': dependencies}
                with self.subTest(dialect=dialect, schema_first=schema_first):
                    _, validator = structured.prepare_format(response_format(schema), [])
                    self.assertEqual(structured.validated_json('{"schema":1,"property":2,"answer":3}',
                                                               validator, 'stop'),
                                     '{"schema":1,"property":2,"answer":3}')
                    with self.assertRaises(structured.StructuredOutputError):
                        structured.validated_json('{"property":2}', validator, 'stop')
                    dependencies['schema']['$ref'] = 'https://fixture.invalid/active'
                    with self.assertRaises(ValueError):
                        structured.prepare_format(response_format(schema), [])


@unittest.skipUnless(structured.jsonschema_modules(), 'jsonschema is not installed')
class SchemaHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tokenizer = ByteTokenizer()
        cls.service = Service(MockEngine(tokenizer, '</think>\n\n{"value":[4,"ok"]}', max_context=16384),
                              tokenizer, ChatTemplate(Path(__file__).resolve().parents[1] / 'serve/chat_template.jinja'))
        cls.server = serve(cls.service, port=0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, schema, path='/v1/chat/completions'):
        body = {'messages': [{'role': 'user', 'content': 'fixture'}], 'max_tokens': 128,
                'response_format': response_format(schema)}
        if path == '/v1/responses':
            body = {'input': 'fixture', 'max_output_tokens': 128,
                    'text': {'format': {'type': 'json_schema', 'name': 'fixture', 'schema': schema}}}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        try:
            connection.request('POST', path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_bad_dialects_return_400_before_loading_in_chat_and_responses(self):
        schemas = [{'type': 'object', '$schema': []},
                   {'type': 'object', '$schema': 'https://fixture.invalid/unknown-draft'}]
        embedded = compound_tuple()
        embedded['properties']['value']['$schema'] = []
        schemas.append(embedded)
        for path in ('/v1/chat/completions', '/v1/responses'):
            for schema in schemas:
                with self.subTest(path=path, schema=schema), mock.patch.object(self.service, 'load') as load:
                    status, result = self.post(schema, path)
                    self.assertEqual(status, 400, result)
                    load.assert_not_called()

    def test_compound_schema_succeeds_through_both_http_apis(self):
        for path in ('/v1/chat/completions', '/v1/responses'):
            with self.subTest(path=path):
                status, result = self.post(compound_tuple(), path)
                self.assertEqual(status, 200, result)

    def test_inherited_dialect_validates_actual_http_generation(self):
        engine = self.service.engine
        for answer, expected in ((4, 200), ('wrong', 502)):
            script = engine.tok.encode('</think>\n\n' + json.dumps({'value': {'answer': answer}}),
                                       parse_special=True) + engine.tok.encode('<|im_end|>', parse_special=True)
            with self.subTest(answer=answer), mock.patch.object(engine, 'script', script):
                status, result = self.post(inherited_annotation())
                self.assertEqual(status, expected, result)

    def test_old_ref_siblings_remain_ignored_through_http(self):
        for dialect in (DRAFT4, DRAFT7):
            schema = {'$schema': dialect, 'type': 'object', '$ref': '#/definitions/target',
                      'definitions': {'target': {'type': 'object'}},
                      'properties': {'value': {'$ref': 'https://fixture.invalid/ignored'}}}
            with self.subTest(dialect=dialect):
                status, result = self.post(schema)
                self.assertEqual(status, 200, result)

    def test_active_target_in_old_ref_sibling_is_rejected_before_http_loading(self):
        schema = {'$schema': DRAFT7, 'type': 'object', '$ref': '#/properties/value',
                  'properties': {'value': {'$ref': 'https://fixture.invalid/active'}}}
        with mock.patch.object(self.service, 'load') as load:
            status, result = self.post(schema)
            self.assertEqual(status, 400, result)
            load.assert_not_called()

    def test_legacy_mixed_dependencies_anchor_failure_is_controlled_before_loading(self):
        for dialect in (DRAFT4, DRAFT7):
            target = {'id' if dialect == DRAFT4 else '$id': '#target', 'required': ['answer']}
            schema = {'$schema': dialect, 'type': 'object',
                      'dependencies': {'schema': {'$ref': '#target'}, 'property': ['answer']},
                      'definitions': {'target': target}}
            with self.subTest(dialect=dialect), self.assertRaisesRegex(ValueError, 'JSON Pointer'):
                structured.prepare_format(response_format(schema), [])
            for path in ('/v1/chat/completions', '/v1/responses'):
                with self.subTest(dialect=dialect, path=path), mock.patch.object(self.service, 'load') as load:
                    status, result = self.post(schema, path)
                    self.assertEqual(status, 400, result)
                    self.assertIn('JSON Pointer', result['error']['message'])
                    load.assert_not_called()
            schema['dependencies']['schema']['$ref'] = '#/definitions/target'
            _, validator = structured.prepare_format(response_format(schema), [])
            self.assertEqual(structured.validated_json('{"schema":1,"property":2,"answer":3}', validator, 'stop'),
                             '{"schema":1,"property":2,"answer":3}')


if __name__ == '__main__':
    unittest.main()

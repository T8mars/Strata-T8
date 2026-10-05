"""HTTP batch resource handoff, schema extraction and local profile security."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, Service, serve
from serve.test_lifecycle import ResidentEngine

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('strata_comfy_test', ROOT/'comfyui-strata-t8/__init__.py', submodule_search_locations=[str(ROOT/'comfyui-strata-t8')])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
core, nodes = package.nodes.core, package.nodes


class BatchHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(core, 'HOME', Path(self.temp.name))
        patch.start()
        self.addCleanup(patch.stop)
        tok = ByteTokenizer()
        self.engine = ResidentEngine(tok)
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT/'serve/chat_template.jinja'))
        self.httpd = serve(self.svc, port=0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.profile = {'mode': 'external', 'url': f'http://127.0.0.1:{self.httpd.server_address[1]}', 'allow_lifecycle': True}
        core.save_profile('test', self.profile)

    def test_batch_loads_once_pairs_results_and_releases_at_end(self):
        results, paired, custom = nodes.StrataBatch().run(core.Connection('test'), ['first', 'second'], max_tokens=64, reasoning_effort='none')
        self.assertEqual(results, ['Hello.', 'Hello.'])
        self.assertEqual(custom, results)
        items = json.loads(paired)
        self.assertEqual([item['input'] for item in items], ['first', 'second'])
        self.assertEqual(self.engine.starts, 1)
        self.assertFalse(self.engine.alive())
        self.assertEqual(self.engine.closes, 1)

    def test_external_inference_defaults_preserve_resident_service(self):
        core.save_profile('test', dict(self.profile, allow_lifecycle=False))
        result = nodes.StrataText().run(core.Connection('test'), 'hello', max_tokens=32, reasoning_effort='none')
        self.assertEqual(result[0], 'Hello.')
        self.assertTrue(self.engine.alive())
        with self.assertRaisesRegex(core.StrataError, 'explicitly'):
            core.control(core.Connection('test'), 'unload')
        with self.assertRaisesRegex(core.StrataError, 'managed'):
            core.control(core.Connection('test'), 'stop')

    def test_profile_and_connection_hide_credentials(self):
        profile = dict(self.profile, api_key='private-test-key')
        core.save_profile('test', profile)
        core.save_profile('test', dict(profile, api_key='__KEEP__'))
        self.assertEqual(core.read_profile('test')['api_key'], 'private-test-key')
        self.assertNotIn('private-test-key', repr(core.Connection('test')))
        for name in ('../bad', 'with/path', 'C:bad'):
            with self.assertRaises(core.StrataError):
                core.profile_path(name)

    def test_invalid_profile_does_not_replace_saved_profile(self):
        original = core.profile_path('test').read_bytes()
        for invalid in (dict(self.profile, url='file:///bad'), dict(self.profile, api_key='private\r\nkey')):
            with self.assertRaises(core.StrataError):
                core.save_profile('test', invalid)
            self.assertEqual(core.profile_path('test').read_bytes(), original)

    def test_preloaded_same_gpu_service_is_released_before_baseline(self):
        self.engine.restart()
        core.save_profile('test', dict(self.profile, same_gpu=True))
        def handoff(_):
            self.assertFalse(self.engine.alive())
            return None
        with mock.patch.object(core, 'gpu_handoff', side_effect=handoff):
            answer = nodes.StrataText().run(core.Connection('test'), 'hello', max_tokens=32, reasoning_effort='none')
        self.assertEqual(answer[0], 'Hello.')
        self.assertFalse(self.engine.alive())
        self.assertEqual(self.engine.starts, 2)

    def test_transport_errors_cannot_leak_api_key(self):
        key = 'private-secret-value'
        client = core.Client(dict(self.profile, api_key=key))
        with mock.patch('http.client.HTTPConnection.connect', side_effect=ValueError(key)):
            with self.assertRaises(core.StrataError) as caught:
                client.request('/v1/status', check=None)
        self.assertNotIn(key, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)


class Structured(unittest.TestCase):
    def test_lists_and_json_pointer_are_typed(self):
        source = json.dumps({'shots': [{'prompt': 'a', 'duration': 1.25}, {'prompt': 'b', 'duration': 2}]})
        value, items, custom = nodes.StrataExtract().run(source, '/shots', 'prompt', 'array')
        self.assertEqual(items, ['a', 'b'])
        self.assertEqual(custom, items)
        self.assertEqual(nodes.StrataNumber().run(source, '/shots/0/duration'), (1.25,))
        self.assertEqual(nodes.pointer({'a/b': {'~k': 'v'}}, '/a~1b/~0k'), 'v')
        with self.assertRaises(core.StrataError):
            nodes.StrataExtract().run(source, '/shots', '', 'string')
        with self.assertRaises(core.StrataError):
            nodes.StrataNumber().run('{"number":true}', '/number')

    def test_schema_rejects_bad_types_and_remote_reference(self):
        with self.assertRaises(Exception):
            nodes.validated('{"shots":[]}', nodes.STORY_SCHEMA)
        with self.assertRaisesRegex(core.StrataError, 'local fragments'):
            nodes.validated('{}', {'$ref': 'https://example.invalid/schema'})

    def test_history_does_not_accept_workflow_images_or_unbounded_turns(self):
        for history in ('{}', '[{"role":"tool","content":"x"}]', '[{"role":"user","content":[]}]'):
            with self.assertRaises(core.StrataError):
                nodes.request('hi', history=history)
        self.assertEqual(nodes.request('hi', refresh=4, seed=8)['seed'], 8)
        self.assertNotIn('refresh', nodes.request('hi', refresh=4))

    def test_sampling_and_schema_reject_invalid_values_before_inference(self):
        for options in ({'top_k': 0}, {'top_k': 100}, {'top_p': 0}):
            with self.assertRaises(core.StrataError):
                nodes.request('hi', **options)
        with mock.patch.object(core, 'generate') as generate:
            with self.assertRaisesRegex(core.StrataError, 'local fragments'):
                nodes.StrataStructured().run(core.Connection('test'), 'hi', schema='{"$ref":"https://example.invalid/"}')
            generate.assert_not_called()


class OwnedProcesses(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(core, 'HOME', Path(self.temp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_owned_uses_recorded_python_after_runtime_change(self):
        manager = core.Managed('test', {'runtime': str(Path(self.temp.name)/'new-runtime')})
        manager.state_path.write_text(json.dumps({'pid':123,'created':7,'python':sys.executable}),encoding='utf-8')
        proc = mock.Mock()
        proc.create_time.return_value=7
        proc.exe.return_value=sys.executable
        proc.cmdline.return_value=[sys.executable,str(manager.dir/'service.json')]
        with mock.patch('psutil.Process', return_value=proc):
            self.assertIs(manager.owned(),proc)
            proc.create_time.return_value=8
            self.assertIsNone(manager.owned())

    def test_disappearing_child_does_not_skip_remaining_kills(self):
        import psutil
        manager = core.Managed('test', {'runtime': self.temp.name})
        parent, first, second = mock.Mock(), mock.Mock(), mock.Mock()
        parent.children.return_value=[first,second]
        first.kill.side_effect=psutil.NoSuchProcess(123)
        with mock.patch.object(manager,'owned',return_value=parent), mock.patch('psutil.wait_procs',side_effect=[([],[first,second]),([] ,[])]):
            manager.stop()
        first.kill.assert_called_once()
        second.kill.assert_called_once()
        parent.terminate.assert_called_once()

    def test_profile_save_waits_for_active_profile_transaction(self):
        original={'mode':'external','url':'http://127.0.0.1:8080'}
        core.save_profile('test',original)
        saved=threading.Event()
        def save():
            core.save_profile('test',dict(original,url='http://127.0.0.1:8081'))
            saved.set()
        with core.profile_lock('test'):
            worker=threading.Thread(target=save);worker.start()
            self.assertFalse(saved.wait(.15))
            self.assertEqual(core.read_profile('test')['url'],original['url'])
        self.assertTrue(saved.wait(3))
        worker.join()


if __name__ == '__main__':
    unittest.main()

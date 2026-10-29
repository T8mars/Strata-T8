"""Speech numeric/body boundaries and damaged-cache recovery without models."""
import io
import json
from pathlib import Path
import threading
import unittest
import urllib.error
from unittest import mock

from serve import test_asr as http_fixture
from serve import test_asr_lifecycle as worker_fixture
from serve import test_video_tasks as video_fixture
from serve.asr import contract


class SpeechNumericBounds(unittest.TestCase):
    def test_unrepresentable_integer_is_a_validation_error(self):
        for value in (10**400, -(10**400)):
            with self.subTest(sign=value > 0), self.assertRaisesRegex(ValueError, 'finite'):
                contract.number(value, 'speech time')

    def test_confucius_unrepresentable_duration_is_a_validation_error(self):
        with self.assertRaisesRegex(ValueError, 'finite'):
            contract.from_confucius({'status':'completed','text':'','audio_seconds':10**400})

    def test_finite_but_out_of_range_confucius_duration_is_rejected_before_integer_conversion(self):
        for seconds in (1e308, -1e308):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, 'duration'):
                contract.from_confucius({'status':'completed','text':'','audio_seconds':seconds})

    def test_confucius_subsample_rounding_at_duration_limits_is_preserved(self):
        for seconds in (3600.00001, -.00001):
            with self.subTest(seconds=seconds):
                value = contract.from_confucius({'status':'completed','text':'','audio_seconds':seconds})
                self.assertEqual(value['status'], 'no_speech')
        for seconds in (3600.00004, -.00004):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, 'duration'):
                contract.from_confucius({'status':'completed','text':'','audio_seconds':seconds})


class SpeechNumericHTTP(unittest.TestCase):
    setUp = http_fixture.SpeechHTTP.setUp
    tearDown = http_fixture.SpeechHTTP.tearDown
    request = http_fixture.SpeechHTTP.request

    def test_huge_json_cue_is_rejected_without_losing_the_http_response(self):
        value = http_fixture.timed(cues=[{'id':'one','start_s':10**400,
                                        'end_s':10**400+1,'text':'hello'}])
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/v1/asr/transcript/parse', {'text':json.dumps(value)})
        self.assertEqual(failure.exception.code, 400)
        valid = self.request('/v1/asr/transcript/parse', {'text':'1\n00:00:01,000 --> 00:00:02,000\nhello'})
        self.assertEqual(valid['cues'][0]['start_s'], 1)
        self.assertFalse(hasattr(self.service, 'asr_manager'))


class VideoNumericHTTP(unittest.TestCase):
    setUp = http_fixture.SpeechHTTP.setUp
    tearDown = http_fixture.SpeechHTTP.tearDown
    request = http_fixture.SpeechHTTP.request

    def test_huge_plan_fields_return_400_before_creating_task_or_promoting_source(self):
        source = self.request('/v1/video/sources', {'owner':video_fixture.OWNER,'data':'eA=='})
        marker = self.root/'.video-tasks'/(source['id']+'.source.json')
        original = marker.read_bytes()
        for target, field in (('plan','end_s'),('segment','end_s'),('segment','sampling_fps')):
            plan = json.loads(json.dumps(video_fixture.PLAN))
            (plan if target == 'plan' else plan['segments'][0])[field] = 10**400
            with self.subTest(target=target,field=field):
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    self.request('/v1/video/tasks', {'owner':video_fixture.OWNER,'source_id':source['id'],'config':{'plan':plan}})
                self.assertEqual(failure.exception.code, 400)
                self.assertEqual(marker.read_bytes(), original)
                self.assertEqual(list((self.root/'.video-tasks').glob('*.task.json')), [])

    def test_huge_checkpoint_time_does_not_advance_revision(self):
        source = self.request('/v1/video/sources', {'owner':video_fixture.OWNER,'data':'eA=='})
        task = self.request('/v1/video/tasks', {'owner':video_fixture.OWNER,'source_id':source['id'],
            'config':{'plan':video_fixture.PLAN}})
        state = {'notes':[{'start_s':0,'end_s':4,'frames':1,'text':'first window'}],
                 'times':[10**400],'empty_segments':0,'phase':'observe'}
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/v1/video/tasks/checkpoint', {'owner':video_fixture.OWNER,
                'id':task['id'],'revision':0,'state':state})
        self.assertEqual(failure.exception.code, 400)
        saved = self.request('/v1/video/tasks/get', {'owner':video_fixture.OWNER,'id':task['id']})
        self.assertEqual(saved['revision'], 0)
        self.assertEqual(saved['state']['notes'], [])
        state['times'] = [.5]
        saved = self.request('/v1/video/tasks/checkpoint', {'owner':video_fixture.OWNER,
            'id':task['id'],'revision':0,'state':state})
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(saved['state']['times'], [.5])

    def test_deep_optional_speech_profile_does_not_block_visual_only_task(self):
        profile = self.root/'asr-settings.json'
        damaged = '{"junk":'+('['*10000)+'0'+(']'*10000)+'}'
        profile.write_text(damaged, encoding='utf-8')
        source = self.request('/v1/video/sources', {'owner':video_fixture.OWNER,'data':'eA=='})
        task = self.request('/v1/video/tasks', {'owner':video_fixture.OWNER,'source_id':source['id'],
            'config':{'plan':video_fixture.PLAN,'asr':False}})
        saved = self.request('/v1/video/tasks/get', {'owner':video_fixture.OWNER,'id':task['id']})
        self.assertEqual(saved['state']['notes'], [])
        self.assertEqual(profile.read_text('utf-8'), damaged)
        profile.write_text('{}', encoding='utf-8')
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/v1/video/tasks/get', {'owner':video_fixture.OWNER,'id':task['id']})
        self.assertEqual(failure.exception.code, 400)


class DamagedSpeechCache(unittest.TestCase):
    setUp = worker_fixture.OwnedWorker.setUp
    complete_worker = worker_fixture.OwnedWorker.complete_worker
    write_worker = worker_fixture.OwnedWorker.write_worker

    def test_deeply_nested_cache_is_recomputed_and_replaced(self):
        self.complete_worker()
        first = self.manager.transcribe(io.BytesIO(b'fixture'))
        path = self.root/'.asr-cache'/(first['cache_key']+'.json')
        depth = 10000
        path.write_text('{"junk":'+('['*depth)+'0'+(']'*depth)+'}', encoding='utf-8')
        recovered = self.manager.transcribe(io.BytesIO(b'fixture'))
        self.assertFalse(recovered['cache_hit'])
        self.assertEqual(recovered['cues'][0]['text'], 'hello')
        self.assertEqual(self.manager.progress()['phase'], 'completed')
        self.assertEqual(contract.transcript(json.loads(path.read_text('utf-8')))['cache_key'], first['cache_key'])
        with mock.patch('subprocess.Popen', side_effect=AssertionError('healed cache must avoid worker')):
            self.assertTrue(self.manager.transcribe(io.BytesIO(b'fixture'))['cache_hit'])

    def test_cancellation_during_bad_cache_read_does_not_start_a_worker(self):
        self.complete_worker()
        self.manager.transcribe(io.BytesIO(b'fixture'))
        cancel = threading.Event()
        read = Path.read_text
        def corrupted(path, *args, **kwargs):
            if path.parent.resolve() == (self.root/'.asr-cache').resolve():
                cancel.set()
                return '{"junk":'+('['*10000)+'0'+(']'*10000)+'}'
            return read(path, *args, **kwargs)
        with mock.patch.object(Path, 'read_text', corrupted), \
                mock.patch('subprocess.Popen', side_effect=AssertionError('cancelled cache must not start worker')):
            with self.assertRaises(InterruptedError):
                self.manager.transcribe(io.BytesIO(b'fixture'), cancel=cancel)
        self.assertEqual(self.manager.progress()['phase'], 'cancelled')


if __name__ == '__main__':
    unittest.main()

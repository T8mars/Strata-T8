"""Video protocol, ordered media binding and cancellation without a GPU."""
import base64
import copy
import http.client
import io
import json
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

from PIL import Image
from serve import video
from serve.frontend import ChatTemplate, openai_to_messages
from serve.server import (ByteTokenizer, MockEngine, Service, Vision, RequestCancelled,
                          EngineStarting, engine_args, serve)
from tools import strata_video
from tools.test_strata_video import av, write_video


def png(color='red'):
    out = io.BytesIO()
    Image.new('RGB', (64, 32), color).save(out, format='PNG')
    return out.getvalue()


def part(mode='native', count=4):
    return {'type': 'strata_video', 'mode': mode, 'contract_version': 1,
            'frames': [{'source': 'data:image/png;base64,' + base64.b64encode(png('red' if i % 2 else 'blue')).decode(),
                        'timestamp_s': i/2, 'source_timestamp_s': 12+i/2, 'index': i}
                       for i in range(count)], 'metadata': {'duration_s': count/2, 'time_origin_s': 12}}


class FakeVision:
    native_video = video_requested = True
    def __init__(self, directory):
        self.dir, self.calls = Path(directory), []
    def alive(self):
        return True
    def _write(self, kind):
        rows = 2
        path = self.dir / f'{len(self.calls)}.sve'
        path.write_bytes(struct.pack('<4sIIII', b'SVE1', rows, 2, 1, 2560) + b'\0' * rows * 2560 * 4)
        self.calls.append(kind)
        return path, rows
    def encode_pair(self, a, b, **kwargs):
        return self._write('video')
    def encode(self, source, **kwargs):
        return self._write('image')


class VideoContract(unittest.TestCase):
    def test_normalized_explicit_contract(self):
        messages, _, _ = openai_to_messages({'messages': [{'role': 'user', 'content': [part()]}]})
        self.assertEqual(messages[0]['content'][0]['mode'], 'native')

    def test_reject_bad_contract_before_gpu(self):
        for change in ({'contract_version': True}, {'contract_version': 1.0}, {'contract_version': 2}, {'mode': 'auto'},
                       {'frames': []}, {'frames': [None]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                video.video_part({**part(), **change})

    def test_clip_window_and_declared_sampling_rate_are_bounded(self):
        for metadata in ({'duration_s': 3601}, {'sampling_fps': True},
                         {'sampling_fps': 0}, {'sampling_fps': 121}):
            data = part()
            data['metadata'].update(metadata)
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                video.video_part(data)
        data = part(count=2)
        data['metadata'].pop('duration_s')
        data['frames'][1].update(timestamp_s=3600, source_timestamp_s=3612)
        with self.assertRaises(ValueError):
            video.video_part(data)

    def test_reject_times_indices_and_remote_sources(self):
        for key, value in [('timestamp_s', float('nan')), ('timestamp_s', 0),
                           ('source_timestamp_s', 12), ('index', 0), ('source', 'https://example.com/frame.png')]:
            data = part()
            data['frames'][1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                video.video_part(data)

    def test_source_and_clip_clocks_must_agree(self):
        data = part()
        data['frames'][1]['source_timestamp_s'] = 12.6
        with self.assertRaises(ValueError):
            video.video_part(data)

    def test_system_video_rejected(self):
        with self.assertRaises(ValueError):
            openai_to_messages({'messages': [{'role': 'system', 'content': [part()]}]})

    def test_frames_mode_uses_images_and_observed_times(self):
        result = video.expand_videos([{'role': 'user', 'content': [part('frames')]}], Vision.load, lambda: None)
        content = result[0]['content']
        self.assertEqual([x['type'] for x in content], ['text', 'image']*4)
        self.assertEqual(content[2]['text'], '<0.5 seconds>')

    def test_native_fused_pair_times_and_odd_last_copy(self):
        data = part(count=3)
        original = copy.deepcopy(data)
        result = video.expand_videos([{'role': 'user', 'content': [data]}], Vision.load, lambda: None)
        content = result[0]['content']
        self.assertEqual(content[0]['type'], 'video')
        pairs = content[0]['pairs']
        self.assertEqual(round(pairs[0]['timestamp_s'], 1), .2)
        self.assertEqual(pairs[1]['timestamp_s'], 1)
        self.assertEqual(pairs[1]['pair'][0], pairs[1]['pair'][1])
        self.assertEqual(original, data)

    def test_whole_request_frame_limit(self):
        with self.assertRaises(ValueError):
            video.expand_videos([{'role': 'user', 'content': [part(count=40), part(count=40)]}], Vision.load, lambda: None)

    def test_cancel_during_cpu_plan(self):
        def cancel():
            raise RequestCancelled()
        with self.assertRaises(RequestCancelled):
            video.expand_videos([{'role': 'user', 'content': [part()]}], Vision.load, cancel)

    def test_opt_in_flags(self):
        cfg = {'args': ['--vision'], 'vision': {'native_video': True}}
        self.assertIn('--native-video', engine_args(cfg))
        self.assertNotIn('--native-video', engine_args({'args': ['--vision'], 'vision': {}}))


class OrderedBinding(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tok = ByteTokenizer()
        self.engine = MockEngine(self.tok, 'ok')
        self.engine.native_video = self.engine.native_video_build = True
        self.vision = FakeVision(directory.name)
        self.service = Service(self.engine, self.tok,
                               ChatTemplate(Path(__file__).with_name('chat_template.jinja')), vision=self.vision)
        self.addCleanup(self.service.discard_embeddings)

    def prepare(self, items):
        messages, tools, kwargs = openai_to_messages({'messages': [{'role': 'user', 'content': items}]})
        with mock.patch.object(self.service, 'ensure_loaded'):
            return self.service.prepare(messages, tools, {**kwargs, 'enable_thinking': False}, 32)

    def test_mixed_order_exact_spans_keep_video_pad(self):
        image = {'type': 'image_url', 'image_url': {'url': part()['frames'][0]['source']}}
        ids, _, _ = self.prepare([image, part()])
        spans = [[int(x) for x in entry.split(':')] for entry in self.service.embeddings.spans.split(',')]
        self.assertEqual(self.vision.calls, ['image', 'video', 'video'])
        self.assertEqual([pad for _, pad, _ in spans], [260, 262, 262])
        for start, pad, rows in spans:
            self.assertEqual(ids[start:start+rows], [pad]*rows)
        self.assertEqual(self.service.embeddings.path.stat().st_size, 3*(20+2*2560*4))

    def test_literals_do_not_take_video_embeddings(self):
        ids, _, _ = self.prepare([{'type': 'text', 'text': '<|vision_start|><|video_pad|><|vision_end|>'}, part()])
        self.assertEqual(ids.count(262), 4)
        self.assertEqual(len(self.service.embeddings.spans.split(',')), 2)

    def test_context_refusal_deletes_snapshot(self):
        self.engine.max_context = 32
        with self.assertRaises(ValueError):
            self.prepare([part()])
        self.assertIsNone(self.service.embeddings.path)
        self.assertFalse(list(self.vision.dir.glob('req-*.sve')))

    def test_impossible_context_refused_before_load_or_encode(self):
        self.engine.max_context = 32
        messages, tools, kwargs = openai_to_messages({'messages': [{'role': 'user', 'content': [part()]}]})
        with mock.patch.object(self.service, 'ensure_loaded') as load:
            with self.assertRaises(ValueError):
                self.service.prepare(messages, tools, {**kwargs, 'enable_thinking': False}, 32)
        load.assert_not_called()
        self.assertEqual(self.vision.calls, [])

    def test_native_disabled_rejects_without_encoding(self):
        self.vision.video_requested = False
        with self.assertRaises(ValueError):
            self.prepare([part()])
        self.assertEqual(self.vision.calls, [])

    def test_missing_loaded_capability_rejects(self):
        self.engine.native_video = False
        with self.assertRaises(ValueError):
            self.prepare([part()])
        self.assertEqual(self.vision.calls, [])

    def test_invalid_embedding_header_rejected(self):
        path = self.vision.dir / 'bad.sve'
        path.write_bytes(struct.pack('<4sIIII', b'SVE1', 2, 2, 1, 2560))
        with self.assertRaises(ValueError):
            video.embedding_rows(path, 2)


@unittest.skipUnless(av is not None, 'PyAV and NumPy are required for upload fixtures')
class VideoUploadHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='strata-video-http-')
        source = Path(cls.directory.name) / 'upload.mp4'
        write_video(source, [i/8 for i in range(24)])
        cls.encoded = base64.b64encode(source.read_bytes()).decode()
        cls.tok = ByteTokenizer()
        cls.engine = MockEngine(cls.tok, 'must not generate')
        cls.engine.alive = lambda: False
        cls.engine.unloaded = True
        cls.engine.restart = mock.Mock(side_effect=AssertionError('sampling loaded the model'))
        cls.engine.generate = mock.Mock(side_effect=AssertionError('sampling ran inference'))
        cls.vision = FakeVision(cls.directory.name)
        cls.vision.alive = lambda: False
        cls.service = Service(cls.engine, cls.tok,
                              ChatTemplate(Path(__file__).with_name('chat_template.jinja')), vision=cls.vision)
        cls.service.ensure_loaded = mock.Mock(side_effect=AssertionError('sampling called ensure_loaded'))
        cls.httpd = serve(cls.service, host='127.0.0.1', port=0)
        cls.base = f'http://127.0.0.1:{cls.httpd.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.directory.cleanup()

    def setUp(self):
        self.engine.restart.reset_mock()
        self.engine.generate.reset_mock()
        self.service.ensure_loaded.reset_mock()
        self.service.api_key = ''
        self.engine.native_video_build = False
        self.vision.calls.clear()

    def tearDown(self):
        self.service.api_key = ''
        self.engine.restart.assert_not_called()
        self.engine.generate.assert_not_called()
        self.service.ensure_loaded.assert_not_called()
        self.assertFalse(self.service.loaded())
        self.assertFalse(self.vision.alive())
        self.assertEqual(self.vision.calls, [])

    def post(self, body, *, raw=None, headers=None, path='/v1/video/sample'):
        request = urllib.request.Request(self.base + path,
            data=raw if raw is not None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_chat_rejects_invalid_video_before_loading_or_encoding(self):
        for parts, enabled in (([part(count=40), part(count=40)], True), ([part()], False)):
            with self.subTest(frame_counts=[len(p['frames']) for p in parts], enabled=enabled), \
                 mock.patch.object(self.vision, 'video_requested', enabled, create=True), \
                 mock.patch.object(self.service, 'load') as load:
                status, result = self.post({'messages':[{'role':'user','content':parts}], 'max_tokens':32},
                                           path='/v1/chat/completions')
                self.assertEqual(status, 400, result)
                load.assert_not_called()

    def test_actual_upload_preserves_pts_and_does_not_load_model(self):
        status, result = self.post({'data': self.encoded, 'max_frames': 8})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['part']['mode'], 'frames')
        self.assertEqual([f['timestamp_s'] for f in result['part']['frames']], [0, 1, 2])
        self.assertEqual([f['source_timestamp_s'] for f in result['part']['frames']], [0, 1, 2])
        strata_video.validate_manifest(result['manifest'], 3)
        self.assertTrue(result['manifest']['coverage']['sampling_completed'])
        self.assertFalse(result['manifest']['coverage']['decode_completed'])
        for frame in result['part']['frames']:
            with Image.open(io.BytesIO(Vision.load(frame['source']))) as image:
                self.assertEqual(image.size, (96, 64))

    def test_custom_density_can_sample_more_than_eight_frames_in_both_modes_without_loading(self):
        self.engine.native_video_build = True
        for mode in ('frames','native'):
            with self.subTest(mode=mode):
                status, result = self.post({'data':self.encoded,'mode':mode,'max_frames':32,'sampling_fps':8})
                self.assertEqual(status,200,result)
                self.assertEqual(result['part']['mode'],mode)
                self.assertEqual(len(result['part']['frames']),24)
                self.assertEqual(result['manifest']['sampling']['max_frames'],32)
                self.assertEqual(result['manifest']['sampling']['sampling_fps'],8)
                self.assertAlmostEqual(result['manifest']['coverage']['max_sample_gap_s'],0.125)

    def test_dense_windows_cover_288_cfr_source_frames_at_24_fps_without_loading(self):
        from fractions import Fraction
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'dense24.mp4'
            with av.open(str(path), 'w') as output:
                stream = output.add_stream('libx264', rate=24)
                stream.width, stream.height, stream.pix_fmt = 96, 64, 'yuv420p'
                stream.time_base = stream.codec_context.time_base = Fraction(1,24)
                stream.codec_context.options = {'bf':'0','g':'24','threads':'1','preset':'ultrafast'}
                for index in range(288):
                    frame = av.VideoFrame.from_image(Image.new('RGB',(96,64),'red' if index%2 else 'blue'))
                    frame.pts, frame.time_base = index, Fraction(1,24)
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)
            encoded = base64.b64encode(path.read_bytes()).decode()
        self.engine.native_video_build = True
        for mode in ('frames','native'):
            times = []
            for segment in range(18):
                status, result = self.post({'data':encoded,'mode':mode,'max_frames':16,'sampling_fps':24,
                                           'start_s':segment*16/24,'end_s':(segment+1)*16/24})
                self.assertEqual(status,200,result)
                self.assertEqual(len(result['part']['frames']),16)
                times.extend(f['source_timestamp_s'] for f in result['part']['frames'])
            self.assertEqual(len(set(times)),288)
            for index, pts in enumerate(times):
                self.assertAlmostEqual(pts,index/24)

    def test_trim_keeps_absolute_clock_and_relative_clip(self):
        status, result = self.post({'data': self.encoded, 'start_s': 1.25, 'end_s': 2.75,
                                    'sampling_fps': 2, 'max_frames': 8})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['part']['metadata']['time_origin_s'], 1.25)
        self.assertEqual(result['part']['metadata']['duration_s'], 1.5)
        self.assertEqual([f['timestamp_s'] for f in result['part']['frames']], [0, .5, 1])
        self.assertEqual([f['source_timestamp_s'] for f in result['part']['frames']], [1.25, 1.75, 2.25])

    def test_malformed_json_shapes_and_base64_are_400(self):
        for body in (None, [], {}, {'data': False}, {'data': '%%%'},
                     {'data': base64.b64encode(b'not a video').decode()},
                     {'data': self.encoded, 'max_frames': True},
                     {'data': self.encoded, 'max_frames': 1},
                     {'data': self.encoded, 'max_frames': 65},
                     {'data': self.encoded, 'mode': 'auto'},
                     {'data': self.encoded, 'sampling_fps': float('nan')},
                     {'data': self.encoded, 'start_s': 4}):
            with self.subTest(body=str(body)[:70]):
                status, result = self.post(body)
                self.assertEqual(status, 400, result)
                self.assertEqual(result['error']['type'], 'invalid_request_error')
        status, _ = self.post({}, raw=b'{broken')
        self.assertEqual(status, 400)

    def test_oversize_rejected_before_cpu_decode(self):
        with mock.patch.object(video, 'MAX_VIDEO_BYTES', 100), \
                mock.patch.object(strata_video, 'sample_video') as decoder:
            status, result = self.post({'data': base64.b64encode(b'x'*101).decode()})
        self.assertEqual(status, 400, result)
        decoder.assert_not_called()

    def test_api_key_checked_before_decode(self):
        self.service.api_key = 'video-upload-test'
        with mock.patch.object(strata_video, 'sample_video') as decoder:
            status, _ = self.post({'data': self.encoded})
        self.assertEqual(status, 401)
        decoder.assert_not_called()
        status, result = self.post({'data': self.encoded}, headers={'Authorization': 'Bearer video-upload-test'})
        self.assertEqual(status, 200, result)

    def test_sampling_requires_vision_and_native_capability(self):
        with mock.patch.object(self.service, 'vision', None):
            status, _ = self.post({'data': self.encoded})
        self.assertEqual(status, 400)
        status, _ = self.post({'data': self.encoded, 'mode': 'native'})
        self.assertEqual(status, 400)
        self.engine.native_video_build = True
        status, result = self.post({'data': self.encoded, 'mode': 'native'})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['part']['mode'], 'native')

    def test_timeout_returns_error_and_next_upload_recovers(self):
        with mock.patch.object(video, 'sample_upload_isolated', side_effect=TimeoutError('CPU decode timeout')):
            status, result = self.post({'data': self.encoded})
        self.assertEqual(status, 400, result)
        self.assertIn('timeout', result['error']['message'])
        status, result = self.post({'data': self.encoded})
        self.assertEqual(status, 200, result)

    def test_http_disconnect_cancels_decoder_and_recovers(self):
        entered, stopped = threading.Event(), threading.Event()
        def decode(*args, cancel=None, **kwargs):
            entered.set()
            try:
                if not cancel.wait(4):
                    raise AssertionError('HTTP disconnect did not cancel the decoder')
                raise strata_video.VideoCancelled('client disconnected')
            finally:
                stopped.set()
        with mock.patch.object(video, 'sample_upload_isolated', side_effect=decode):
            connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=5)
            try:
                connection.request('POST', '/v1/video/sample', json.dumps({'data': self.encoded}),
                                   {'Content-Type': 'application/json'})
                self.assertTrue(entered.wait(2))
                connection.sock.shutdown(2)
            finally:
                connection.close()
            self.assertTrue(stopped.wait(2))
        status, result = self.post({'data': self.encoded})
        self.assertEqual(status, 200, result)

    def test_status_upload_byte_limit_matches_decoder(self):
        limit = self.service.v1_status()['capabilities']['video']['limits']['max_input_bytes']
        self.assertEqual(limit, video.MAX_VIDEO_BYTES)


FAKE_PAIR_HELPER = r'''
import json, shlex, struct, sys, time
from pathlib import Path
print('READY 2560 video=1 encv=1', flush=True)
for line in sys.stdin:
    fields = shlex.split(line)
    if fields[0] == 'QUIT':
        break
    assert fields[0] == 'ENCV' and len(fields) == 4, fields
    inputs = [Path(name).read_bytes() for name in fields[1:3]]
    with Path('calls.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'fields': fields, 'line': line.rstrip('\n'),
                              'inputs': [len(value) for value in inputs]}) + '\n')
    mode = Path('mode').read_text() if Path('mode').exists() else 'ok'
    if mode == 'hang':
        time.sleep(60)
    if mode == 'exit':
        break
    if mode == 'error':
        print('ERR invalid pair fixture', flush=True)
        continue
    rows, nx = 2, 3 if mode == 'bad-grid' else 2
    Path(fields[3]).write_bytes(struct.pack('<4sIIII', b'SVE1', rows, nx, 1, 2560) + b'\0' * rows * 2560 * 4)
    if mode == 'write-hang':
        time.sleep(60)
    if mode == 'bad-count':
        print('OK invalid 2 1 1', flush=True)
    elif mode == 'wrong-response':
        print('OKAY 2 2 1 1', flush=True)
    else:
        print('OK 2 2 1 1', flush=True)
'''


class VideoEncoderProcesses(unittest.TestCase):
    def make(self):
        obj = Vision({'exe': 'unused', 'mmproj': 'unused', 'model': 'unused',
                      'native_video': True, 'encode_timeout_s': 2}, lazy=True)
        self.addCleanup(shutil.rmtree, obj.dir, True)
        self.addCleanup(obj.close, True)
        obj.spawn = ([sys.executable, '-u', '-c', FAKE_PAIR_HELPER], None, None)
        obj.restart()
        self.assertTrue(obj.native_video)
        return obj

    def calls(self, obj):
        path = obj.dir / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text('utf-8').splitlines()] if path.exists() else []

    def test_real_subprocess_quoted_protocol_order_and_cache(self):
        obj = self.make()
        first, second = png('red'), png('blue')
        path, rows = obj.encode_pair(first, second)
        self.assertEqual(rows, 2)
        self.assertEqual(video.embedding_rows(path, rows), 2)
        self.assertEqual(obj.encode_pair(first, second), (path, rows))
        reverse, _ = obj.encode_pair(second, first)
        self.assertNotEqual(path, reverse)
        calls = self.calls(obj)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['inputs'], [len(first), len(second)])
        self.assertEqual(calls[1]['inputs'], [len(second), len(first)])
        self.assertTrue(all(Path(name).name == name for call in calls for name in call['fields'][1:]))
        self.assertTrue(all(re.fullmatch(r'ENCV "[^"]+" "[^"]+" "[^"]+"', call['line']) for call in calls))
        self.assertFalse(list(obj.dir.glob('*.png')))
        self.assertTrue(obj.alive())

    def test_cancelled_cached_pair_is_not_returned(self):
        obj = self.make()
        obj.encode_pair(png('red'), png('blue'))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(RequestCancelled):
            obj.encode_pair(png('red'), png('blue'), cancel=cancel)
        self.assertEqual(len(self.calls(obj)), 1)

    def test_timeout_kills_child_cleans_and_restarts(self):
        obj = self.make()
        normal_timeout = obj.encode_timeout_s
        (obj.dir/'mode').write_text('hang')
        obj.encode_timeout_s = .2
        before = time.monotonic()
        with self.assertRaises(EngineStarting):
            obj.encode_pair(png('red'), png('blue'))
        self.assertLess(time.monotonic()-before, 3)
        self.assertIsNone(obj.proc)
        self.assertFalse(list(obj.dir.glob('*.png')))
        self.assertFalse(list(obj.dir.glob('*.sve')))
        (obj.dir/'mode').write_text('ok')
        obj.encode_timeout_s = normal_timeout
        obj.restart()
        path, rows = obj.encode_pair(png('red'), png('blue'))
        self.assertEqual(video.embedding_rows(path, rows), 2)

    def test_cancellation_kills_child_cleans_and_restarts(self):
        obj = self.make()
        (obj.dir/'mode').write_text('hang')
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(RequestCancelled):
            obj.encode_pair(png('red'), png('blue'), cancel=cancel)
        self.assertIsNone(obj.proc)
        self.assertFalse(list(obj.dir.glob('*.png')))
        self.assertFalse(list(obj.dir.glob('*.sve')))
        (obj.dir/'mode').write_text('ok')
        obj.restart()
        self.assertEqual(obj.encode_pair(png('red'), png('blue'))[1], 2)

    def test_partial_output_timeout_preserves_completed_cache(self):
        obj = self.make()
        first, second = png('red'), png('blue')
        cached, rows = obj.encode_pair(first, second)
        (obj.dir/'mode').write_text('write-hang')
        obj.encode_timeout_s = .2
        with self.assertRaises(EngineStarting):
            obj.encode_pair(second, first)
        self.assertEqual(list(obj.dir.glob('*.sve')), [cached])
        self.assertFalse(list(obj.dir.glob('*.png')))
        self.assertIsNone(obj.proc)
        (obj.dir/'mode').write_text('ok')
        obj.restart()
        self.assertEqual(obj.encode_pair(first, second), (cached, rows))
        self.assertEqual(len(self.calls(obj)), 2)

    def test_malformed_result_and_grid_cleanup_then_recover(self):
        obj = self.make()
        for mode in ('error', 'bad-count', 'bad-grid', 'wrong-response'):
            (obj.dir/'mode').write_text(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                obj.encode_pair(png('red'), png('blue'))
            self.assertFalse(list(obj.dir.glob('*.png')))
            self.assertFalse(list(obj.dir.glob('*.sve')))
            self.assertEqual(obj.cache, {})
        (obj.dir/'mode').write_text('ok')
        self.assertEqual(obj.encode_pair(png('red'), png('blue'))[1], 2)

    def test_dead_helper_is_detected_then_can_restart(self):
        obj = self.make()
        (obj.dir/'mode').write_text('exit')
        with self.assertRaises(ValueError):
            obj.encode_pair(png('red'), png('blue'))
        self.assertFalse(list(obj.dir.glob('*.png')))
        self.assertFalse(list(obj.dir.glob('*.sve')))
        (obj.dir/'mode').write_text('ok')
        obj.restart()
        self.assertEqual(obj.encode_pair(png('red'), png('blue'))[1], 2)

    def test_exited_helper_with_buffered_stdin_closes_and_restarts(self):
        obj = self.make()
        cached, rows = obj.encode_pair(png('red'), png('blue'))
        proc, pump = obj.proc, obj.pump
        proc.kill()
        proc.wait(timeout=5)
        pump.join(timeout=2)
        self.assertFalse(pump.is_alive())
        # No newline: keep bytes buffered until close flushes to the dead child.
        proc.stdin.write('pending-buffered-command')
        obj.close()
        self.assertTrue(proc.stdin.closed)
        self.assertTrue(proc.stdout.closed)
        self.assertIsNone(obj.proc)
        self.assertIsNone(obj.pump)
        self.assertTrue(obj.stopped)
        self.assertTrue(cached.is_file())
        obj.close()
        obj.restart()
        self.assertTrue(obj.alive())
        self.assertEqual(obj.encode_pair(png('red'), png('blue')), (cached, rows))
        reverse, count = obj.encode_pair(png('blue'), png('red'))
        self.assertNotEqual(reverse, cached)
        self.assertEqual(video.embedding_rows(reverse, count), 2)

    def test_legacy_ready_rejected_and_child_closed(self):
        obj = Vision({'exe': 'unused', 'mmproj': 'unused', 'model': 'unused',
                      'native_video': True}, lazy=True)
        self.addCleanup(shutil.rmtree, obj.dir, True)
        self.addCleanup(obj.close, True)
        obj.spawn = ([sys.executable, '-u', '-c',
                      'import sys; print("READY 2560",flush=True); sys.stdin.readline()'], None, None)
        with self.assertRaisesRegex(ValueError, 'ENCV-capable'):
            obj.restart()
        self.assertIsNone(obj.proc)

    def test_native_ready_magic_and_width_are_validated(self):
        for ready in ('READYX 2560 video=1 encv=1', 'READY 1 video=1 encv=1',
                      'READY 2560 video=1 encv=0'):
            obj = Vision({'exe': 'unused', 'mmproj': 'unused', 'model': 'unused',
                          'native_video': True}, lazy=True)
            self.addCleanup(shutil.rmtree, obj.dir, True)
            self.addCleanup(obj.close, True)
            code = f'import sys; print({ready!r},flush=True); sys.stdin.readline()'
            obj.spawn = ([sys.executable, '-u', '-c', code], None, None)
            with self.subTest(ready=ready):
                with self.assertRaises((ValueError, RuntimeError)):
                    obj.restart()
                self.assertIsNone(obj.proc)


if __name__ == '__main__':
    unittest.main()

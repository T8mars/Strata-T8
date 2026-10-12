"""Video input regressions: real CPU decode/HTTP, no model or GPU calls."""
import base64
import io
import math
import threading
import unittest
from unittest import mock

from PIL import Image
from serve import test_video as fixtures
from serve import video
from tools import strata_video


class SamplingBoundaries(unittest.TestCase):
    def test_subnormal_rate_has_one_finite_target(self):
        targets = strata_video.sampling_schedule(0, 3, 5e-324, max_frames=1)
        self.assertEqual(targets, [0])
        self.assertTrue(all(math.isfinite(target) for target in targets))

    def test_subnormal_rate_never_retains_more_than_frame_cap(self):
        images = [Image.new('RGB', (32, 32), color) for color in ('red', 'green', 'blue')]
        sampled = strata_video.sample_images(images, fps=1, sampling_fps=5e-324, max_frames=1)
        self.assertEqual(len(sampled.frames), 1)
        self.assertEqual(sampled.frames[0].image.tobytes(), images[0].tobytes())
        strata_video.validate_manifest(sampled.manifest, 1)

    def test_huge_integer_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            strata_video.finite(10**400, 'sampling_fps', positive=True)

    def test_large_schedule_spreads_bounded_targets_without_overflow(self):
        targets = strata_video.sampling_schedule(0, 1e308, 120, max_frames=4)
        self.assertEqual(targets, [0, 2.5e307, 5e307, 7.5e307])
        self.assertTrue(all(math.isfinite(target) for target in targets))

    def test_skinny_image_stays_inside_pixel_budget(self):
        for size in ((16384, 1), (1, 16384), (32768, 2), (2, 32768)):
            with self.subTest(size=size):
                bounded = strata_video.bounded_image(Image.new('RGB', size, 'red'), 1024)
                self.assertLessEqual(bounded.width * bounded.height, 1024)
                self.assertEqual(bounded.mode, 'RGB')

    def test_upload_cancel_after_decode_skips_png_serialization(self):
        cancelled = threading.Event()
        sampled = strata_video.sample_images([Image.new('RGB', (32, 32)) for _ in range(2)],
                                              fps=1, max_frames=2)
        def decode(*args, **kwargs):
            cancelled.set()
            return sampled
        with mock.patch.object(strata_video, 'sample_video', side_effect=decode), \
             mock.patch.object(Image.Image, 'save') as serialize:
            with self.assertRaises(strata_video.VideoCancelled):
                video.sample_upload({'data': base64.b64encode(b'CPU fixture').decode()}, cancel=cancelled)
        serialize.assert_not_called()

    def test_corrupt_native_image_is_a_validation_error(self):
        part = fixtures.part(count=1)
        part['frames'][0]['source'] = 'data:image/png;base64,' + base64.b64encode(b'not a PNG').decode()
        with self.assertRaises(ValueError):
            video.expand_videos([{'role': 'user', 'content': [part]}], fixtures.Vision.load, lambda: None)

    def test_native_skinny_frame_keeps_its_cpu_pixel_budget(self):
        payload = io.BytesIO()
        Image.new('RGB', (1048576, 1), 'red').save(payload, format='PNG')
        part = fixtures.part(count=1)
        part['frames'][0]['source'] = 'data:image/png;base64,' + base64.b64encode(payload.getvalue()).decode()
        planned = video.expand_videos([{'role': 'user', 'content': [part]}], fixtures.Vision.load, lambda: None)
        for data in planned[0]['content'][0]['pairs'][0]['pair']:
            with Image.open(io.BytesIO(data)) as image:
                self.assertLessEqual(image.width * image.height, 589824)


@unittest.skipUnless(fixtures.av is not None, 'PyAV is required for CPU HTTP fixtures')
class VideoInputHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.VideoUploadHTTP.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.VideoUploadHTTP.tearDownClass.__func__(cls)

    def setUp(self):
        fixtures.VideoUploadHTTP.setUp(self)

    def tearDown(self):
        fixtures.VideoUploadHTTP.tearDown(self)

    post = fixtures.VideoUploadHTTP.post

    def test_huge_numeric_input_returns_400_before_decode(self):
        with mock.patch.object(fixtures.av, 'open') as decoder:
            status, result = self.post({'data': self.encoded, 'sampling_fps': 10**400})
        self.assertEqual(status, 400, result)
        self.assertEqual(result['error']['type'], 'invalid_request_error')
        decoder.assert_not_called()

    def test_subnormal_rate_real_upload_respects_cap(self):
        status, result = self.post({'data': self.encoded, 'sampling_fps': 5e-324, 'max_frames': 2})
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result['part']['frames']), 1)
        self.assertEqual(result['part']['frames'][0]['timestamp_s'], 0)
        strata_video.validate_manifest(result['manifest'], 1)

    def test_corrupt_native_image_returns_400_and_server_recovers(self):
        part = fixtures.part(count=1)
        part['frames'][0]['source'] = 'data:image/png;base64,' + base64.b64encode(b'not a PNG').decode()
        self.engine.native_video_build = True
        with mock.patch.object(self.service, 'load'):
            status, result = self.post({'messages': [{'role': 'user', 'content': [part]}], 'max_tokens': 32},
                                       path='/v1/chat/completions')
        self.assertEqual(status, 400, result)
        self.assertEqual(result['error']['type'], 'invalid_request_error')
        self.assertIsNone(getattr(self.service.embeddings, 'path', None))
        status, result = self.post({'data': self.encoded})
        self.assertEqual(status, 200, result)


if __name__ == '__main__':
    unittest.main()

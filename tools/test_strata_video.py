"""Real CPU video decode regressions; no model, GPU, network or FFmpeg CLI."""
from __future__ import annotations

import copy
from fractions import Fraction
import io
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strata_video as V

try:
    import av
    import numpy as np
    from PIL import Image
except ImportError:
    av = np = Image = None


def write_video(path, timestamps, *, origin=0.0, width=96, height=64, rate=8):
    """Lossy H.264 container fixture with explicit presentation timestamps."""
    with av.open(str(path), 'w') as output:
        stream = output.add_stream('libx264', rate=rate)
        stream.width, stream.height, stream.pix_fmt = width, height, 'yuv420p'
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.codec_context.options = {'bf': '0', 'g': '8', 'threads': '1', 'preset': 'ultrafast'}
        for i, timestamp in enumerate(timestamps):
            pixels = np.zeros((height, width, 3), dtype=np.uint8)
            pixels[:, :, i % 3] = 220
            # Asymmetric corner lets the rotation test check more than shape.
            pixels[:height//3, :width//3] = [255, 255, 255]
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts = round((origin + timestamp) * 1000)
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def rotate_mp4(path, rotation=90):
    """Set the real MP4 track display matrix without external tools."""
    data = bytearray(path.read_bytes())
    # tkhd version 0: full-box fields through height include the 3x3 matrix.
    at = data.index(b'tkhd')
    payload = at + 4
    version = data[payload]
    matrix_at = payload + (52 if version == 1 else 40)
    matrices = {
        90: (0, -65536, 0, 65536, 0, 0, 0, 0, 1 << 30),
        180: (-65536, 0, 0, 0, -65536, 0, 0, 0, 1 << 30),
        270: (0, 65536, 0, -65536, 0, 0, 0, 0, 1 << 30),
    }
    data[matrix_at:matrix_at + 36] = struct.pack('>9i', *matrices[rotation])
    path.write_bytes(data)


def write_audio_only(path):
    with av.open(str(path), 'w') as output:
        stream = output.add_stream('aac', rate=8000)
        stream.layout = 'mono'
        for index in range(4):
            frame = av.AudioFrame.from_ndarray(np.zeros((1, 1024), dtype=np.float32),
                                              format='fltp', layout='mono')
            frame.sample_rate = 8000
            frame.pts = index * 1024
            frame.time_base = Fraction(1, 8000)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


@unittest.skipUnless(av is not None, 'PyAV, NumPy and Pillow are required for CPU video fixtures')
class VideoSampling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='strata-video-')
        cls.directory = Path(cls.temp.name)
        cls.cfr = cls.directory / '中文 帧率 固定.mp4'
        write_video(cls.cfr, [i/8 for i in range(24)])
        cls.vfr = cls.directory / 'variable.mkv'
        write_video(cls.vfr, [0, .1, .3, .8, 1.1, 1.9, 2.3, 3.2])
        cls.offset = cls.directory / 'offset.mkv'
        write_video(cls.offset, [i/8 for i in range(24)], origin=10)
        cls.rotated = cls.directory / 'rotated.mp4'
        cls.rotated.write_bytes(cls.cfr.read_bytes())
        rotate_mp4(cls.rotated)
        cls.audio = cls.directory / 'audio-only.m4a'
        write_audio_only(cls.audio)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cfr_exact_pts_and_pixels(self):
        result = V.sample_video(self.cfr, max_frames=8)
        self.assertEqual([f.timestamp_s for f in result.frames], [0, 1, 2])
        self.assertEqual([f.source_timestamp_s for f in result.frames], [0, 1, 2])
        self.assertTrue(all(f.timestamp_kind == 'pts' for f in result.frames))
        self.assertEqual([f.image.size for f in result.frames], [(96, 64)] * 3)
        colors = [max(range(3), key=lambda channel: f.image.getpixel((90, 60))[channel])
                  for f in result.frames]
        self.assertEqual(colors, [0, 2, 1])
        self.assertEqual(result.manifest['coverage']['continuous_observation'], False)
        self.assertTrue(result.manifest['coverage']['sampling_completed'])
        self.assertFalse(result.manifest['coverage']['decode_completed'])
        self.assertEqual(V.validate_manifest(result.manifest, 3), result.manifest)

    def test_vfr_uses_actual_presentation_times(self):
        result = V.sample_video(self.vfr, max_frames=8)
        self.assertEqual([f.source_timestamp_s for f in result.frames], [0, 1.1, 2.3, 3.2])
        self.assertAlmostEqual(result.manifest['coverage']['max_sample_gap_s'], 1.2)
        self.assertNotEqual([f.timestamp_s for f in result.frames], [0, 1, 2, 3])

    def test_trim_preserves_source_clock(self):
        result = V.sample_video(self.cfr, start_s=1.25, end_s=2.75,
                                sampling_fps=2, max_frames=8)
        self.assertEqual([f.timestamp_s for f in result.frames], [0, .5, 1])
        self.assertEqual([f.source_timestamp_s for f in result.frames], [1.25, 1.75, 2.25])
        self.assertEqual(result.manifest['sampling']['requested_targets_s'], [1.25, 1.75, 2.25])
        V.validate_manifest(result.manifest, 3)

    def test_nonzero_stream_origin_and_trim(self):
        result = V.sample_video(self.offset, start_s=1, end_s=2.5,
                                sampling_fps=2, max_frames=8)
        self.assertEqual(result.manifest['source']['time_origin_s'], 10)
        self.assertEqual([f.timestamp_s for f in result.frames], [0, .5, 1])
        self.assertEqual([f.source_timestamp_s for f in result.frames], [11, 11.5, 12])
        V.validate_manifest(result.manifest, 3)

    def test_real_display_matrix_rotation(self):
        plain = V.sample_video(self.cfr, max_frames=1).frames[0].image
        rotated = V.sample_video(self.rotated, max_frames=1).frames[0].image
        self.assertEqual(rotated.size, (64, 96))
        # Decoder reports a counter-clockwise display rotation for this matrix.
        self.assertEqual(rotated.tobytes(), plain.rotate(90, expand=True).tobytes())

    def test_crop_uses_rotated_display_coordinates(self):
        displayed = V.sample_video(self.rotated, max_frames=1).frames[0].image
        actual = V.sample_video(self.rotated, max_frames=1, crop=(8, 12, 32, 40)).frames[0].image
        self.assertEqual(actual.size, (32, 40))
        self.assertEqual(actual.tobytes(), displayed.crop((8, 12, 40, 52)).tobytes())
        # This crop fits the encoded 96x64 shape, but not the displayed 64x96.
        with self.assertRaisesRegex(ValueError, 'displayed frame'):
            V.sample_video(self.rotated, max_frames=1, crop=(64, 0, 16, 16))

    def test_crop_shape_and_coordinates_are_checked(self):
        for crop in ((0, 0, 0, 32), (-1, 0, 16, 16), (0, 0, True, 16),
                     (0, 0, 16.5, 16), (0, 0, 16), '0,0,16,16'):
            with self.subTest(crop=crop), self.assertRaises(ValueError):
                V.sample_video(self.cfr, crop=crop)

    def test_seekable_stream_matches_unicode_file(self):
        expected = V.sample_video(self.cfr, max_frames=2)
        actual = V.sample_video(io.BytesIO(self.cfr.read_bytes()), max_frames=2)
        self.assertEqual(actual.manifest, expected.manifest)
        self.assertEqual([f.image.tobytes() for f in actual.frames],
                         [f.image.tobytes() for f in expected.frames])

    def test_frame_cap_spreads_coverage(self):
        result = V.sample_video(self.cfr, sampling_fps=8, max_frames=3)
        self.assertEqual([f.timestamp_s for f in result.frames], [0, 1, 2])
        self.assertTrue(result.manifest['coverage']['frame_cap_applied'])

    def test_resize_obeys_pixel_budget(self):
        result = V.sample_video(self.cfr, max_frames=1, max_pixels=1024)
        self.assertLessEqual(result.frames[0].image.width * result.frames[0].image.height, 1024)
        self.assertEqual(result.frames[0].image.mode, 'RGB')

    def test_bad_input_and_audio_only_fail(self):
        with self.assertRaises(ValueError):
            V.sample_video(io.BytesIO(b'not a video'))
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(8000)
            writer.writeframes(b'\0' * 16000)
        audio.seek(0)
        with self.assertRaises(ValueError):
            V.sample_video(audio)
        # An allowed MOV-family container with actual audio-only AAC content
        # reaches the stream check, rather than only the format whitelist.
        with self.assertRaisesRegex(ValueError, 'no video stream'):
            V.sample_video(self.audio)

    def test_url_and_oversize_inputs_rejected(self):
        for source in ('https://example.invalid/movie.mp4', self.directory):
            with self.subTest(source=str(source)), self.assertRaises(ValueError):
                V.sample_video(source)
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            V.sample_video(self.cfr, max_input_bytes=1)
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            V.sample_video(io.BytesIO(self.cfr.read_bytes()), max_input_bytes=1)

    def test_limits_rejected_before_decode(self):
        for options in ({'sampling_fps': 121}, {'max_frames': 257}, {'max_pixels': 0},
                        {'max_frames': 256, 'max_pixels': 1_048_576},
                        {'end_s': 2, 'start_s': 2}, {'timeout_s': float('nan')},
                        {'max_frames': True}, {'max_decoded_frames': 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                V.sample_video(self.cfr, **options)

    def test_missing_optional_decoder_has_an_actionable_error(self):
        with patch.dict(sys.modules, {'av': None}):
            with self.assertRaisesRegex(ValueError, 'install av==15.0.0'):
                V.sample_video(self.cfr)

    def test_window_and_decode_limits(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            V.sample_video(self.cfr, start_s=5)
        with self.assertRaisesRegex(ValueError, 'duration'):
            V.sample_video(self.cfr, max_duration_s=1)
        with self.assertRaisesRegex(ValueError, 'decoded frame'):
            V.sample_video(self.cfr, max_decoded_frames=2)

    def test_cancellation_before_and_during_decode(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(V.VideoCancelled):
            V.sample_video(self.cfr, cancel=stop)
        calls = []
        def cancel():
            calls.append(1)
            return len(calls) >= 4
        with self.assertRaises(V.VideoCancelled):
            V.sample_video(self.cfr, cancel=cancel)
        self.assertEqual(len(calls), 4)
        # Cancellation does not poison a subsequent real decode.
        self.assertEqual(len(V.sample_video(self.cfr).frames), 3)

    def test_timeout_is_checked_during_decode(self):
        with patch.object(V.time, 'monotonic', side_effect=[0, 2]):
            with self.assertRaises(TimeoutError):
                V.sample_video(self.cfr, timeout_s=1)


@unittest.skipUnless(Image is not None, 'Pillow is required for image adapters')
class ImageSampling(unittest.TestCase):
    def images(self, count):
        return [Image.new('RGB', (32, 32), (i, 0, 0)) for i in range(count)]

    def test_tensor_adapter_preselected_keeps_full_duration_and_indices(self):
        result = V.sample_images(self.images(3), timestamps=[2, 4, 6],
                                 source_indices=[20, 40, 60], source_duration_s=10,
                                 time_origin_s=100, preselected=True, start_s=2,
                                 end_s=8, max_frames=3)
        self.assertEqual(result.manifest['source']['duration_s'], 10)
        self.assertEqual([f.timestamp_s for f in result.frames], [0, 2, 4])
        self.assertEqual([f.source_timestamp_s for f in result.frames], [102, 104, 106])
        self.assertEqual([f.source_index for f in result.frames], [20, 40, 60])
        V.validate_manifest(result.manifest, 3)

    def test_preselected_metadata_is_required_and_bounded(self):
        for options in ({'timestamps': [0]}, {'source_duration_s': 1},
                        {'timestamps': [0, 1], 'source_duration_s': 2},
                        {'timestamps': [0], 'source_duration_s': 2}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                V.sample_images(self.images(2), preselected=True, max_frames=1, **options)

    def test_images_are_converted_only_when_selected(self):
        class BadImage:
            pass
        result = V.sample_images([Image.new('RGB', (32, 32)), BadImage(),
                                  Image.new('RGB', (32, 32)), BadImage()],
                                 fps=2, sampling_fps=1, max_frames=4)
        self.assertEqual([f.source_index for f in result.frames], [0, 2])

    def test_declared_timestamp_count_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'counts'):
            V.sample_images(self.images(2), timestamps=[0], source_duration_s=2,
                            preselected=True, max_frames=4)

    def test_source_indices_validate_count_and_values(self):
        for indices in ([0], [0, -1], [0, True], [0, 1.5], [0, 0]):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                V.sample_images(self.images(2), timestamps=[0, 1],
                                source_duration_s=2, source_indices=indices,
                                preselected=True, max_frames=4)

    def test_estimated_clock_is_disclosed(self):
        result = V.sample_images(self.images(8), fps=4, max_frames=4)
        self.assertEqual([f.timestamp_s for f in result.frames], [0, 1])
        self.assertEqual(result.manifest['coverage']['timestamp_kind'], 'estimated')
        self.assertEqual(result.manifest['coverage']['continuous_observation'], False)

    def test_invalid_timestamps_and_budget(self):
        for stamps in ([0, 0], [1, 0], [0, float('nan')], [0, True], [-1, 0]):
            with self.subTest(stamps=stamps), self.assertRaises(ValueError):
                V.sample_images(self.images(2), timestamps=stamps, source_duration_s=2)
        with self.assertRaises(ValueError):
            V.bounded_image(Image.new('RGB', (4097, 4097)), 1024)


@unittest.skipUnless(Image is not None, 'Pillow is required for manifest fixtures')
class ManifestValidation(unittest.TestCase):
    def manifest(self):
        return V.sample_images([Image.new('RGB', (32, 32)) for _ in range(2)],
                               timestamps=[0, 1], source_duration_s=2,
                               preselected=True, max_frames=4).manifest

    def test_clone_count_and_contract(self):
        manifest = self.manifest()
        actual = V.validate_manifest(manifest, 2)
        actual['frames'][0]['timestamp_s'] = 99
        self.assertEqual(manifest['frames'][0]['timestamp_s'], 0)
        with self.assertRaises(ValueError):
            V.validate_manifest(manifest, 3)
        manifest['contract_version'] = 2
        with self.assertRaises(ValueError):
            V.validate_manifest(manifest)

    def test_inconsistent_times_and_frame_indices(self):
        base = self.manifest()
        for field, value in (('timestamp_s', .5), ('source_timestamp_s', float('nan')),
                             ('source_timestamp_s', 0), ('index', 0),
                             ('timestamp_s', 2)):
            bad = copy.deepcopy(base)
            bad['frames'][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                V.validate_manifest(bad)

    def test_sampling_window_cannot_extend_beyond_source(self):
        manifest = self.manifest()
        manifest['sampling']['end_s'] = 3
        with self.assertRaises(ValueError):
            V.validate_manifest(manifest)

    def test_untrusted_manifest_cannot_change_sampling_contract_or_budgets(self):
        for field, value in (('contract_version', True), ('sampling_fps', True),
                             ('sampling_fps', 0), ('sampling_fps', 121),
                             ('max_frames', 1), ('max_pixels', 1023)):
            bad = self.manifest()
            target = bad if field == 'contract_version' else bad['sampling']
            target[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                V.validate_manifest(bad)
        bad = self.manifest()
        bad['frames'][1]['index'] = V.MAX_FRAMES
        with self.assertRaises(ValueError):
            V.validate_manifest(bad)


if __name__ == '__main__':
    unittest.main()

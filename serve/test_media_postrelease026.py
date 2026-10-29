"""Recovery identity and late cancellation checks without real inference models."""
import io
from pathlib import Path
import threading
import unittest
from unittest import mock

from serve import test_asr_lifecycle as speech_fixture
from serve import test_media_jobs as media_fixture
from serve import test_video_recovery_026 as recovery_fixture
from serve import media_jobs


class VisionRecoveryIdentity(unittest.TestCase):
    setUp = recovery_fixture.RecoveryContext.setUp
    signature = recovery_fixture.RecoveryContext.signature
    write = recovery_fixture.RecoveryContext.write

    def test_visual_encoder_and_projector_replacement_invalidates_checkpoint(self):
        from types import SimpleNamespace
        executable = self.write('vision/strata-vision.exe', b'encoder')
        projector = self.write('vision/mmproj.gguf', b'projector')
        model = self.write('vision/tokenizer.gguf', b'model')
        self.service.vision = SimpleNamespace(
            spawn=([str(executable), '--mmproj', str(projector), '--model', str(model)], None, None),
            dir=self.root)
        for path in (executable, projector, model):
            with self.subTest(asset=path.name):
                before = self.signature()
                path.write_bytes(path.read_bytes() + b'changed')
                original = Path.read_bytes
                def read(candidate):
                    self.assertNotEqual(candidate, path, 'recovery must not hash large vision weights')
                    return original(candidate)
                with mock.patch.object(Path, 'read_bytes', read):
                    self.assertNotEqual(before, self.signature())

    def test_visual_reader_arguments_change_but_load_state_does_not(self):
        from types import SimpleNamespace
        self.service.vision = SimpleNamespace(
            spawn=(['reader', '--max-tokens', '300'], None, None), dir=self.root)
        before = self.signature()
        self.service.vision.native_video = True
        self.service.vision.stopped = False
        self.assertEqual(before, self.signature())
        self.service.vision.spawn[0][-1] = '1024'
        self.assertNotEqual(before, self.signature())

    def test_automatic_fallback_keeps_identity_but_replaced_reader_does_not(self):
        from serve.server import Vision
        executable = self.write('vision/gpu-reader.exe', b'gpu')
        fallback = self.write('vision/cpu-reader.exe', b'cpu')
        projector = self.write('vision/mmproj.gguf', b'projector')
        model = self.write('vision/tokenizer.gguf', b'model')
        arguments = lambda cfg: [str(executable if cfg.get('gpu') else fallback),
                                '--mmproj', str(projector), '--model', str(model)]
        with mock.patch('serve.server.vision_reader.cpu_fallback', return_value={'gpu':False}), \
                mock.patch.object(Vision, 'reader_args', side_effect=arguments), \
                mock.patch.object(Vision, 'work_dir', return_value=self.root):
            self.service.vision = Vision({'gpu':True}, lazy=True)
        before = self.signature()
        self.service.vision.spawn, self.service.vision.fallback = self.service.vision.fallback, None
        self.assertEqual(before, self.signature())
        for path in (executable, fallback, projector, model):
            with self.subTest(asset=path.name):
                before = self.signature()
                path.write_bytes(path.read_bytes() + b'changed')
                self.assertNotEqual(before, self.signature())


class SpeechLateCancellation(unittest.TestCase):
    setUp = speech_fixture.OwnedWorker.setUp
    write_worker = speech_fixture.OwnedWorker.write_worker
    complete_worker = speech_fixture.OwnedWorker.complete_worker

    def test_cancel_at_empty_audio_return_does_not_report_completed(self):
        for status in ('no_track', 'silence'):
            with self.subTest(status=status):
                cancel = threading.Event()
                def extracted(*args, **kwargs):
                    cancel.set()
                    return {'status':status, 'pcm':None, 'window_start_s':0, 'window_end_s':2}
                with mock.patch('serve.media_jobs.extract_audio', side_effect=extracted):
                    with self.assertRaises(InterruptedError):
                        self.manager.transcribe(io.BytesIO(b'fixture'), cancel=cancel)
                self.assertEqual(self.manager.progress()['phase'], 'cancelled')

    def test_cancel_during_cached_transcript_read_does_not_report_completed(self):
        self.complete_worker()
        self.manager.transcribe(io.BytesIO(b'fixture'))
        cancel = threading.Event()
        original = Path.read_text
        def read(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path.parent.resolve() == (self.root/'.asr-cache').resolve():
                cancel.set()
            return value
        with mock.patch.object(Path, 'read_text', read), \
                mock.patch('subprocess.Popen', side_effect=AssertionError('no new worker on a cancelled cache hit')):
            with self.assertRaises(InterruptedError):
                self.manager.transcribe(io.BytesIO(b'fixture'), cancel=cancel)
        self.assertEqual(self.manager.progress()['phase'], 'cancelled')
        self.assertTrue(cancel.is_set())


class DecodeLateCancellation(unittest.TestCase):
    setUp = media_fixture.IsolatedMedia.setUp

    def test_cancel_during_video_result_validation_does_not_return_frames(self):
        from tools import strata_video
        cancel = threading.Event()
        validate = strata_video.validate_manifest
        def checked(*args, **kwargs):
            result = validate(*args, **kwargs)
            cancel.set()
            return result
        with mock.patch.object(strata_video, 'validate_manifest', side_effect=checked):
            with self.assertRaises(InterruptedError):
                media_jobs.sample_video(self.data, self.options, cancel)

    def test_cancel_during_audio_result_read_does_not_return_pcm(self):
        from serve.test_asr import AudioClock
        cancel = threading.Event()
        read = media_jobs._audio_result
        def checked(*args, **kwargs):
            result = read(*args, **kwargs)
            cancel.set()
            return result
        with mock.patch.object(media_jobs, '_audio_result', side_effect=checked):
            with self.assertRaises(InterruptedError):
                media_jobs.extract_audio(AudioClock().wav(), start_s=0, end_s=1, cancel=cancel)


if __name__ == '__main__':
    unittest.main()

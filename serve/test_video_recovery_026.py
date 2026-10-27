"""Recovery and late-cancel regressions; no models, downloads or GPU use."""
import base64
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from serve.asr import config
from serve.video_tasks import VideoStore, runtime_context


class RecoveryContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = SimpleNamespace(engine=SimpleNamespace(spawn=()), model='fixture')

    def signature(self):
        return runtime_context(self.service, self.root)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_all_speech_and_fusion_sources_invalidate_saved_evidence(self):
        for name in ('serve/asr/worker.py', 'serve/asr/confucius/segmented.py',
                     'serve/web/speech-analysis.js', 'serve/video_tasks.py'):
            with self.subTest(name=name):
                path = self.write(name, b'before')
                before = self.signature()
                path.write_bytes(b'after edit')
                self.assertNotEqual(before, self.signature())

    def test_selected_asr_assets_invalidate_recovery_without_reading_weights(self):
        directory = self.root / 'speech-models'
        self.write('asr-settings.json', json.dumps({'model_dir':str(directory)}).encode())
        names = ['runtime/asr/native-cpu/lib/llama.dll',
                 'runtime/asr/native-cpu/python/qwen3asr_native.pyd',
                 'speech-models/'+config.MODELS['Q4_K_M'][0],
                 'speech-models/'+config.PROJECTOR[0],
                 'speech-models/FireRedVAD-ONNX/fireredvad_stream_vad_with_cache.onnx']
        for name in names:
            with self.subTest(name=name):
                path = self.write(name, b'before')
                before = self.signature()
                path.write_bytes(b'after replacement')
                original = Path.read_bytes
                def read(candidate):
                    self.assertNotEqual(candidate, path, 'fingerprinting must not hash model weights')
                    return original(candidate)
                with mock.patch.object(Path, 'read_bytes', read):
                    self.assertNotEqual(before, self.signature())

    def test_invalid_optional_asr_profile_does_not_block_visual_only_tasks(self):
        self.write('asr-settings.json', b'{broken json')
        before = self.signature()
        self.write('asr-settings.json', b'{}')
        self.assertNotEqual(before, self.signature())

    def test_active_loading_state_does_not_invalidate_recovery(self):
        before = self.signature()
        self.service.engine.loaded = True
        self.service.engine.busy = True
        self.assertEqual(before, self.signature())


class UploadCommit(unittest.TestCase):
    def test_cancel_during_last_write_does_not_commit_an_orphan_source(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VideoStore(directory)
            cancel = threading.Event()
            fsync = os.fsync
            def finish_write(handle):
                fsync(handle)
                cancel.set()
            with mock.patch('serve.video_tasks.os.fsync', side_effect=finish_write):
                with self.assertRaisesRegex(InterruptedError, 'cancelled'):
                    store.upload('a'*64, base64.b64encode(b'fixture').decode(), cancel=cancel)
            self.assertEqual(list(store.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

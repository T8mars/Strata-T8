"""ASR paths and recovery must not depend on the caller's working directory."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from serve.asr import config
from serve.asr.manager import AsrManager
from serve.video_tasks import runtime_context


class RelativeAssets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base/'app'
        self.root.mkdir()
        self.other = self.base/'other'
        self.other.mkdir()
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.other)
        self.settings = {**config.DEFAULTS, 'model_dir':'models/speech', 'vad_dir':'models/vad'}
        config.save(self.root, self.settings)
        self.assets = [('speech.gguf',b'model'), ('projector.gguf',b'projector')]
        self.vad = [('vad.onnx',b'vad')]
        def entry(name, value):
            return name, len(value), hashlib.sha256(value).hexdigest()
        for directory, assets in [('speech',self.assets), ('vad',self.vad)]:
            (self.root/'models'/directory).mkdir(parents=True)
            for name, value in assets:
                (self.root/'models'/directory/name).write_bytes(value)
        patches = [mock.patch.dict(config.MODELS, {'Q4_K_M':entry(*self.assets[0])}),
                   mock.patch.object(config,'PROJECTOR',entry(*self.assets[1])),
                   mock.patch('tools.prepare_asr_models.VAD',[entry(*self.vad[0])])]
        for patch in patches:
            patch.start(); self.addCleanup(patch.stop)

    def test_model_hashing_resolves_relative_paths_from_application(self):
        result = AsrManager(self.root)._signature(self.settings)
        self.assertEqual(set(result), {'speech.gguf','projector.gguf','vad/vad.onnx'})
        self.assertEqual(config.load(self.root)['model_dir'], 'models/speech')

    def test_relative_vad_default_and_override_are_application_relative(self):
        self.assertEqual(config.vad_path(self.root,self.settings), self.root/'models/vad')
        self.assertEqual(config.vad_path(self.root,{**self.settings,'vad_dir':''}),
                         self.root/'models/speech/FireRedVAD-ONNX')

    def test_availability_uses_same_models_as_worker(self):
        result = config.availability(self.root, self.settings)
        self.assertNotIn('speech.gguf',result['missing'])
        self.assertNotIn('projector.gguf',result['missing'])

    def test_optional_unreadable_asset_does_not_break_visual_capabilities(self):
        svc = SimpleNamespace(engine=SimpleNamespace(spawn=()), model='fixture')
        original = Path.stat
        target = self.root/'models/speech/speech.gguf'
        def denied(path, *args, **kwargs):
            if path == target:
                raise PermissionError('fixture disconnected speech drive')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path,'stat',denied):
            first = runtime_context(svc, self.root)
            self.assertEqual(first, runtime_context(svc, self.root))
        self.assertNotEqual(first, runtime_context(svc, self.root))


if __name__ == '__main__':
    unittest.main()

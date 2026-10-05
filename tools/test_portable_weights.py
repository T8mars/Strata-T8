"""Only the fixed vision encoder may enter the vision edition; old packages stay strict."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from portable_weights import VISION_PATH, VISION_SIZE, VISION_SHA256
from portable_update import validate_manifest
from portable_version import archive_name


class WeightPolicy(unittest.TestCase):
    def manifest(self):
        return {'version': '0.1.39-t8.5', 'edition': 'VisionReady-NoMainModel', 'models_included': True,
                'weights': {'main': False, 'mtp': False, 'vision': True},
                'files': [{'path': VISION_PATH, 'size': VISION_SIZE, 'sha256': VISION_SHA256}]}

    def test_exact_vision_role_is_allowed(self):
        validate_manifest(Path.cwd(), self.manifest(), verify=False)

    def test_wrong_hash_missing_file_wrong_roles_and_false_no_models_rejected(self):
        changes = [lambda m: m['files'][0].update(sha256='0'*64),
                   lambda m: m['files'].clear(), lambda m: m['weights'].update(main=True),
                   lambda m: m.update(models_included=False), lambda m: m.update(edition='Portable-NoModels')]
        for change in changes:
            manifest = self.manifest()
            change(manifest)
            with self.assertRaises(ValueError):
                validate_manifest(Path.cwd(), manifest, verify=False)

    def test_unrelated_model_is_rejected_even_with_correct_encoder(self):
        manifest = self.manifest()
        manifest['files'].append({'path': 'accidental.gguf', 'size': 3, 'sha256': '0'*64})
        with self.assertRaisesRegex(ValueError, 'Model in release'):
            validate_manifest(Path.cwd(), manifest, verify=False)

    def test_legacy_and_vision_asset_names_are_distinct(self):
        self.assertIn('Portable-NoModels', archive_name('0.1.39-t8.5'))
        self.assertIn('VisionReady-NoMainModel', archive_name('0.1.39-t8.5', 'VisionReady-NoMainModel'))
        with self.assertRaises(ValueError):
            archive_name('0.1.39-t8.5', 'any-model')


if __name__ == '__main__':
    unittest.main()

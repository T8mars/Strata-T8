"""Regressions found while auditing shipping files, manifests and resumed downloads."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import build_portable, portable_download, verify_portable
from tools.test_portable_update import package


class PortableAudit(unittest.TestCase):
    def test_source_packaging_excludes_untracked_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'source'
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root/'serve').mkdir()
            (root/'serve/server.py').write_text('public source')
            (root/'serve/private.json').write_text('private credentials')
            subprocess.run(['git', 'add', 'serve/server.py'], cwd=root, check=True)
            target = Path(directory)/'package'
            with mock.patch.object(build_portable, 'ROOT', root), mock.patch.object(build_portable, 'TARGET', target):
                build_portable.copy_source('serve')
            self.assertEqual((target/'serve/server.py').read_text(), 'public source')
            self.assertFalse((target/'serve/private.json').exists())

    def test_installed_verifier_allows_user_data_but_rejects_ambiguous_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = package(root, {'app.py': 'release'})
            (root/'portable-settings.json').write_text('user configuration')
            self.assertEqual(verify_portable.verify(root), 0)
            manifest['files'].append(dict(manifest['files'][0], path='APP.py'))
            (root/'PACKAGE-MANIFEST.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                verify_portable.verify(root)

    def test_no_models_manifest_cannot_declare_main_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = package(root, {'app.py': 'release'})
            manifest['weights'] = {'main': True, 'mtp': False, 'vision': False}
            (root/'PACKAGE-MANIFEST.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'weight roles'):
                verify_portable.verify(root)

    def test_corrupt_verification_stamp_rechecks_existing_file_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'weight.gguf'
            target.write_bytes(b'verified model')
            target.with_name(target.name+'.verified.json').write_text('{truncated')
            with mock.patch('urllib.request.urlopen', side_effect=AssertionError('network')):
                portable_download.download('https://unused', target, target.stat().st_size, hashlib.sha256(target.read_bytes()).hexdigest())

    def test_corrupt_or_invalid_range_state_restarts_and_verifies(self):
        payload = b'range data'
        digest = hashlib.sha256(payload).hexdigest()
        states = ['{truncated', '[]', json.dumps({'size': len(payload), 'sha256': digest, 'chunk': 1048576, 'complete': [999]})]
        for state in states:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)/'weight.gguf'
                target.with_name(target.name+'.part').write_bytes(b'partial')
                target.with_name(target.name+'.ranges.json').write_text(state)
                response = io.BytesIO(payload)
                response.status = 206
                response.headers = {'Content-Range': f'bytes 0-{len(payload)-1}/{len(payload)}'}
                with mock.patch('urllib.request.urlopen', return_value=response):
                    portable_download.download('https://unused', target, len(payload), digest, chunk_mib=1)
                self.assertEqual(target.read_bytes(), payload)

    def test_download_rejects_invalid_catalog_before_creating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'sub/file'
            for size, digest in ((0, '0'*64), (-1, '0'*64), (True, '0'*64), (4, 'broken')):
                with self.assertRaises(ValueError):
                    portable_download.download('https://unused', target, size, digest)
            self.assertFalse(target.parent.exists())


if __name__ == '__main__':
    unittest.main()

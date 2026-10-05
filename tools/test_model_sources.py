"""Domestic source priority and reusing already verified model files."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tools.portable_download import download
from tools.prepare_portable_model import sources


class ModelSources(unittest.TestCase):
    def test_domestic_first_and_pinned_fallbacks(self):
        urls = sources({'repository': 'author/model', 'official_revision': 'hf-commit', 'modelscope_revision': 'ms-commit'}, {'file': 'shard.gguf'})
        self.assertTrue(urls[0].startswith('https://modelscope.cn/'))
        self.assertIn('Revision=ms-commit', urls[0])
        self.assertTrue(urls[1].startswith('https://hf-mirror.com/'))
        self.assertIn('/resolve/hf-commit/', urls[2])
    def test_existing_GGUF_hash_verified_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'model.gguf'
            file.write_bytes(b'already downloaded')
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            with mock.patch('urllib.request.urlopen', side_effect=AssertionError('unexpected network')):
                download('https://unused', file, file.stat().st_size, digest)
            self.assertTrue(file.with_name(file.name+'.verified.json').exists())


if __name__ == '__main__': unittest.main()

"""A shard mapping must not keep the discarded Python file handle open."""
import builtins
import gc
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import warnings

from tools import strata_pack


class ShardResources(unittest.TestCase):
    def capture_file(self, *args, **kwargs):
        handle = builtins.open(*args, **kwargs)
        self.handles.append(handle)
        return handle

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'shard.gguf'
        self.path.write_bytes(b'mapped shard bytes')
        self.handles = []

    def tearDown(self):
        for handle in self.handles:
            handle.close()
        self.tmp.cleanup()

    def test_actual_file_closes_while_read_only_mapping_remains_usable(self):
        with mock.patch('canonical_xcheck.open', side_effect=self.capture_file, create=True):
            mapping, size = strata_pack.open_shard(self.path)
        try:
            self.assertEqual(len(self.handles), 1)
            self.assertTrue(self.handles[0].closed, 'discarded file descriptor must close immediately')
            self.assertEqual(size, len(b'mapped shard bytes'))
            self.assertEqual(mapping[:], b'mapped shard bytes')
            with self.assertRaises(TypeError):
                mapping[0] = ord('x')
        finally:
            mapping.close()

    def test_failed_mapping_construction_closes_actual_file(self):
        self.path.write_bytes(b'')
        with mock.patch('canonical_xcheck.open', side_effect=self.capture_file, create=True), self.assertRaises(ValueError):
            strata_pack.open_shard(self.path)
        self.assertTrue(self.handles[0].closed)

    def test_repeated_mappings_emit_no_unclosed_file_warning(self):
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter('always', ResourceWarning)
            for _ in range(20):
                mapping, _ = strata_pack.open_shard(self.path)
                mapping.close()
            gc.collect()
        self.assertEqual([warning for warning in emitted if issubclass(warning.category, ResourceWarning)], [])


if __name__ == '__main__':
    unittest.main()

"""Old updaters leave empty dist-info directories after a dependency upgrade."""
import importlib.metadata
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import portable


class RuntimeMetadata(unittest.TestCase):
    def test_empty_old_metadata_does_not_shadow_the_new_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site/'pillow-12.3.0.dist-info/licenses').mkdir(parents=True)
            current = site/'pillow-13.0.0.dist-info'
            current.mkdir()
            (current/'METADATA').write_text('Name: pillow\nVersion: 13.0.0\n', encoding='utf-8')
            with mock.patch.object(sys, 'path', [str(site), *sys.path]):
                self.assertIsNone(importlib.metadata.version('pillow'))
                self.assertEqual(portable.runtime_dependency_version('pillow'), '13.0.0')

    def test_valid_old_metadata_is_still_reported_for_the_pin_check(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            for version in ('12.3.0', '13.0.0'):
                info = site/f'pillow-{version}.dist-info'
                info.mkdir()
                (info/'METADATA').write_text(f'Name: pillow\nVersion: {version}\n', encoding='utf-8')
            with mock.patch.object(sys, 'path', [str(site), *sys.path]):
                self.assertEqual(portable.runtime_dependency_version('pillow'), '12.3.0')

    def test_missing_metadata_reports_the_dependency_instead_of_a_none_type_error(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'t8_missing_dependency-1.0.dist-info').mkdir()
            with mock.patch.object(sys, 'path', [directory, *sys.path]):
                with self.assertRaisesRegex(RuntimeError, 't8-missing-dependency'):
                    portable.runtime_dependency_version('t8-missing-dependency')

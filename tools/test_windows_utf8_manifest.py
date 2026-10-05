from pathlib import Path
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
from tools.windows_utf8_manifest import (ACTIVE, ASM1, ASM3, WIN2019, patch_executable,
                                        patch_engine, read_manifests, signed_pe, utf8_manifest, write_manifests)


def unsigned_fixture(target):
    # The Python distribution is signed. Remove its certificate directory only
    # from this disposable resource-test fixture, never from packaged binaries.
    data = bytearray(Path(sys.executable).read_bytes())
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    optional = pe + 24
    directory = {0x10b: 96, 0x20b: 112}[struct.unpack_from('<H', data, optional)[0]]
    struct.pack_into('<II', data, optional + directory + 4 * 8, 0, 0)
    target.write_bytes(data)
    manifests = read_manifests(target)
    legacy = {}
    for lang, data in manifests.items():
        root = ET.fromstring(utf8_manifest(data))
        root.find('.//' + ACTIVE).text = 'Legacy'
        legacy[lang] = ET.tostring(root, encoding='utf-8')
    if legacy:
        write_manifests(target, legacy)


class ManifestXML(unittest.TestCase):
    def test_preserves_vendor_uac_and_dependencies(self):
        original = f'''<assembly xmlns="{ASM1}" manifestVersion="1.0">
        <dependency><dependentAssembly><assemblyIdentity name="vendor.runtime"/></dependentAssembly></dependency>
        <trustInfo xmlns="{ASM3}"><security><requestedPrivileges><requestedExecutionLevel level="asInvoker"/></requestedPrivileges></security></trustInfo>
        </assembly>'''.encode()
        patched = utf8_manifest(original)
        root = ET.fromstring(patched)
        self.assertEqual(root.find('.//{' + ASM1 + '}assemblyIdentity').get('name'), 'vendor.runtime')
        self.assertEqual(root.find('.//{' + ASM3 + '}requestedExecutionLevel').get('level'), 'asInvoker')
        self.assertEqual(root.find('.//' + ACTIVE).text, 'UTF-8')
        self.assertEqual(utf8_manifest(patched), patched)

    def test_replaces_existing_codepage(self):
        original = f'<assembly xmlns="{ASM1}"><application xmlns="{ASM3}"><windowsSettings><activeCodePage xmlns="{WIN2019}">Legacy</activeCodePage></windowsSettings></application></assembly>'.encode()
        self.assertEqual(ET.fromstring(utf8_manifest(original)).find('.//' + ACTIVE).text, 'UTF-8')

    def test_rejects_ambiguous_codepages(self):
        original = f'<assembly><activeCodePage xmlns="{WIN2019}"/><activeCodePage xmlns="{WIN2019}"/></assembly>'.encode()
        with self.assertRaises(ValueError):
            utf8_manifest(original)


@unittest.skipUnless(os.name == 'nt', 'Win32 resource API')
class ManifestExecutable(unittest.TestCase):
    def test_actual_resource_update_at_unicode_path_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix='manifest-中文 空格-') as directory:
            helper = Path(directory)/'helper.exe'
            unsigned_fixture(helper)
            original = read_manifests(helper)
            result = patch_executable(helper)
            patched = read_manifests(helper)
            self.assertEqual(set(patched), set(original) if original else {0})
            for lang, data in patched.items():
                self.assertEqual(data, utf8_manifest(original[lang]) if original else utf8_manifest(f'<assembly xmlns="{ASM1}" manifestVersion="1.0"/>'.encode()))
            self.assertEqual(patch_executable(helper)['sha256_after'], result['sha256_after'])

    def test_engine_records_original_hash_and_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory)
            unsigned_fixture(engine/'strata-vision.exe')
            (engine/'BUILD.json').write_text(json.dumps({'version': 'test', 'source': 'release'}))
            first = patch_engine(engine)
            self.assertEqual(patch_engine(engine), first)
            build = json.loads((engine/'BUILD.json').read_text())
            self.assertEqual(build['source'], 'release')
            self.assertEqual(build['portable_patches'], first)

    def test_signed_binary_is_rejected_without_modification(self):
        if not signed_pe(sys.executable):
            self.skipTest('Test runtime is unsigned')
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory)/'signed.exe'
            shutil.copy2(sys.executable, helper)
            original = helper.read_bytes()
            manifests = read_manifests(helper)
            if manifests and all(utf8_manifest(data) == data for data in manifests.values()):
                result = patch_executable(helper)
                self.assertEqual(result['sha256_before'], result['sha256_after'])
            else:
                with self.assertRaisesRegex(ValueError, 'signed native executable'):
                    patch_executable(helper)
            self.assertEqual(helper.read_bytes(), original)

    def test_required_patch_refuses_signature_before_resource_write(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory)/'signed-fixture.exe'
            unsigned_fixture(helper)
            original = helper.read_bytes()
            with mock.patch('tools.windows_utf8_manifest.signed_pe', return_value=True), \
                 mock.patch('tools.windows_utf8_manifest.write_manifests') as writer:
                with self.assertRaisesRegex(ValueError, 'signed native executable'):
                    patch_executable(helper)
                writer.assert_not_called()
            self.assertEqual(helper.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()

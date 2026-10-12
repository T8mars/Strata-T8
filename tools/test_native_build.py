"""CPU tests for stale native artifacts, architecture scope and atomic installation."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_native_portable as b


class NativeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, data in {
            'setup.py': 'LLAMA_CPP_COMMIT = "'+'a'*40+'"\n',
            'CMakeLists.txt': 'project(fixture)\n', 'src/program/generate.cpp': 'native v1\n',
            'include/strata/native.hpp': 'native contract\n', 'tools/vision/strata_vision.cpp': 'vision v1\n',
            'tools/build_native_portable.py': 'build fixture\n', 'tools/windows_utf8_manifest.py': 'patch fixture\n',
            'third_party/llama.cpp/CMakeLists.txt': 'project(llama)\n',
            'third_party/llama.cpp/src/llama.cpp': 'pinned llama\n',
        }.items():
            path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data)
        third = {'repository': 'ggml-org/llama.cpp', 'commit': 'a'*40, 'archive_sha256': 'b'*64,
                 'source_tree_sha256': b.canonical_digest(b.llama_inputs(self.root)), 'source_file_count': 2}
        self.native = {'schema': 1, 'source_commit': 'c'*40, 'portable': True,
            'cuda_targets':list(b.CUDA_TARGETS), 'fatbinary_compression':'size',
            'cuda_architectures': list(b.ARCHITECTURES), 'source_identity': b.source_identity(self.root, third),
            'compiler': {'cuda':'Cuda compilation tools, release 13.0, V13.0.88'},
            'capabilities': {'native_video': True, 'geniv': 1, 'encv': 1, 'sve': 1}, 'outputs': {}}
        self.engine = self.root/'engine'; self.engine.mkdir()
        self.vendor = {'version': '0.1.42', 'upstream_asset': {'repository': 'Niko1221/Strata',
            'release_tag': 'v0.1.42', 'name': 'strata-windows-x64.zip', 'sha256': 'd'*64, 'upstream_commit': 'e'*40}}
        (self.engine/'BUILD.json').write_text(json.dumps(self.vendor))
        (self.engine/'vendor.dll').write_bytes(b'vendor DLL remains')
        self.artifacts = {}
        for name in ['strata.exe', 'strata-vision.exe']:
            artifact = self.root/'artifacts'/name; artifact.parent.mkdir(exist_ok=True); artifact.write_bytes(b'new '+name.encode())
            self.artifacts[name] = artifact
            (self.engine/name).write_bytes(b'old '+name.encode())
            self.native['outputs'][name] = {'sha256': b.digest(artifact), 'size': artifact.stat().st_size,
                'ptx_architectures':['120a'] if name=='strata-vision.exe' else ['120'],
                'cuda_architectures': list(b.ARCHITECTURES), 'utf8_manifest': 'activeCodePage=UTF-8'}

    def test_current_source_and_hashes(self):
        self.assertIs(b.validate_native_build(self.native, self.root), self.native)

    def test_native_source_change_blocks_release(self):
        (self.root/'src/program/generate.cpp').write_text('new implementation')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            b.validate_native_build(self.native, self.root)

    def test_header_added_blocks_release(self):
        (self.root/'include/new.hpp').write_text('new input')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            b.validate_native_build(self.native, self.root)

    def test_inline_cpu_kernel_changed_blocks_release(self):
        (self.root/'src/cpu_rows.inl').write_text('included CPU kernel implementation')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            b.validate_native_build(self.native, self.root)

    def test_parent_server_edits_do_not_invalidate_native(self):
        (self.root/'serve').mkdir(); (self.root/'serve/server.py').write_text('Python change')
        b.validate_native_build(self.native, self.root)

    def test_third_party_change_blocks_release(self):
        (self.root/'third_party/llama.cpp/src/llama.cpp').write_text('modified')
        with self.assertRaisesRegex(ValueError, 'third-party source changed'):
            b.validate_native_build(self.native, self.root)

    def test_pin_change_blocks_release(self):
        (self.root/'setup.py').write_text('LLAMA_CPP_COMMIT = "'+'f'*40+'"')
        with self.assertRaisesRegex(ValueError, 'pin differs'):
            b.validate_native_build(self.native, self.root)

    def test_manifest_tampering_blocks_release(self):
        self.native['source_identity']['inputs'][0]['sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'manifest checksum'):
            b.validate_native_build(self.native, self.root)

    def test_single_gpu_build_is_not_release(self):
        self.native['cuda_architectures'] = ['89']
        with self.assertRaisesRegex(ValueError, 'architecture set'):
            b.validate_native_build(self.native, self.root)

    def test_missing_vision_binary_proof_blocks_release(self):
        del self.native['outputs']['strata-vision.exe']
        with self.assertRaisesRegex(ValueError, 'engine and vision'):
            b.validate_native_build(self.native, self.root)

    def test_artifact_mismatch_blocks_release(self):
        with self.assertRaisesRegex(ValueError, 'checksum failed'):
            b.validate_native_build(self.native, self.root, self.engine)

    def test_output_architecture_mismatch_blocks_release(self):
        self.native['outputs']['strata.exe']['cuda_architectures'] = ['89']
        with self.assertRaisesRegex(ValueError, 'architecture proof'):
            b.validate_native_build(self.native, self.root)

    def test_repeated_ptx_build_is_not_the_bounded_release_recipe(self):
        self.native['outputs']['strata-vision.exe']['ptx_architectures'] = ['75','120a']
        with self.assertRaisesRegex(ValueError, 'latest-only PTX proof'):
            b.validate_native_build(self.native,self.root)

    def test_windows_executable_spelling_is_normalized_for_cmake_cache_reuse(self):
        source = self.artifacts['strata.exe']
        spelling = str(source.with_suffix('.EXE')) if sys.platform=='win32' else str(source)
        with patch.object(b.shutil,'which',return_value=spelling):
            self.assertEqual(b.executable('strata',{}),source.resolve().as_posix())

    def test_package_without_cpp_sources_verifies_its_own_binaries(self):
        portable = self.root/'portable'; (portable/'engine').mkdir(parents=True)
        (portable/'setup.py').write_text((self.root/'setup.py').read_text())
        for name, source in self.artifacts.items():
            b.shutil.copy2(source, portable/'engine'/name)
        self.assertFalse((portable/'src').exists())
        b.validate_native_build(self.native, portable, portable/'engine')
        (portable/'engine/strata.exe').write_bytes(b'changed portable engine')
        with self.assertRaisesRegex(ValueError, 'checksum failed'):
            b.validate_native_build(self.native, portable, portable/'engine')

    def test_source_checkout_and_physical_engine_path_are_independent(self):
        import portable_build_provenance as provenance
        portable = self.root/'portable'; (portable/'engine').mkdir(parents=True)
        for name, source in self.artifacts.items(): b.shutil.copy2(source, portable/'engine'/name)
        build = dict(self.vendor, t8_native_build=self.native)
        meta = {'upstream_repository':'Niko1221/Strata', 'upstream_version':'0.1.42',
                'engine_version':'0.1.42', 'upstream_release_tag':'v0.1.42',
                'upstream_commit':'e'*40, 'engine_assets_sha256':{
                    'strata-windows-x64.zip':'d'*64, 'strata-windows-x64-hip.zip':'f'*64}}
        provenance.validate_engine_build(build, 'engine', meta, '0.1.42', root=self.root,
                                        engine_directory=portable/'engine')
        # The old vendor executables in the source checkout remain different.
        self.assertNotEqual(b.digest(self.engine/'strata.exe'), self.native['outputs']['strata.exe']['sha256'])

    def test_install_preserves_vendor_dll_and_source(self):
        patcher = lambda p:{'sha256_after': b.digest(p), 'manifest': 'activeCodePage=UTF-8'}
        with patch('windows_utf8_manifest.patch_executable', side_effect=patcher):
            b.install_build(self.root, self.root, self.artifacts, copy.deepcopy(self.native), self.root/'build')
        installed = json.loads((self.engine/'BUILD.json').read_text())
        self.assertEqual(installed['upstream_asset'], self.vendor['upstream_asset'])
        self.assertEqual((self.engine/'vendor.dll').read_bytes(), b'vendor DLL remains')
        self.assertEqual((self.engine/'strata.exe').read_bytes(), self.artifacts['strata.exe'].read_bytes())
        self.assertTrue(installed['capabilities']['native_video'])
        self.assertEqual(installed['source'], 't8_native_build')
        self.assertEqual(installed['archs'], list(map(int,b.ARCHITECTURES)))
        self.assertEqual(installed['cuda'], '13.0')
        self.assertEqual(len(list((self.root/'build/vendor-backup').rglob('*.exe'))), 2)

    def test_failed_second_copy_keeps_vendor_tree(self):
        real_copy = b.shutil.copy2
        def failing_copy(source, destination, *args, **kwargs):
            if Path(source) == self.artifacts['strata-vision.exe']:
                raise OSError('simulated copy failure')
            return real_copy(source, destination, *args, **kwargs)
        patcher = lambda p:{'sha256_after': b.digest(p), 'manifest': 'activeCodePage=UTF-8'}
        with patch('windows_utf8_manifest.patch_executable', side_effect=patcher), patch.object(b.shutil, 'copy2', side_effect=failing_copy):
            with self.assertRaisesRegex(OSError, 'simulated'):
                b.install_build(self.root, self.root, self.artifacts, copy.deepcopy(self.native), self.root/'build')
        self.assertEqual(json.loads((self.engine/'BUILD.json').read_text()), self.vendor)
        self.assertEqual((self.engine/'strata.exe').read_bytes(), b'old strata.exe')

    def test_linked_destination_rejected(self):
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaisesRegex(ValueError, 'vendor engine tree'):
                b.install_build(self.root, self.root, self.artifacts, copy.deepcopy(self.native), self.root/'build')


if __name__ == '__main__':
    unittest.main()

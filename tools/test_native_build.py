"""CPU tests for stale native artifacts, architecture scope and atomic installation."""
from __future__ import annotations
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
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
            'CMakeLists.txt': 'project(strata VERSION 0.1.42)\n', 'src/program/generate.cpp': 'native v1\n',
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

    def add_kquant_library(self):
        name = 'strata_kq.dll'
        source = self.root/'artifacts'/name
        source.write_bytes(b'new source-matched kquant library')
        self.artifacts[name] = source
        (self.engine/name).write_bytes(b'old vendor kquant library')
        self.native['libraries'] = {name: {'sha256': b.digest(source), 'size': source.stat().st_size,
            'cuda_architectures': list(b.ARCHITECTURES), 'ptx_architectures': ['120']}}

    def test_new_engine_requires_source_matched_kquant_library(self):
        (self.root/'CMakeLists.txt').write_text('add_library(strata_kq SHARED kernels.cu)\n')
        with self.assertRaisesRegex(ValueError, 'source-matched strata_kq.dll'):
            b.validate_native_build(self.native, self.root, check_source=False)

    def test_library_keeps_legacy_updater_schema_and_executable_output_contract(self):
        self.add_kquant_library()
        b.validate_native_build(self.native, self.root)
        self.assertEqual(self.native['schema'], 1)
        self.assertEqual(set(self.native['outputs']), {'strata.exe', 'strata-vision.exe'})
        self.assertEqual(set(self.native['libraries']), {'strata_kq.dll'})

    def test_unknown_library_cannot_bypass_hash_validation(self):
        self.native['libraries'] = {'unverified.dll': {}}
        with self.assertRaisesRegex(ValueError, 'library build proof'):
            b.validate_native_build(self.native, self.root)

    def test_library_is_installed_and_hash_checked_without_executable_manifest_patch(self):
        self.add_kquant_library()
        patcher = lambda p: {'sha256_after': b.digest(p), 'manifest': 'activeCodePage=UTF-8'}
        with patch('windows_utf8_manifest.patch_executable', side_effect=patcher) as patch_exe:
            b.install_build(self.root, self.root, self.artifacts, copy.deepcopy(self.native), self.root/'build')
        self.assertEqual(patch_exe.call_count, 2)
        installed = json.loads((self.engine/'BUILD.json').read_text())['t8_native_build']
        b.validate_native_build(installed, self.root, self.engine)
        self.assertEqual((self.engine/'strata_kq.dll').read_bytes(), self.artifacts['strata_kq.dll'].read_bytes())
        (self.engine/'strata_kq.dll').write_bytes(b'stale vendor library')
        with self.assertRaisesRegex(ValueError, 'checksum failed: strata_kq.dll'):
            b.validate_native_build(installed, self.root, self.engine)

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

    def test_msvc_record_executes_developer_environment_path_not_parent_path(self):
        selected = self.root/'developer-tools'/'cl.exe'
        selected.parent.mkdir(); selected.write_bytes(b'compiler fixture')
        env = {'PATH': str(selected.parent)}
        directories = {kind: self.root/(kind+'-build') for kind in ('engine', 'vision')}
        for directory in directories.values():
            directory.mkdir()
            (directory/'CMakeCache.txt').write_text(
                'CMAKE_C_COMPILER:FILEPATH='+selected.as_posix()+'\n'+
                'CMAKE_CXX_COMPILER:FILEPATH='+selected.as_posix()+'\n', encoding='utf-8')
        result = subprocess.CompletedProcess([], 2, stdout='selected MSVC version\n', stderr='no source\n')
        with patch.dict(os.environ, {'PATH': str(self.root/'unrelated-parent-tools')}), \
             patch.object(b.shutil, 'which', return_value=str(selected)) as which, \
             patch.object(b.subprocess, 'run', return_value=result) as child:
            record = b.msvc_compiler_record(directories, env)
        which.assert_called_once_with('cl', path=env['PATH'])
        child.assert_called_once_with([selected.resolve().as_posix(), '/Bv'], env=env,
                                     text=True, capture_output=True, errors='replace')
        self.assertEqual(record['msvc'], 'selected MSVC version\nno source\n')
        self.assertEqual(record['msvc_executable'], selected.resolve().as_posix())
        self.assertEqual(set(record['cmake_compilers']), {'engine', 'vision'})

    def test_msvc_record_refuses_cache_disagreement_before_executing_compiler(self):
        selected = self.root/'developer-cl.exe'; selected.write_bytes(b'compiler fixture')
        different = self.root/'different-cl.exe'; different.write_bytes(b'other compiler')
        for kind in ('engine', 'vision'):
            for language in ('C', 'CXX'):
                with self.subTest(kind=kind, language=language):
                    directories = {name: self.root/(name+'-build') for name in ('engine', 'vision')}
                    for name, directory in directories.items():
                        directory.mkdir(exist_ok=True)
                        (directory/'CMakeCache.txt').write_text(''.join(
                            'CMAKE_'+lang+'_COMPILER:FILEPATH='+
                            (different if name==kind and lang==language else selected).as_posix()+'\n'
                            for lang in ('C', 'CXX')), encoding='utf-8')
                    with patch.object(b.shutil, 'which', return_value=str(selected)), \
                         patch.object(b.subprocess, 'run') as child:
                        with self.assertRaisesRegex(ValueError, 'differs from developer environment'):
                            b.msvc_compiler_record(directories, {'PATH': 'developer-tools'})
                        child.assert_not_called()

    def test_msvc_record_refuses_cache_without_compiler_identity(self):
        directory = self.root/'engine-build'; directory.mkdir()
        (directory/'CMakeCache.txt').write_text('CMAKE_BUILD_TYPE:STRING=Release\n')
        with patch.object(b.shutil, 'which', return_value=str(self.artifacts['strata.exe'])), \
             patch.object(b.subprocess, 'run') as child:
            with self.assertRaisesRegex(ValueError, 'compiler identity missing'):
                b.msvc_compiler_record({'engine': directory}, {'PATH': 'developer-tools'})
            child.assert_not_called()

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

    def test_failed_child_shows_real_stdout_and_stderr_and_keeps_exit_status(self):
        log = self.root/'中文 path'/'engine-configure.log'
        log.parent.mkdir()
        command = [sys.executable, '-X', 'utf8', '-c',
                   'import sys; print("CMAKE_STDOUT", flush=True); '
                   'print("真正的配置错误", file=sys.stderr, flush=True); sys.exit(7)']
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                b.run(command, os.environ.copy(), log)
        self.assertEqual(caught.exception.returncode, 7)
        self.assertIn('CMAKE_STDOUT', stderr.getvalue())
        self.assertIn('真正的配置错误', stderr.getvalue())
        self.assertIn('exit 7', stderr.getvalue())
        self.assertIn('真正的配置错误', log.read_text(encoding='utf-8'))
        self.assertNotIn('CMAKE_STDOUT\n', stdout.getvalue())

    def test_successful_child_keeps_existing_quiet_logging(self):
        log = self.root/'engine-build.log'
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            b.run([sys.executable, '-c', 'print("SUCCESS_OUTPUT")'], os.environ.copy(), log)
        self.assertEqual(log.read_text(encoding='utf-8').strip(), 'SUCCESS_OUTPUT')
        self.assertEqual(stderr.getvalue(), '')
        self.assertTrue(stdout.getvalue().startswith('native build: '))

    def test_failed_child_tail_is_bounded_for_many_lines_and_single_long_line(self):
        for payload in ('EARLY_DIAG\\n' + 'line\\n' * 400 + 'TAIL_ERROR\\n',
                        'EARLY_DIAG' + 'x' * (b.FAILED_LOG_BYTES * 4) + 'TAIL_ERROR\\n'):
            with self.subTest(kind='lines' if 'line' in payload else 'long_line'):
                log = self.root/'engine-build.log'
                log.unlink(missing_ok=True)
                stdout, stderr = io.StringIO(), io.StringIO()
                command = [sys.executable, '-c',
                           'import sys; sys.stdout.write(' + repr(payload.replace('\\n', '\n')) +
                           '); sys.stdout.flush(); sys.exit(9)']
                # Use a real child and a script file to stay below Windows'
                # command-line length limit for the large-output regression.
                script = self.root/'noisy-child.py'
                script.write_text(command[-1], encoding='utf-8')
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(subprocess.CalledProcessError):
                        b.run([sys.executable, str(script)], os.environ.copy(), log)
                report = stderr.getvalue()
                self.assertIn('TAIL_ERROR', report)
                self.assertNotIn('EARLY_DIAG', report)
                self.assertLessEqual(len(report), b.FAILED_LOG_BYTES + 1000)
                self.assertLessEqual(len(report.splitlines()), b.FAILED_LOG_LINES + 2)

    def test_failed_child_diagnostics_survive_cp1252_console(self):
        log = self.root/'engine-configure.log'
        buffer = io.BytesIO()
        console = io.TextIOWrapper(buffer, encoding='cp1252', errors='strict')
        self.addCleanup(console.close)
        command = [sys.executable, '-X', 'utf8', '-c',
                   'import sys; print("配置失败", flush=True); sys.exit(11)']
        with redirect_stdout(io.StringIO()), redirect_stderr(console):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                b.run(command, os.environ.copy(), log)
        self.assertEqual(caught.exception.returncode, 11)
        self.assertIn(b'\\u914d', buffer.getvalue())
        self.assertIn(b'exit 11', buffer.getvalue())

    def test_release_failure_artifact_has_only_explicit_native_diagnostics(self):
        import yaml
        workflow = yaml.safe_load((b.ROOT/'.github/workflows/release.yml').read_text('utf-8'))
        steps = workflow['jobs']['release']['steps']
        uploads = [step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertEqual(len(uploads), 1)
        step = uploads[0]
        self.assertEqual(step['if'], '${{ failure() }}')
        self.assertTrue(step['with']['include-hidden-files'])
        paths = step['with']['path'].splitlines()
        self.assertEqual(set(paths), {
            '.portable-build/t8-native/engine-configure.log',
            '.portable-build/t8-native/engine-build.log',
            '.portable-build/t8-native/vision-configure.log',
            '.portable-build/t8-native/vision-build.log',
            '.portable-build/t8-native/native-contract.log',
            '.portable-build/t8-native/engine-portable/CMakeFiles/CMakeConfigureLog.yaml',
            '.portable-build/t8-native/vision-portable/CMakeFiles/CMakeConfigureLog.yaml',
        })


if __name__ == '__main__':
    unittest.main()

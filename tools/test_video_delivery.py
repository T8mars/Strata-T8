"""CPU delivery regressions for opt-in video, real updates and local Git merges.

All installations and Git histories belong to temporary directories. No model,
compiler, GPU, external download or running service is used by these tests.
"""
from __future__ import annotations
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SOURCE), str(SOURCE/'tools')]
import portable
from serve.server import engine_args
from tools import portable_update as updater
from tools import sync_upstream
import build_native_portable as native
import build_portable as builder
import portable_build_provenance as provenance
import prepare_video_runtime as video_runtime
from portable_version import archive_name


def put(root, relative, value):
    path = root/relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))
    return path


def json_file(root, name, value):
    return put(root, name, json.dumps(value, ensure_ascii=False, indent=2)+'\n')


@contextlib.contextmanager
def local_git_environment():
    """Use the actual Git binary rather than the Windows forwarding shim."""
    candidate = Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'Git/mingw64/bin/git.exe'
    path = str(candidate.parent)+os.pathsep+os.environ.get('PATH', '') if candidate.exists() else os.environ.get('PATH', '')
    with mock.patch.dict(os.environ, {'PATH': path, 'GIT_CONFIG_COUNT': '1',
                                     'GIT_CONFIG_KEY_0': 'index.threads', 'GIT_CONFIG_VALUE_0': '1'}):
        yield


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, encoding='utf-8', stderr=subprocess.PIPE).strip()


def init_git(root):
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Video delivery CPU fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')


def commit(root, message):
    git(root, 'add', '.')
    git(root, 'commit', '-m', message)


class TemporaryInstallation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='Strata 视频交付 ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def launcher_scope(self):
        return mock.patch.multiple(portable, ROOT=self.root, STATE=self.root/'portable-settings.json')

    def config(self):
        return {'exe': 'old.exe', 'backend': 'cuda', 'args': ['--native', 'main.gguf', '--vision'],
                'port': 8083, 'host': '127.0.0.1', 'api_key': 'fixture-only',
                'vision': {'exe': 'old-vision.exe', 'mmproj': 'old-vision.gguf', 'gpu': True,
                           'bundled': True, 'max_tokens': 768}}


class NativeVideoSwitch(TemporaryInstallation):
    def test_unspecified_switch_keeps_default_config_byte_equivalent(self):
        config = self.config()
        before = json.dumps(config, sort_keys=True)
        with self.launcher_scope(), mock.patch.object(portable, 'read_json') as reader:
            self.assertIs(portable.set_native_video(config, None), config)
        self.assertEqual(json.dumps(config, sort_keys=True), before)
        reader.assert_not_called()
        self.assertNotIn('--native-video', engine_args(config))

    def test_explicit_enable_reaches_engine_without_mutating_stored_arguments(self):
        json_file(self.root, 'engine/BUILD.json', {'capabilities': {'native_video': True}})
        config = self.config()
        before = list(config['args'])
        with self.launcher_scope():
            portable.set_native_video(config, True)
        self.assertTrue(config['vision']['native_video'])
        self.assertEqual(config['args'], before)
        self.assertEqual(engine_args(config).count('--native-video'), 1)

    def test_unsupported_backend_or_missing_encoder_is_refused_before_build_read(self):
        for config in ({'backend': 'hip', 'args': [], 'vision': {}}, {'backend': 'cuda', 'args': []}):
            before = copy.deepcopy(config)
            with self.subTest(config=config), self.launcher_scope(), mock.patch.object(portable, 'read_json') as reader:
                with self.assertRaisesRegex(RuntimeError, 'NVIDIA.*vision'):
                    portable.set_native_video(config, True)
                self.assertEqual(config, before)
                reader.assert_not_called()

    def test_vendor_package_cannot_enable_native_video_and_leaves_config_intact(self):
        json_file(self.root, 'engine/BUILD.json', {'version': '0.1.42'})
        config = self.config()
        before = copy.deepcopy(config)
        with self.launcher_scope(), self.assertRaisesRegex(RuntimeError, 'update the package'):
            portable.set_native_video(config, True)
        self.assertEqual(config, before)

    def test_disable_clears_both_legacy_argument_and_new_setting_idempotently(self):
        config = self.config()
        config['vision']['native_video'] = True
        config['args'] += ['--native-video', '--native-video', '--pool-workers', '3']
        with self.launcher_scope():
            portable.set_native_video(config, False)
            once = copy.deepcopy(config)
            portable.set_native_video(config, False)
        self.assertEqual(config, once)
        self.assertFalse(config['vision']['native_video'])
        self.assertNotIn('--native-video', engine_args(config))
        self.assertEqual(config['args'][-2:], ['--pool-workers', '3'])

    def test_refresh_preserves_opt_in_and_custom_settings_while_rebinding_runtime(self):
        json_file(self.root, 'meta.json', {'edition': 'VisionReady-NoMainModel'})
        json_file(self.root, 'vision/catalog.json', {'file': 'encoder.gguf'})
        config = self.config()
        config['vision']['native_video'] = True
        config['args'] += ['--max-context', '8192', '--kv', 'int8']
        cfg = json_file(self.root, 'strata-iq3_s.json', config)
        state = {'portable_data_dir': 'separate-model'}
        current = {'version': '0.1.42-t8.2', 'edition': 'VisionReady-NoMainModel'}
        with self.launcher_scope(), mock.patch.object(portable.upstream, 'cuda_lib_dirs', return_value=[self.root/'runtime']), \
             mock.patch.object(portable.upstream, 'upgrade_config', side_effect=lambda path, value:value):
            portable.refresh_updated_config(cfg, state, current)
        saved = portable.read_json(cfg)
        self.assertEqual(saved['args'], config['args'])
        self.assertEqual(saved['api_key'], config['api_key'])
        self.assertEqual(saved['port'], 8083)
        self.assertTrue(saved['vision']['native_video'])
        self.assertEqual(saved['vision']['exe'], str(self.root/'engine/strata-vision.exe'))
        self.assertEqual(saved['vision']['mmproj'], str(self.root/'vision/weights/encoder.gguf'))
        self.assertIn('--native-video', engine_args(saved))
        self.assertEqual(portable.read_json(self.root/'portable-settings.json')['portable_fingerprint'], current)

    def test_no_models_edition_clears_legacy_native_video_before_next_launch(self):
        json_file(self.root, 'meta.json', {'edition': 'Portable-NoModels'})
        config = self.config()
        config['vision']['native_video'] = True
        config['args'] += ['--native-video', '--custom', 'preserve']
        cfg = json_file(self.root, 'strata-iq3_s.json', config)
        with self.launcher_scope(), mock.patch.object(portable.upstream, 'cuda_lib_dirs', return_value=[]), \
             mock.patch.object(portable.upstream, 'upgrade_config', side_effect=lambda path, value:value):
            portable.refresh_updated_config(cfg, {}, {'version': '0.1.42-t8.2', 'edition': 'Portable-NoModels'})
        saved = portable.read_json(cfg)
        self.assertNotIn('vision', saved)
        self.assertNotIn('--vision', engine_args(saved))
        self.assertNotIn('--native-video', engine_args(saved))
        self.assertEqual(saved['args'][-2:], ['--custom', 'preserve'])

    def test_cli_vision_no_clears_legacy_native_video_without_reconfigure(self):
        config = self.config()
        config['vision']['native_video'] = True
        config['args'] += ['--native-video']
        cfg = json_file(self.root, 'strata-iq3_s.json', config)
        fingerprint = {'app': 'fixture', 'data': 'model', 'gpu': [0], 'ram': 128, 'version': 'current'}
        json_file(self.root, 'portable-settings.json', {'portable_config': cfg.name, 'portable_fingerprint': fingerprint})
        with self.launcher_scope(), mock.patch.object(sys, 'argv', ['portable.py', '--vision', 'no', '--no-browser']), \
             mock.patch.object(portable.os, 'chdir'), mock.patch.object(portable, 'environment_check'), \
             mock.patch.object(portable, 'isolate_setup'), mock.patch.object(portable, 'data_path', return_value=self.root/'model'), \
             mock.patch.object(portable, 'model_delivery'), mock.patch.object(portable, 'fingerprint', return_value=fingerprint), \
             mock.patch.object(portable, 'configure') as configure, mock.patch.object(portable.threading, 'Thread'), \
             mock.patch.object(portable.subprocess, 'call', return_value=0) as start:
            self.assertEqual(portable.main(), 0)
        configure.assert_not_called()
        start.assert_called_once()
        saved = portable.read_json(cfg)
        self.assertNotIn('vision', saved)
        self.assertNotIn('--vision', engine_args(saved))
        self.assertNotIn('--native-video', engine_args(saved))

    def test_contradictory_cli_switches_cannot_destroy_existing_encoder_config(self):
        cfg = json_file(self.root, 'strata-iq3_s.json', self.config())
        fingerprint = {'app': 'fixture', 'data': 'model', 'gpu': [0], 'ram': 128, 'version': 'current'}
        state = json_file(self.root, 'portable-settings.json', {'portable_config': cfg.name, 'portable_fingerprint': fingerprint})
        before = {p:p.read_bytes() for p in (cfg, state)}
        with self.launcher_scope(), \
             mock.patch.object(sys, 'argv', ['portable.py', '--vision', 'no', '--native-video', '--no-browser']), \
             mock.patch.object(portable.os, 'chdir'), mock.patch.object(portable, 'environment_check'), \
             mock.patch.object(portable, 'isolate_setup'), mock.patch.object(portable, 'data_path', return_value=self.root/'model'), \
             mock.patch.object(portable, 'model_delivery'), mock.patch.object(portable, 'fingerprint', return_value=fingerprint), \
             mock.patch.object(portable.threading, 'Thread'), mock.patch.object(portable.subprocess, 'call') as start:
            with self.assertRaises((RuntimeError, SystemExit)):
                portable.main()
        start.assert_not_called()
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_failed_native_enable_during_configure_rolls_back_setup_config_state_and_script(self):
        cfg = json_file(self.root, 'strata-iq3_s.json', self.config())
        state = json_file(self.root, 'portable-settings.json', {'custom': 'preserve', 'portable_config': cfg.name})
        script = put(self.root, 'run-iq3_s'+('.bat' if portable.upstream.WIN else '.sh'), 'previous run script')
        json_file(self.root, 'engine/BUILD.json', {'version': '0.1.42'})
        before = {p:p.read_bytes() for p in (cfg, state, script)}
        def setup():
            portable.save_json(cfg, {'exe': 'new-engine.exe', 'backend': 'cuda', 'args': ['--native', 'fixture.gguf']})
            portable.save_json(state, {'new_setup_value': 'must roll back'})
            script.write_text('generated setup script', encoding='utf-8')
            return 0
        with self.launcher_scope(), mock.patch.object(sys, 'argv', ['test']), \
             mock.patch.object(portable, 'model_delivery', return_value={'family': 'qwen', 'model': 'IQ3_S', 'gguf_dir': 'models'}), \
             mock.patch.object(portable, 'fingerprint', return_value={'data': 'fixture'}), \
             mock.patch.object(portable.upstream, 'main', side_effect=setup), \
             mock.patch.object(portable.upstream, 'write_run_script'), \
             mock.patch.object(portable, 'attach_vision', side_effect=lambda config, *args:config):
            with self.assertRaisesRegex(RuntimeError, 'update the package'):
                portable.configure(self.root/'data', vision='gpu', native_video=True)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)


def native_fixture(root):
    """Small genuine filesystem identity with two independent backend BUILD files."""
    for name, data in {
        'setup.py': 'LLAMA_CPP_COMMIT = "'+'a'*40+'"\n',
        'CMakeLists.txt': 'project(strata VERSION 0.1.42)\n',
        'src/program/generate.cpp': 'native fixture v1\n',
        'tools/vision/strata_vision.cpp': 'vision fixture v1\n',
        'include/strata/native.hpp': 'paired-frame contract\n',
        'tools/build_native_portable.py': 'builder fixture\n',
        'tools/windows_utf8_manifest.py': 'manifest fixture\n',
        'third_party/llama.cpp/CMakeLists.txt': 'project(llama)\n',
        'third_party/llama.cpp/src/llama.cpp': 'pinned llama fixture\n',
        'third_party/llama.cpp/gguf-py/gguf/__init__.py': '# fixture\n',
        'third_party/llama.cpp/LICENSE': 'upstream license fixture\n',
        'third_party/llama.cpp/UPSTREAM.json': '{}',
    }.items():
        put(root, name, data)
    meta = {'version': '0.1.42-t8.2', 'upstream_repository': 'Niko1221/Strata', 'upstream_version': '0.1.42',
            'engine_version': '0.1.42', 'upstream_release_tag': 'v0.1.42', 'upstream_commit': 'e'*40,
            'engine_assets_sha256': {'strata-windows-x64.zip': 'd'*64, 'strata-windows-x64-hip.zip': 'f'*64}}
    json_file(root, 'meta.json', meta)
    json_file(root, 'features.json', {'native_video_cuda': True, 'video_frame_analysis': True, 'video_sampling_cpu': True})
    third = {'repository': 'ggml-org/llama.cpp', 'commit': 'a'*40, 'archive_sha256': 'b'*64,
             'source_tree_sha256': native.canonical_digest(native.llama_inputs(root))}
    build = {'schema': 1, 'source_commit': 'c'*40, 'portable': True,
             'cuda_targets': list(native.CUDA_TARGETS), 'fatbinary_compression': 'size',
             'cuda_architectures': list(native.ARCHITECTURES), 'source_identity': native.source_identity(root, third),
             'capabilities': {'native_video': True, 'geniv': 1, 'encv': 1, 'sve': 1}, 'outputs': {}}
    for directory, asset in provenance.ENGINE_ASSETS.items():
        vendor = {'version': '0.1.42', 'upstream_asset': {'repository': 'Niko1221/Strata', 'release_tag': 'v0.1.42',
                  'name': asset, 'upstream_commit': 'e'*40, 'sha256': meta['engine_assets_sha256'][asset]}}
        put(root, directory+'/strata.exe', b'CPU fixture executable '+directory.encode())
        if directory == 'engine':
            put(root, directory+'/strata-vision.exe', b'CPU fixture vision executable')
            for name in ('strata.exe', 'strata-vision.exe'):
                path = root/directory/name
                build['outputs'][name] = {'sha256': native.digest(path), 'size': path.stat().st_size,
                    'ptx_architectures': ['120a'] if name == 'strata-vision.exe' else ['120'],
                    'utf8_manifest': 'activeCodePage=UTF-8', 'cuda_architectures': list(native.ARCHITECTURES)}
            vendor['t8_native_build'] = build
            vendor['capabilities'] = {'native_video': True, 'geniv': 1, 'encv': 1}
        json_file(root, directory+'/BUILD.json', vendor)
    return meta


class NativeBuildDelivery(TemporaryInstallation):
    def test_release_gate_requires_self_built_cuda_but_accepts_vendor_hip(self):
        meta = native_fixture(self.root)
        for directory in provenance.ENGINE_ASSETS:
            build = portable.read_json(self.root/directory/'BUILD.json')
            provenance.validate_engine_build(build, directory, meta, '0.1.42', allow_vendor=False, root=self.root)
        vendor_only = portable.read_json(self.root/'engine/BUILD.json')
        del vendor_only['t8_native_build']
        with self.assertRaisesRegex(ValueError, 'self-built CUDA'):
            provenance.validate_engine_build(vendor_only, 'engine', meta, '0.1.42', allow_vendor=False, root=self.root)

    def test_native_binary_tamper_blocks_delivery_even_without_source_checkout(self):
        meta = native_fixture(self.root)
        build = portable.read_json(self.root/'engine/BUILD.json')
        shutil.rmtree(self.root/'src')  # Extracted packages have no C++ checkout.
        provenance.validate_engine_build(build, 'engine', meta, '0.1.42', allow_vendor=False, root=self.root)
        put(self.root, 'engine/strata-vision.exe', b'mismatched helper')
        with self.assertRaisesRegex(ValueError, 'executable checksum'):
            provenance.validate_engine_build(build, 'engine', meta, '0.1.42', allow_vendor=False, root=self.root)

    def package_fixture(self):
        meta = native_fixture(self.root)
        names = ['portable.py', 'strata.py', 'requirements-build.txt', 'requirements-monitor.txt', 'requirements.txt', 'requirements-portable.txt', 'requirements-asr.txt', 'LICENSE', 'README.md',
                 'data/dsv41/profile-2.00.bin', 'data/dsv41/profile-2.25.bin',
                 'README-PORTABLE.zh-CN.md', 'model-sources.json', 'START-PORTABLE.bat', 'CHECK-ENV.bat',
                 'IMPORT-MODEL.bat', 'PREPARE-MODEL.bat', 'UPDATE-PORTABLE.bat', 'INSTALL-VISION.bat',
                 'VERIFY-PACKAGE.bat', 'README-UPSTREAM.md', 'runtime/python/marker.txt',
                 'runtime/VIDEO-DEPENDENCIES.json',
                 'serve/video.py', 'tools/strata_video.py', 'ref/fixture.txt', 'data/fixture.txt', 'vision/catalog.json']
        for name in names:
            put(self.root, name, '# fixture\n')
        shutil.copytree(SOURCE/'licenses', self.root/'licenses')
        put(self.root, 'licenses/video/RoAdMaP.md', 'local plan must not ship')
        put(self.root, 'tools/test_fixture.py', 'test must not ship')
        out = self.root/'dist'
        with local_git_environment():
            init_git(self.root)
            commit(self.root, 'packaging fixture')
        return meta, out

    def test_build_with_missing_video_runtime_is_refused_before_creating_distribution(self):
        meta, out = self.package_fixture()
        (self.root/'runtime/VIDEO-DEPENDENCIES.json').unlink()
        # The rest of the package is sufficient; absence of a video runtime
        # must itself block the real package entry point.
        with local_git_environment():
            with mock.patch.multiple(builder, ROOT=self.root, META=meta, OUT=out, TARGET=out/'unused', DOCS=()), \
                 mock.patch.object(builder, 'source_version', return_value='0.1.42'), \
                 mock.patch.object(builder, 'patch_engine'), \
                 mock.patch.object(sys, 'argv', ['build_portable.py', '--edition', 'Portable-NoModels']):
                with self.assertRaisesRegex((ValueError, RuntimeError, OSError), '[Vv]ideo|VIDEO|av\\.libs'):
                    builder.main()
        self.assertFalse(list(out.glob('*.zip')))

    def test_real_package_checks_source_and_stage_and_contains_video_components(self):
        meta, out = self.package_fixture()
        # Exact wheel bytes are verified independently below. This small build
        # isolates packaging behavior and proves both runtime gates are called.
        with local_git_environment(), \
             mock.patch.multiple(builder, ROOT=self.root, META=meta, OUT=out, TARGET=out/'unused', DOCS=()), \
             mock.patch.object(builder, 'source_version', return_value='0.1.42'), mock.patch.object(builder, 'patch_engine'), \
             mock.patch.object(video_runtime, 'verify', return_value={}) as verify, \
             mock.patch.object(sys, 'argv', ['build_portable.py', '--edition', 'Portable-NoModels']):
            builder.main()
        target = out/archive_name(meta['version']).removesuffix('.zip')
        self.assertEqual(verify.call_args_list, [mock.call(self.root), mock.call(target)])
        archive = out/archive_name(meta['version'])
        self.assertTrue(archive.is_file())
        manifest = portable.read_json(target/'PACKAGE-MANIFEST.json')
        required = {'serve/video.py', 'tools/strata_video.py', 'runtime/VIDEO-DEPENDENCIES.json',
                    'licenses/video/NOTICE.md', 'licenses/video/sources.json', 'licenses/video/COPYING.LGPLv3',
                    'engine/BUILD.json', 'engine/strata.exe', 'engine/strata-vision.exe', 'engine-hip/BUILD.json'}
        names = {x['path'] for x in manifest['files']}
        self.assertTrue(required <= names)
        self.assertFalse(any('roadmap.md' in name.casefold() or 'test_fixture.py' in name for name in names))
        self.assertEqual(manifest['weights'], {'main': False, 'mtp': False, 'vision': False})
        self.assertTrue(manifest['engines']['engine']['t8_native_build']['capabilities']['native_video'])
        self.assertNotIn('t8_native_build', manifest['engines']['engine-hip'])
        updater.validate_manifest(target, manifest)
        with zipfile.ZipFile(archive) as z:
            self.assertIsNone(z.testzip())
            archive_names = {name.split('/', 1)[1] for name in z.namelist()}
        self.assertEqual(archive_names, names | {'PACKAGE-MANIFEST.json'})

    def test_staged_runtime_verification_failure_prevents_release_archive(self):
        meta, out = self.package_fixture()
        with local_git_environment(), \
             mock.patch.multiple(builder, ROOT=self.root, META=meta, OUT=out, TARGET=out/'unused', DOCS=()), \
             mock.patch.object(builder, 'source_version', return_value='0.1.42'), mock.patch.object(builder, 'patch_engine'), \
             mock.patch.object(video_runtime, 'verify', side_effect=[{}, ValueError('Video staged-runtime mismatch')]) as verify, \
             mock.patch.object(sys, 'argv', ['build_portable.py', '--edition', 'Portable-NoModels']):
            with self.assertRaisesRegex(ValueError, 'staged-runtime mismatch'):
                builder.main()
        self.assertEqual(verify.call_count, 2)
        self.assertFalse(list(out.glob('*.zip')))


class VideoLicenseInventory(TemporaryInstallation):
    def setUp(self):
        super().setUp()
        shutil.copytree(SOURCE/'licenses', self.root/'licenses')

    def test_actual_cpu_decoder_inventory_matches_dll_bytes_and_fixed_license_sources(self):
        import av
        base = Path(av.__file__).resolve().parent.parent
        sources = portable.read_json(self.root/'licenses/video/sources.json')
        for item in sources['runtime_files']:
            destination = self.root/'runtime/python/Lib/site-packages'/item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base/item['path'], destination)
        result = video_runtime.prepare(self.root)
        self.assertEqual(result['av_version'], '15.0.0')
        self.assertEqual(result['ffmpeg_version'], '7.1.1')
        directory = Path(av.__file__).resolve().parent.parent/'av.libs'
        self.assertEqual({x['path'] for x in result['dynamic_files']}, {p.name for p in directory.glob('*.dll')})
        for entry in result['dynamic_files']:
            path = directory/entry['path']
            self.assertEqual(path.stat().st_size, entry['size'])
            self.assertEqual(native.digest(path), entry['sha256'])
        self.assertTrue(result['libraries']['libavcodec']['license'])
        self.assertEqual(portable.read_json(self.root/'runtime/VIDEO-DEPENDENCIES.json'), json.loads(json.dumps(result)))

    def locked_fixture(self):
        """Reduced wheel fixture proves verification failures on actual bytes."""
        dll = put(self.root, 'runtime/python/Lib/site-packages/av.libs/decoder.dll', b'fixed decoder bytes')
        pyav = put(self.root, 'runtime/python/Lib/site-packages/av/__init__.py', '# fixed PyAV package')
        license_file = put(self.root, 'runtime/python/Lib/site-packages/av.dist-info/licenses/LICENSE', 'BSD fixture')
        base = self.root/'runtime/python/Lib/site-packages'
        sources = {'av_version': '15.0.0', 'ffmpeg_version': '7.1.1',
                   'wheel': {'filename': 'reduced-verifier-fixture.whl', 'digests': {'sha256': 'a'*64}},
                   'runtime_files': [{'path': p.relative_to(base).as_posix(), 'size': p.stat().st_size,
                                      'sha256': native.digest(p)} for p in (dll, pyav, license_file)],
                   'dynamic_files': [{'path': dll.name, 'size': dll.stat().st_size, 'sha256': native.digest(dll)}],
                   'license_sources': []}
        json_file(self.root, 'licenses/video/sources.json', sources)
        inventory = {'av_version': '15.0.0', 'ffmpeg_version': '7.1.1', 'fixed_sources': sources,
                     'dynamic_files': sources['dynamic_files'], 'libraries': {'fixture': {'license': 'fixture only'}}}
        json_file(self.root, 'runtime/VIDEO-DEPENDENCIES.json', inventory)
        video_runtime.verify(self.root)
        return dll, inventory

    def test_dll_tamper_cannot_be_laundered_by_rewriting_inventory(self):
        dll, inventory = self.locked_fixture()
        dll.write_bytes(b'changed decoder bytes')
        inventory['dynamic_files'] = [{'path': dll.name, 'size': dll.stat().st_size, 'sha256': native.digest(dll)}]
        json_file(self.root, 'runtime/VIDEO-DEPENDENCIES.json', inventory)
        with self.assertRaisesRegex(ValueError, 'fixed wheel/source identity'):
            video_runtime.verify(self.root)
        inventory['dynamic_files'] = inventory['fixed_sources']['dynamic_files']
        json_file(self.root, 'runtime/VIDEO-DEPENDENCIES.json', inventory)
        with self.assertRaisesRegex(ValueError, 'runtime file differs'):
            video_runtime.verify(self.root)

    def test_missing_pyav_license_and_unexpected_dll_are_refused(self):
        _, _ = self.locked_fixture()
        extra = put(self.root, 'runtime/python/Lib/site-packages/av.libs/unlisted.dll', b'not a pinned component')
        with self.assertRaisesRegex(ValueError, 'Unexpected.*dynamic library'):
            video_runtime.verify(self.root)
        extra.unlink()
        (self.root/'runtime/python/Lib/site-packages/av.dist-info/licenses/LICENSE').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing.*runtime file'):
            video_runtime.verify(self.root)

    def test_license_tampering_cannot_publish_dependency_inventory(self):
        put(self.root, 'licenses/video/COPYING.LGPLv3', 'changed license text')
        with self.assertRaisesRegex(ValueError, 'license/source digest'):
            video_runtime.prepare(self.root)
        self.assertFalse((self.root/'runtime/VIDEO-DEPENDENCIES.json').exists())

    def test_decoder_source_version_mismatch_cannot_publish_inventory(self):
        sources = portable.read_json(self.root/'licenses/video/sources.json')
        sources['av_version'] = '0.0.0'
        json_file(self.root, 'licenses/video/sources.json', sources)
        with self.assertRaisesRegex(ValueError, 'fixed dependency sources'):
            video_runtime.prepare(self.root)
        self.assertFalse((self.root/'runtime/VIDEO-DEPENDENCIES.json').exists())


def update_package(root, files, version):
    files = {**files, 'meta.json': json.dumps({'version': version, 'repository': 'T8mars/Strata-T8',
             'edition': 'Portable-NoModels', 'weights': {'main': False, 'mtp': False, 'vision': False}, 'models_included': False})}
    entries = []
    for name, value in files.items():
        path = put(root, name, value)
        entries.append({'path': name, 'size': path.stat().st_size, 'sha256': native.digest(path)})
    manifest = {'version': version, 'edition': 'Portable-NoModels', 'weights': {'main': False, 'mtp': False, 'vision': False},
                'models_included': False, 'files': entries}
    json_file(root, 'PACKAGE-MANIFEST.json', manifest)
    return manifest


@unittest.skipUnless(os.name == 'nt', 'Actual portable replacement requires Windows')
class VideoReleaseUpdate(TemporaryInstallation):
    def prepare(self):
        installed, incoming = self.root/'installed', self.root/'incoming-source'
        old = {'engine/BUILD.json': '{"version":"old"}', 'engine/strata.exe': b'old engine',
               'serve/server.py': '# old server', 'runtime/python/old-decoder.txt': 'obsolete decoder',
               'tools/apply_portable_update.ps1': (SOURCE/'tools/apply_portable_update.ps1').read_bytes()}
        update_package(installed, old, '0.1.42-t8.1')
        newer = {'engine/BUILD.json': '{"version":"0.1.42","capabilities":{"native_video":true}}',
                 'engine/strata.exe': b'new native engine', 'engine/strata-vision.exe': b'new ENCV helper',
                 'serve/server.py': '# native server', 'serve/video.py': '# frame contract',
                 'tools/strata_video.py': '# real CPU decoder entry', 'requirements-portable.txt': 'av==15.0.0\n',
                 'runtime/VIDEO-DEPENDENCIES.json': '{"av_version":"15.0.0"}',
                 'runtime/python/Lib/site-packages/av/__init__.py': '# new PyAV package',
                 'runtime/python/Lib/site-packages/av.libs/avcodec.dll': b'new decoder DLL',
                 'licenses/video/NOTICE.md': '# independent decoder licenses',
                 'licenses/video/COPYING.LGPLv3': b'license text fixture'}
        update_package(incoming, newer, '0.1.42-t8.2')
        config = self.config()
        config['vision']['native_video'] = True
        self.user_files = {'strata-iq3_s.json': json.dumps(config), 'portable-settings.json': '{"data":"external"}',
                           'Strata-data/model.gguf': b'user model marker', 'logs/user.log': b'user log',
                           'roadmap.md': '# private plan stays local'}
        for name, value in self.user_files.items():
            put(installed, name, value)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as z:
            for path in incoming.rglob('*'):
                if path.is_file():
                    z.write(path, 'Strata/'+path.relative_to(incoming).as_posix())
        data = payload.getvalue()
        name = archive_name('0.1.42-t8.2')
        release = {'tag_name': 'v0.1.42-t8.2', 'assets': [
            {'name': name, 'size': len(data), 'browser_download_url': 'https://github.com/fixture/archive'},
            {'name': name+'.sha256', 'browser_download_url': 'https://github.com/fixture/checksum'}]}
        def fetch(url, destination=None, limit=None):
            if destination is None:
                return (hashlib.sha256(data).hexdigest()+'  '+name).encode('ascii')
            destination.write_bytes(data)
            return len(data)
        stage = self.root/'owned-stage'
        stage.mkdir()
        with mock.patch.object(updater, 'fetch', side_effect=fetch), \
             mock.patch.object(updater, 'running_processes', return_value=[]), \
             mock.patch.object(updater.tempfile, 'mkdtemp', return_value=str(stage)):
            plan = updater.prepare(installed, release)
        return installed, plan, old, newer

    def apply(self, installed):
        return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                               str(installed/'.portable-update/apply.ps1'), '-PlanPath',
                               str(installed/'.portable-update/plan.json')], capture_output=True,
                               text=True, errors='replace', timeout=45)

    def assert_user_files(self, installed):
        for name, value in self.user_files.items():
            self.assertEqual((installed/name).read_bytes(), value if isinstance(value, bytes) else value.encode('utf-8'))

    def test_actual_verified_upgrade_installs_all_video_layers_and_preserves_opt_in(self):
        installed, plan, old, newer = self.prepare()
        result = self.apply(installed)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        for name, value in newer.items():
            self.assertEqual((installed/name).read_bytes(), value if isinstance(value, bytes) else value.encode('utf-8'))
        self.assertFalse((installed/'runtime/python/old-decoder.txt').exists())
        self.assert_user_files(installed)
        self.assertTrue(portable.read_json(installed/'strata-iq3_s.json')['vision']['native_video'])
        self.assertEqual((Path(plan['backup'])/'engine/strata.exe').read_bytes(), old['engine/strata.exe'])

    def test_decoder_corruption_after_prepare_preserves_entire_old_installation(self):
        installed, plan, old, _ = self.prepare()
        put(Path(plan['stage']), 'runtime/python/Lib/site-packages/av.libs/avcodec.dll', b'corrupt')
        result = self.apply(installed)
        self.assertNotEqual(result.returncode, 0)
        for name, value in old.items():
            self.assertEqual((installed/name).read_bytes(), value if isinstance(value, bytes) else value.encode('utf-8'))
        self.assertFalse((installed/'serve/video.py').exists())
        self.assertEqual(portable.read_json(installed/'PACKAGE-MANIFEST.json')['version'], '0.1.42-t8.1')
        self.assert_user_files(installed)


class VideoUpstreamSynchronization(TemporaryInstallation):
    def setUp(self):
        super().setUp()
        scope = local_git_environment()
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)
        self.upstream = self.root/'upstream'
        self.upstream.mkdir()
        init_git(self.upstream)
        for name, data in {'CMakeLists.txt': 'project(strata VERSION 0.1.42)\n', 'README.md': 'upstream README',
                           'src/program/generate.cpp': 'native base\n', 'setup.py': 'upstream installer base\n'}.items():
            put(self.upstream, name, data)
        commit(self.upstream, 'base upstream')
        self.fork = self.root/'fork'
        git(self.root, 'clone', str(self.upstream), str(self.fork))
        git(self.fork, 'config', 'user.name', 'Video delivery CPU fixture')
        git(self.fork, 'config', 'user.email', 'fixture@example.invalid')
        json_file(self.fork, 'meta.json', {'upstream_version': '0.1.42', 'revision': 2, 'version': '0.1.42-t8.2'})
        self.retained = {'README.md': 'professional T8 README', 'serve/video.py': 'video message contract',
                         'tools/strata_video.py': 'decoder production module', 'tools/build_native_portable.py': 'native builder',
                         'requirements-portable.txt': 'av==15.0.0\n', 'licenses/video/NOTICE.md': 'video notice',
                         'src/program/generate.cpp': 'T8 native temporal inference\n',
                         'features.json': '{"native_video_cuda":true,"native_video_default":false}',
                         '.gitattributes': 'README.md merge=t8-keep\n', '.gitignore': '.portable-build/\nroadmap.md\n'}
        for name, value in self.retained.items():
            put(self.fork, name, value)
        commit(self.fork, 'T8 opt-in video implementation')
        put(self.fork, 'roadmap.md', 'private local research')

    def test_unrelated_upstream_update_preserves_all_video_files_and_requires_engine_rebuild(self):
        put(self.upstream, 'CMakeLists.txt', 'project(strata VERSION 0.1.43)\n')
        put(self.upstream, 'setup.py', 'upstream installer next version\n')
        commit(self.upstream, 'next upstream release')
        git(self.upstream, 'tag', 'v0.1.43')
        assets = [{'name': 'strata-windows-x64.zip', 'digest': 'sha256:'+'d'*64},
                  {'name': 'strata-windows-x64-hip.zip', 'digest': 'sha256:'+'f'*64}]
        result = sync_upstream.sync(self.fork, str(self.upstream), 'refs/tags/v0.1.43', release_assets=assets)
        self.assertTrue(result['changed'])
        self.assertEqual(result['version'], '0.1.43-t8.1')
        for name, value in self.retained.items():
            self.assertEqual((self.fork/name).read_text('utf-8'), value)
        self.assertEqual((self.fork/'setup.py').read_text(), 'upstream installer next version\n')
        self.assertEqual(portable.read_json(self.fork/'meta.json')['engine_version'], '0.1.43')
        with self.assertRaisesRegex(ValueError, 'source engine version'):
            provenance.validate_engine_build({'version': '0.1.42'}, 'engine',
                portable.read_json(self.fork/'meta.json'), '0.1.43', root=self.fork)
        self.assertEqual((self.fork/'roadmap.md').read_text(), 'private local research')
        self.assertEqual(git(self.fork, 'status', '--porcelain'), '')

    def test_conflicting_native_upstream_edit_aborts_and_preserves_full_video_delivery(self):
        before = git(self.fork, 'rev-parse', 'HEAD')
        put(self.upstream, 'src/program/generate.cpp', 'different upstream generation algorithm\n')
        commit(self.upstream, 'conflicting native update')
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            sync_upstream.sync(self.fork, str(self.upstream), 'refs/heads/main')
        self.assertEqual(git(self.fork, 'rev-parse', 'HEAD'), before)
        for name, value in self.retained.items():
            self.assertEqual((self.fork/name).read_text('utf-8'), value)
        report = portable.read_json(self.fork/'.portable-build/upstream-conflict.json')
        self.assertIn('src/program/generate.cpp', report['conflicts'])
        self.assertEqual(git(self.fork, 'status', '--porcelain'), '')

    def test_upstream_private_plan_is_never_merged_or_packaged(self):
        before = git(self.fork, 'rev-parse', 'HEAD')
        put(self.upstream, 'docs/RoadMap.md', 'unexpected private planning')
        commit(self.upstream, 'private upstream path')
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            sync_upstream.sync(self.fork, str(self.upstream), 'refs/heads/main')
        self.assertEqual(git(self.fork, 'rev-parse', 'HEAD'), before)
        self.assertFalse((self.fork/'docs/RoadMap.md').exists())
        self.assertEqual((self.fork/'roadmap.md').read_text(), 'private local research')
        self.assertEqual(git(self.fork, 'status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()

"""Reproduce upstream hotfix/native version and private-roadmap packaging cases."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import bootstrap_portable as bootstrap, build_portable as builder
from tools import portable_build_provenance as provenance


COMMIT = '82f46a8c8f475f001ad76d92f58f4a4f8ffb0253'


def fixture_zip(version='0.1.40'):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('BUILD.json', json.dumps({'version': version, 'source': 'release'}))
        archive.writestr('strata.exe', b'unsigned vendor executable fixture')
    return data.getvalue()


def fixture_meta(pins):
    return {'upstream_repository': 'Niko1221/Strata', 'upstream_version': '0.1.40',
            'engine_version': '0.1.40', 'upstream_release_tag': 'v0.1.40.1',
            'upstream_commit': COMMIT, 'engine_assets_sha256': pins}


class UpstreamNativeBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload = fixture_zip()
        self.pins = {name: hashlib.sha256(self.payload).hexdigest() for name in provenance.ENGINE_ASSETS.values()}
        self.meta = fixture_meta(self.pins)
        self.release = {'tag_name': 'v0.1.40.1', 'draft': False, 'prerelease': False,
                        'assets': [{'name': name, 'digest': 'sha256:' + sha,
                                    'browser_download_url': f'https://github.com/Niko1221/Strata/releases/download/v0.1.40.1/{name}'}
                                   for name, sha in self.pins.items()]}
        self.requests = []

    def tearDown(self):
        self.tmp.cleanup()

    def network(self, request, **kwargs):
        self.requests.append(request)
        if isinstance(request, str):
            self.assertIn('/releases/download/v0.1.40.1/', request)
            return io.BytesIO(self.payload)
        self.assertEqual(request.full_url, 'https://api.github.com/repos/Niko1221/Strata/releases/tags/v0.1.40.1')
        return io.BytesIO(json.dumps(self.release).encode())

    def invoke(self):
        with mock.patch.object(bootstrap.urllib.request, 'urlopen', side_effect=self.network), \
             mock.patch.object(bootstrap, 'patch_engine'):
            return bootstrap.bootstrap_engines(self.meta, '0.1.40', self.root/'cache', self.root/'stage')

    def test_hotfix_release_uses_three_part_native_build_and_records_verified_source(self):
        with mock.patch.dict('os.environ', {'GH_TOKEN': 'fixture-token'}):
            builds = self.invoke()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[0].get_header('Authorization'), 'Bearer fixture-token')
        for directory, name in provenance.ENGINE_ASSETS.items():
            self.assertEqual(builds[directory]['version'], '0.1.40')
            self.assertEqual(builds[directory]['upstream_asset'],
                             {'repository': 'Niko1221/Strata', 'release_tag': 'v0.1.40.1',
                              'name': name, 'sha256': self.pins[name], 'upstream_commit': COMMIT})
            self.assertEqual((self.root/'cache'/f'0.1.40.1-{name}').read_bytes(), self.payload)
        self.requests.clear()
        self.invoke()
        self.assertEqual(len(self.requests), 1, 'verified cached archives must avoid asset downloads')

    def test_changed_official_digest_is_rejected_before_download(self):
        self.release['assets'][0]['digest'] = 'sha256:' + 'f'*64
        with self.assertRaisesRegex(ValueError, 'pinned SHA256'):
            self.invoke()
        self.assertEqual(len(self.requests), 1)
        self.assertFalse((self.root/'stage').exists())

    def test_asset_checksum_is_verified_before_extraction(self):
        self.payload = b'corrupt upstream download'
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            self.invoke()
        self.assertFalse((self.root/'stage').exists())
        self.assertFalse(any((self.root/'cache').iterdir()))

    def test_wrong_native_build_leaves_existing_engine_untouched(self):
        self.payload = fixture_zip('0.1.39')
        for asset in self.release['assets']:
            sha = hashlib.sha256(self.payload).hexdigest()
            self.meta['engine_assets_sha256'][asset['name']] = sha
            asset['digest'] = 'sha256:' + sha
        target = self.root/'stage/engine'
        target.mkdir(parents=True)
        (target/'strata.exe').write_bytes(b'previous working binary')
        with self.assertRaisesRegex(ValueError, 'Mismatched upstream engine'):
            self.invoke()
        self.assertEqual((target/'strata.exe').read_bytes(), b'previous working binary')
        self.assertEqual(list(target.parent.iterdir()), [target])

    def test_verified_engine_replaces_old_vendor_tree(self):
        old = self.root/'stage/engine/obsolete-vendor.dll'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'old unused library')
        self.invoke()
        self.assertFalse(old.exists())
        self.assertTrue((old.parent/'strata.exe').is_file())

    def test_failed_install_restores_previous_engine(self):
        target = self.root/'stage/engine'
        target.mkdir(parents=True)
        (target/'strata.exe').write_bytes(b'previous working binary')
        rename = Path.rename
        def fail_install(path, destination):
            if path.name == 'engine' and path.parent.name.startswith('.engine-stage-'):
                raise OSError('fixture install denied')
            return rename(path, destination)
        with mock.patch.object(Path, 'rename', fail_install), self.assertRaisesRegex(OSError, 'install denied'):
            self.invoke()
        self.assertEqual((target/'strata.exe').read_bytes(), b'previous working binary')
        self.assertEqual(list(target.parent.iterdir()), [target])

    def test_failed_rollback_preserves_previous_engine_for_recovery(self):
        target = self.root/'stage/engine'
        target.mkdir(parents=True)
        (target/'strata.exe').write_bytes(b'previous working binary')
        rename = Path.rename
        def deny_restore(path, destination):
            if path.parent.name.startswith('.engine-stage-'):
                raise OSError('fixture directory rename denied')
            return rename(path, destination)
        with mock.patch.object(Path, 'rename', deny_restore), self.assertRaisesRegex(RuntimeError, 'rollback incomplete'):
            self.invoke()
        backups = list(target.parent.glob('.engine-stage-*/previous/strata.exe'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'previous working binary')

    def test_unstable_wrong_tag_or_duplicate_asset_cannot_select_engines(self):
        cases = [dict(self.release, tag_name='v0.1.40'), dict(self.release, prerelease=True),
                 dict(self.release, assets=self.release['assets'] + self.release['assets'][:1])]
        for value in cases:
            with self.subTest(value=value):
                self.release = value
                self.requests.clear()
                with self.assertRaises(ValueError):
                    self.invoke()
                self.assertEqual(len(self.requests), 1)

    def test_manifest_rejects_other_hotfix_commit_or_asset(self):
        builds = self.invoke()
        for field, value in [('release_tag', 'v0.1.40'), ('upstream_commit', 'a'*40), ('sha256', 'b'*64)]:
            changed = json.loads(json.dumps(builds['engine']))
            changed['upstream_asset'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                provenance.validate_engine_build(changed, 'engine', self.meta, '0.1.40')

    def test_source_engine_and_release_identity_must_match(self):
        for changes in ({'engine_version': '0.1.39'}, {'upstream_release_tag': 'v0.1.41.1'},
                        {'upstream_release_tag': 'v0.01.40.1'}, {'upstream_commit': 'main'},
                        {'engine_assets_sha256': {}}, {'engine_assets_sha256': {'unknown.zip': '0'*64}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                provenance.build_identity(dict(self.meta, **changes), '0.1.40')


class ShippingFiles(unittest.TestCase):
    def test_pack_cli_starts_using_only_shipped_source(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'package'
            with mock.patch.object(builder, 'TARGET', target):
                builder.copy_source('tools')
                builder.copy_source('ref')
            # Embedded Python adds the development root through its _pth file.
            # Remove it explicitly so this cannot pass by borrowing local source.
            script = (
                'import sys; from pathlib import Path; '
                f'root=Path({str(builder.ROOT)!r}).resolve(); '
                'sys.path[:]=[p for p in sys.path if Path(p).resolve() not in (root,root/"tools",root/"ref")]; '
                f'sys.path.insert(0,{str(target/"tools")!r}); '
                'import strata_pack,canonical_xcheck,load; '
                'print(strata_pack.__file__); print(canonical_xcheck.__file__); print(load.__file__); '
                'sys.argv=["strata_pack.py","--help"]; strata_pack.main()'
            )
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], cwd=target,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Path(result.stdout.splitlines()[2]).resolve(), (target/'ref'/'load.py').resolve())
            self.assertIn('build', result.stdout)
            self.assertIn('verify', result.stdout)

    def test_local_or_tracked_roadmaps_never_enter_staged_source_or_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'source'
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            for name in ('roadmap.md', 'serve/ROADMAP.MD', 'runtime/nested/RoadMap.Md', 'serve/server.py'):
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('private local plan' if path.suffix.lower() == '.md' else 'public source')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            target = Path(directory)/'package'
            with mock.patch.object(builder, 'ROOT', root), mock.patch.object(builder, 'TARGET', target):
                builder.copy_source('.')
                builder.copy_tree('runtime')
                files = builder.package_files()
            self.assertEqual([p.relative_to(target).as_posix() for p in files], ['serve/server.py'])

    def test_accidental_roadmap_in_final_tree_blocks_manifest_and_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for rel in ('roadmap.md', 'engine/ROADMAP.MD', 'runtime/docs/RoadMap.Md'):
                path = target/rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('must not ship')
                with mock.patch.object(builder, 'TARGET', target), self.assertRaisesRegex(ValueError, 'roadmap'):
                    builder.package_files()
                path.unlink()

    def test_operational_docs_include_new_referenced_documents(self):
        self.assertIn('STRIX_HALO.md', builder.DOCS)
        self.assertIn('EXCHANGE_ROTATION.md', builder.DOCS)
        for name in builder.DOCS:
            path = builder.ROOT/'docs'/name
            self.assertTrue(path.is_file(), name)
            for target in re.findall(r'\]\(([^)]+\.md)(?:#[^)]*)?\)', path.read_text(encoding='utf-8')):
                if '://' in target or '/' in target:
                    continue
                self.assertIn(target, builder.DOCS, f'{name} references unshipped docs/{target}')


class GgufBootstrap(unittest.TestCase):
    def test_cpu_only_cli_bypasses_engine_python_and_model_setup(self):
        with mock.patch.object(sys, 'argv', ['bootstrap_portable.py', '--gguf-only']), \
             mock.patch.object(bootstrap, 'bootstrap_gguf') as gguf, \
             mock.patch.object(bootstrap, 'bootstrap_engines', side_effect=AssertionError('engine download')), \
             mock.patch.object(bootstrap, 'download', side_effect=AssertionError('runtime download')), \
             mock.patch.object(bootstrap, 'metadata', side_effect=AssertionError('runtime metadata')):
            bootstrap.main()
        gguf.assert_called_once_with(bootstrap.ROOT/'.portable-build')

    def test_pinned_source_bootstrap_copies_only_gguf_and_license_and_records_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root/'cache'
            cache.mkdir()
            source = f'llama.cpp-{bootstrap.setup.LLAMA_CPP_COMMIT}/'
            archive = cache/f'llama-{bootstrap.setup.LLAMA_CPP_COMMIT}.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr(source + 'gguf-py/gguf/__init__.py', 'pinned package')
                package.writestr(source + 'LICENSE', 'MIT fixture')
                package.writestr(source + 'src/private-native-source.cpp', 'does not belong in minimal gguf tree')
            with mock.patch.object(bootstrap.urllib.request, 'urlopen', side_effect=AssertionError('cached source download')):
                llama = bootstrap.bootstrap_gguf(cache, root/'target')
            self.assertEqual((llama/'gguf-py/gguf/__init__.py').read_text(), 'pinned package')
            self.assertFalse((llama/'src').exists())
            self.assertEqual(json.loads((llama/'UPSTREAM.json').read_text()),
                             {'repository': 'ggml-org/llama.cpp', 'commit': bootstrap.setup.LLAMA_CPP_COMMIT})
            with mock.patch.object(bootstrap, 'download', side_effect=AssertionError('unchanged pinned source')):
                self.assertEqual(bootstrap.bootstrap_gguf(cache, root/'target'), llama)


if __name__ == '__main__':
    unittest.main()

"""Reject ambiguous hardware packages and distinguish product from engine versions."""
import copy
from pathlib import Path
import tempfile
import unittest

from tools import portable_upstream_components as components
from tools.portable_build_provenance import build_identity, engine_assets, validate_engine_build
from tools.portable_version import product_version, validate_source_release
from tools.sync_upstream import release_asset_pins


def assets():
    return [{'name': f'strata-{kind}-0.1.{51 if kind == "dsv41" else 50}-windows-x86_64-{backend}-{variant}.tar.gz',
             'digest': 'sha256:'+str(i+1)*64}
            for i, (kind, backend, variant) in enumerate(components.PROFILES.values())]


def metadata():
    found = components.component_assets(assets())
    return {'upstream_repository': 'Niko1221/Strata', 'upstream_version': '0.2.6',
            'source_version': '0.1.51', 'engine_version': '0.1.51', 'component_schema': 2,
            'upstream_release_tag': 'v0.2.6', 'upstream_commit': 'a'*40,
            'engine_components': found, 'engine_assets_sha256': release_asset_pins(assets())}


class SplitComponents(unittest.TestCase):
    def test_product_and_engine_versions_are_independent_and_bound(self):
        meta = metadata()
        identity = build_identity(meta, '0.1.51')
        self.assertEqual(identity['upstream_version'], '0.2.6')
        self.assertEqual(identity['engine_version'], '0.1.51')
        with self.assertRaises(ValueError): build_identity(meta, '0.1.50')
        with self.assertRaises(ValueError): build_identity({**meta, 'upstream_release_tag': 'v0.2.5'}, '0.1.51')
        with self.assertRaises(ValueError): validate_source_release('0.1.51', 'v0.2.6')
        self.assertEqual(len(engine_assets(meta)), 6)

    def test_missing_duplicate_wrong_backend_and_checksum_fail_closed(self):
        cases = [assets()[:-1], assets()+[assets()[0]],
                 [{**a, 'name': a['name'].replace('windows-x86_64', 'linux-x86_64')} for a in assets()],
                 [{**a, 'digest': 'md5:bad'} for a in assets()]]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                components.component_assets(value)
        meta = metadata()
        meta['engine_components']['engine']['backend'] = 'hip'
        with self.assertRaises(ValueError): build_identity(meta, '0.1.51')

    def test_vendor_version_is_not_claimed_as_self_compiled_version(self):
        meta = metadata()
        component = meta['engine_components']['engine-hip']
        build = {'version': '0.1.50', 'upstream_asset': {'repository': 'Niko1221/Strata',
                 'release_tag': 'v0.2.6', 'name': component['name'], 'sha256': component['sha256'],
                 'upstream_commit': 'a'*40}}
        validate_engine_build(build, 'engine-hip', meta, '0.1.51')
        with self.assertRaises(ValueError):
            validate_engine_build({**build, 'version': '0.1.51'}, 'engine-hip', meta, '0.1.51')
        bad = copy.deepcopy(meta)
        bad['engine_assets_sha256'][component['name']] = 'f'*64
        with self.assertRaises(ValueError): build_identity(bad, '0.1.51')

    def test_product_version_is_read_without_importing_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'CMakeLists.txt').write_text('project(strata VERSION 0.1.51)')
            self.assertEqual(product_version(root), '0.1.51')
            (root/'strata_cli').mkdir()
            (root/'strata_cli/__init__.py').write_text('raise RuntimeError("do not import")\nPRODUCT_VERSION = "0.2.6"\n')
            self.assertEqual(product_version(root), '0.2.6')


if __name__ == '__main__': unittest.main()

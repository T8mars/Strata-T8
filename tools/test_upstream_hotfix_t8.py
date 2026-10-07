"""Real Git merges across hotfix tags and a verified upstream history rewrite."""
import json
import unittest

from tools import test_sync_upstream as fixtures
from tools.sync_upstream import sync, select_ref, release_asset_pins
from tools.portable_version import upstream_release_key, upstream_version_key, version_key, validate_source_release
from tools.check_private_paths import private_paths


class HotfixSynchronization(unittest.TestCase):
    setUp = fixtures.UpstreamSync.setUp
    tearDown = fixtures.UpstreamSync.tearDown
    git = fixtures.UpstreamSync.git
    identity = fixtures.UpstreamSync.identity
    commit = fixtures.UpstreamSync.commit
    def new_release(self, version='0.1.40', tag='v0.1.40.1'):
        (self.up/'CMakeLists.txt').write_text(f'project(strata VERSION {version})')
        (self.up/'new.py').write_text('new hotfix behavior')
        self.commit(self.up, 'hotfix source')
        self.git(self.up, 'tag', tag)
        return self.git(self.up, 'rev-parse', 'HEAD')

    def test_hotfix_tag_keeps_three_part_package_and_exact_source(self):
        sha = self.new_release()
        assets = [{'name': name, 'digest': 'sha256:'+digit*64} for name, digit in
                  [('strata-windows-x64.zip', 'a'), ('strata-windows-x64-hip.zip', 'b')]]
        result = sync(self.repo, str(self.up), select_ref('release', 'v0.1.40.1'), release_assets=assets)
        meta = json.loads((self.repo/'meta.json').read_text())
        self.assertEqual(result['version'], '0.1.40-t8.1')
        self.assertEqual(meta['upstream_release_tag'], 'v0.1.40.1')
        self.assertEqual(meta['upstream_commit'], sha)
        self.assertEqual(meta['upstream_version'], meta['engine_version'])
        self.assertEqual(meta['engine_version'], '0.1.40')
        self.assertEqual(meta['engine_assets_sha256'], release_asset_pins(assets))
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        self.assertFalse(sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')['changed'])
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))

    def test_four_part_native_hotfix_advances_revision_and_retains_exact_engine(self):
        self.new_release()
        sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        (self.up/'CMakeLists.txt').write_text('project(strata VERSION 0.1.40.2)')
        self.commit(self.up, 'native hotfix')
        self.git(self.up, 'tag', 'v0.1.40.2')
        result = sync(self.repo, str(self.up), 'refs/tags/v0.1.40.2')
        meta = json.loads((self.repo/'meta.json').read_text())
        self.assertEqual(result['version'], '0.1.40-t8.2')
        self.assertEqual(meta['upstream_version'], '0.1.40.2')
        self.assertEqual(meta['engine_version'], '0.1.40.2')
        self.assertEqual(meta['upstream_release_tag'], 'v0.1.40.2')
        self.assertEqual(meta['upstream_commit'], self.git(self.up, 'rev-parse', 'HEAD'))
        self.assertFalse(sync(self.repo, str(self.up), 'refs/tags/v0.1.40.2')['changed'])

    def test_four_part_source_cannot_be_relabelled_with_another_native_hotfix_tag(self):
        self.new_release(version='0.1.40.2', tag='v0.1.40.3')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'differs'):
            sync(self.repo, str(self.up), 'refs/tags/v0.1.40.3')
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_mismatched_hotfix_base_rolls_back_all_generated_files(self):
        self.new_release(version='0.1.39')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'differs'):
            sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
        self.assertFalse((self.repo/'new.py').exists())
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_earlier_hotfix_cannot_downgrade_even_when_commit_is_an_ancestor(self):
        self.new_release()
        self.git(self.up, 'tag', 'v0.1.40')
        sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            sync(self.repo, str(self.up), 'refs/tags/v0.1.40')
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_new_hotfix_revises_package_without_reusing_previous_digest_pins(self):
        self.new_release()
        sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        meta = json.loads((self.repo/'meta.json').read_text())
        meta['engine_assets_sha256'] = {'stale': 'c'*64}
        (self.repo/'meta.json').write_text(json.dumps(meta))
        self.commit(self.repo, 'old pins')
        (self.up/'new.py').write_text('second hotfix')
        self.commit(self.up, 'second hotfix')
        self.git(self.up, 'tag', 'v0.1.40.2')
        result = sync(self.repo, str(self.up), 'refs/tags/v0.1.40.2')
        self.assertEqual(result['version'], '0.1.40-t8.2')
        self.assertNotIn('engine_assets_sha256', json.loads((self.repo/'meta.json').read_text()))

    def test_missing_asset_hash_restores_original_checkout(self):
        self.new_release()
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'missing'):
            sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1', release_assets=[])
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_noop_still_checks_official_asset_pins(self):
        self.new_release()
        assets = [{'name': name, 'digest': 'sha256:'+digit*64} for name, digit in
                  [('strata-windows-x64.zip', 'a'), ('strata-windows-x64-hip.zip', 'b')]]
        sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1', release_assets=assets)
        meta = json.loads((self.repo/'meta.json').read_text())
        meta['engine_assets_sha256']['strata-windows-x64.zip'] = 'c'*64
        (self.repo/'meta.json').write_text(json.dumps(meta))
        self.commit(self.repo, 'incorrect pin')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'SHA256 pins'):
            sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1', release_assets=assets)
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_main_preview_preserves_stable_hotfix_floor(self):
        self.new_release(tag='v0.1.40.1')
        (self.up/'new.py').write_text('second hotfix')
        self.commit(self.up, 'second hotfix')
        self.git(self.up, 'tag', 'v0.1.40.2')
        sync(self.repo, str(self.up), 'refs/tags/v0.1.40.2')
        (self.up/'new.py').write_text('later main')
        self.commit(self.up, 'main preview')
        sync(self.repo, str(self.up), 'refs/heads/main')
        meta = json.loads((self.repo/'meta.json').read_text())
        self.assertNotIn('upstream_release_tag', meta)
        self.assertEqual(meta['upstream_stable_release_tag'], 'v0.1.40.2')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))

    def test_upstream_private_path_never_enters_checkout_or_pushable_branch(self):
        for name in ('roadmap.md', 'serve/RoadMap.Md'):
            with self.subTest(name=name):
                if name != 'roadmap.md':
                    self.tearDown(); self.setUp()
                (self.repo/'.gitignore').write_text('.portable-build/\nroadmap.md\n')
                self.commit(self.repo, 'ignore local roadmap')
                path = self.up/name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('upstream planning document')
                self.commit(self.up, 'incoming roadmap')
                before = self.git(self.repo, 'rev-parse', 'HEAD')
                with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
                    sync(self.repo, str(self.up), 'refs/heads/main')
                self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
                self.assertFalse((self.repo/name).exists())
                self.assertEqual(private_paths(self.repo), [])
                report = json.loads((self.repo/'.portable-build/upstream-conflict.json').read_text())
                # The diagnostic tip is retained locally; the workflow must not push it.
                self.assertEqual(private_paths(self.repo, report['branch']), [name])
                self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_release_cannot_relabel_newer_main_preview_as_stable_source(self):
        self.new_release()
        sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        (self.up/'new.py').write_text('unreleased preview feature')
        self.commit(self.up, 'preview')
        sync(self.repo, str(self.up), 'refs/heads/main')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'stable checkout'):
            sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
        self.assertEqual((self.repo/'new.py').read_text(), 'unreleased preview feature')

    def rewrite_history(self, *, matching=True, known=True):
        old = self.git(self.up, 'rev-parse', 'HEAD')
        meta = json.loads((self.repo/'meta.json').read_text())
        if known: meta['upstream_commit'] = old
        (self.repo/'meta.json').write_text(json.dumps(meta))
        (self.repo/'.gitignore').write_text('.portable-build/\n')
        self.commit(self.repo, 'remember imported upstream')
        tree = self.git(self.up, 'rev-parse', 'HEAD^{tree}')
        if not matching:
            (self.up/'app.py').write_text('different old baseline')
            self.commit(self.up, 'alter tree')
            tree = self.git(self.up, 'rev-parse', 'HEAD^{tree}')
        rewritten = self.git(self.up, 'commit-tree', tree, '-m', 'new root')
        self.git(self.up, 'switch', '--detach', rewritten)
        self.new_release()
        return old, rewritten

    def test_exact_tree_history_rewrite_preserves_both_histories_and_fork(self):
        old, rewritten = self.rewrite_history()
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        result = sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        incoming = self.git(self.up, 'rev-parse', 'HEAD')
        for ancestor in (old, before, rewritten, incoming):
            self.git(self.repo, 'merge-base', '--is-ancestor', ancestor, 'HEAD')
        self.assertEqual(result['upstream_commit'], incoming)
        self.assertEqual((self.repo/'README.md').read_text(), 'T8 readme')
        self.assertEqual((self.repo/'portable.py').read_text(), 'T8 launcher')
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_changed_or_unknown_baseline_refuses_rewrite_and_preserves_checkout(self):
        for matching, known in ((False, True), (True, False)):
            with self.subTest(matching=matching, known=known):
                if (matching, known) != (False, True):
                    self.tearDown(); self.setUp()
                self.rewrite_history(matching=matching, known=known)
                before = self.git(self.repo, 'rev-parse', 'HEAD')
                with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
                    sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
                self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
                self.assertFalse(self.git(self.repo, 'status', '--porcelain'))
                report = json.loads((self.repo/'.portable-build/upstream-conflict.json').read_text())
                self.assertIn('manual review required', report['error'])

    def test_conflict_after_rewrite_aborts_only_owned_bridge_merge(self):
        self.rewrite_history()
        (self.up/'app.py').write_text('upstream conflict')
        self.commit(self.up, 'upstream customization')
        self.git(self.up, 'tag', '-f', 'v0.1.40.1')
        (self.repo/'app.py').write_text('fork customization')
        self.commit(self.repo, 'fork customization')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            sync(self.repo, str(self.up), 'refs/tags/v0.1.40.1')
        self.assertEqual(before, self.git(self.repo, 'rev-parse', 'HEAD'))
        self.assertEqual((self.repo/'app.py').read_text(), 'fork customization')
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))
        report = json.loads((self.repo/'.portable-build/upstream-conflict.json').read_text())
        self.assertIn('app.py', report['conflicts'])
        self.assertEqual(report['upstream_commit'], self.git(self.up, 'rev-parse', 'HEAD'))


class UpstreamVersionContract(unittest.TestCase):
    def test_four_part_source_requires_exact_release_but_old_python_hotfix_is_accepted(self):
        validate_source_release('0.1.40', 'v0.1.40.1')
        validate_source_release('0.1.40.2', 'v0.1.40.2')
        for tag in ['v0.1.40', 'v0.1.40.1', 'v0.1.40.3']:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate_source_release('0.1.40.2', tag)

    def test_upstream_source_and_tags_accept_four_segments_but_package_stays_three(self):
        self.assertEqual(upstream_release_key('0.1.40'), (0, 1, 40, 0))
        self.assertGreater(upstream_release_key('0.1.40.1'), upstream_release_key('0.1.40'))
        self.assertEqual(upstream_version_key('0.1.40.2'), (0, 1, 40, 2))
        self.assertEqual(upstream_version_key('0.1.40'), (0, 1, 40, 0))
        for bad in ('0.1.40.1.2', '0.1.40.01', '01.1.40'):
            with self.assertRaises(ValueError): upstream_version_key(bad)
        with self.assertRaises(ValueError): version_key('0.1.40.1-t8.1')
        for bad in ('v0.1.40.01', 'v0.1.40.1.2', 'v0.1.40-rc1', 'v0.1.40\n'):
            with self.assertRaises(ValueError): select_ref('release', bad)

    def test_invalid_asset_digests_never_become_pins(self):
        for assets in ([], [{'name':'strata-windows-x64.zip','digest':'md5:x'}],
                       [{'name':'strata-windows-x64.zip','digest':'sha256:'+'a'*64}]*2):
            with self.assertRaises(ValueError): release_asset_pins(assets)


if __name__ == '__main__': unittest.main()

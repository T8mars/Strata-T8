"""Sixth audit: installed bytes, delayed metadata and upstream transaction state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest

from tools import portable_version as versions
from tools import sync_upstream as upstream
from tools import test_portable_update as fixtures
from tools import test_portable_update_round4 as fourth
from tools import test_portable_update_round5 as fifth
from tools import test_sync_upstream as git_fixtures


@unittest.skipUnless(os.name == 'nt', 'actual Windows apply')
class InstalledBytes(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    plan = fourth.ApplyTopology.plan
    apply = fixtures.WindowsApply.apply
    wrapper = fifth.ApplyBoundaries.wrapper
    bind = fifth.ApplyBoundaries.bind

    def restored(self, result, root, path):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertTrue(path.exists())
        report = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertFalse(report['success'])
        self.assertEqual(report['rollback_errors'], [])

    def test_copy_time_staging_change_is_not_committed(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        body = '''function Copy-Item { param([string]$LiteralPath,[string]$Destination)
if ($LiteralPath.EndsWith('app.py')) { [IO.File]::WriteAllText($LiteralPath,'changed after preflight') }
Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination
}'''
        self.restored(self.wrapper(path, body), root, path)
        self.assertEqual((incoming/'app.py').read_text(), 'changed after preflight')
        self.assertFalse(any(p.name.startswith('.t8-') for p in root.rglob('*')))

    def test_corrupt_copy_with_same_length_is_not_committed(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        body = '''function Copy-Item { param([string]$LiteralPath,[string]$Destination)
Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination
if ($LiteralPath.EndsWith('app.py')) { [IO.File]::WriteAllText($Destination,'bad') }
}'''
        self.restored(self.wrapper(path, body), root, path)

    def test_manifest_copy_is_verified_before_committing(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        body = '''function Copy-Item { param([string]$LiteralPath,[string]$Destination)
Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination
if ($LiteralPath.EndsWith('PACKAGE-MANIFEST.json')) { [IO.File]::AppendAllText($Destination,' ') }
}'''
        self.restored(self.wrapper(path, body), root, path)

    def rebind_meta(self, path, incoming, changes, *, array=False):
        meta_path = incoming/'meta.json'
        meta = json.loads(meta_path.read_text())
        meta.update(changes)
        meta_path.write_text(json.dumps([meta] if array else meta), encoding='utf-8')
        def changed(plan, manifest):
            entry = next(e for e in manifest['files'] if e['path'] == 'meta.json')
            entry.update(size=meta_path.stat().st_size,
                         sha256=hashlib.sha256(meta_path.read_bytes()).hexdigest())
            plan['new'] = manifest['files']
        self.bind(path, incoming, changed)

    def test_delayed_apply_rejects_metadata_version_disagreement(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.rebind_meta(path, incoming, {'version': '0.1.39-t8.99'})
        self.restored(self.apply(path), root, path)
        self.assertFalse((self.base/'backup').exists())

    def test_delayed_apply_rejects_metadata_edition_disagreement(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.rebind_meta(path, incoming, {'edition': 'VisionReady-NoMainModel'})
        self.restored(self.apply(path), root, path)
        self.assertFalse((self.base/'backup').exists())

    def test_metadata_boolean_edition_is_not_coerced_into_string(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.rebind_meta(path, incoming, {'edition': True})
        self.restored(self.apply(path), root, path)
        self.assertFalse((self.base/'backup').exists())

    def test_delayed_apply_rejects_main_model_role(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.rebind_meta(path, incoming, {'weights': {'main': True, 'mtp': False, 'vision': False}})
        self.restored(self.apply(path), root, path)
        self.assertFalse((self.base/'backup').exists())

    def test_delayed_apply_rejects_numeric_boolean(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.rebind_meta(path, incoming, {'models_included': 0})
        self.restored(self.apply(path), root, path)
        self.assertFalse((self.base/'backup').exists())

    def test_single_element_metadata_array_is_not_coerced_into_object(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.rebind_meta(path, incoming, {}, array=True)
        self.restored(self.apply(path), root, path)
        self.assertFalse((self.base/'backup').exists())

    def test_single_element_plan_array_is_not_coerced_into_object(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        path.write_text('['+path.read_text()+']')
        result = self.apply(path)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertFalse((self.base/'backup').exists())

    def test_explicit_nomodels_weight_roles_remain_valid(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        roles = {'main': False, 'mtp': False, 'vision': False}
        self.rebind_meta(path, incoming, {'edition': 'Portable-NoModels', 'weights': roles,
                                         'models_included': False})
        def changed(plan, manifest):
            manifest.update(edition='Portable-NoModels', weights=roles)
        self.bind(path, incoming, changed)
        result = self.apply(path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'new')

    def test_legacy_delayed_apply_without_optional_roles_remains_valid(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        result = self.apply(path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'new')


class UpstreamTransactions(unittest.TestCase):
    git = git_fixtures.UpstreamSync.git
    identity = git_fixtures.UpstreamSync.identity
    commit = git_fixtures.UpstreamSync.commit
    tearDown = git_fixtures.UpstreamSync.tearDown

    def setUp(self):
        git_fixtures.UpstreamSync.setUp(self)
        (self.repo/'.gitignore').write_text('.portable-build/\n')
        self.commit(self.repo, 'ignore owned diagnostics')

    def advance(self, version=None):
        (self.up/'new.py').write_text('upstream feature')
        if version is not None:
            (self.up/'CMakeLists.txt').write_text(f'project(strata VERSION {version})')
        self.commit(self.up, 'upstream change')

    def snapshot(self):
        return self.git(self.repo, 'rev-parse', 'HEAD'), (self.repo/'meta.json').read_bytes()

    def preserved(self, before):
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_clean_pending_merge_is_preserved_and_refused_before_fetch(self):
        self.advance()
        self.git(self.repo, 'switch', '-c', 'user-merge')
        self.git(self.repo, 'commit', '--allow-empty', '-m', 'user empty commit')
        self.git(self.repo, 'switch', 'main')
        self.git(self.repo, 'merge', '--no-ff', '--no-commit', 'user-merge')
        marker = self.repo/'.git/MERGE_HEAD'
        original = marker.read_bytes()
        before = self.snapshot()
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))
        with self.assertRaises(RuntimeError) as refused:
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertTrue(marker.exists(), 'Synchronization must not abort the user merge')
        self.assertEqual(marker.read_bytes(), original)
        self.assertRegex(str(refused.exception), 'progress|operation')
        self.preserved(before)

    def test_clean_pending_cherry_pick_is_preserved(self):
        self.advance()
        self.git(self.repo, 'switch', '-c', 'user-pick')
        self.git(self.repo, 'commit', '--allow-empty', '-m', 'user empty commit')
        self.git(self.repo, 'switch', 'main')
        stopped = subprocess.run(['git', 'cherry-pick', 'user-pick'], cwd=self.repo,
                                 capture_output=True, text=True)
        self.assertNotEqual(stopped.returncode, 0)
        marker = self.repo/'.git/CHERRY_PICK_HEAD'
        original = marker.read_bytes()
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'progress|operation'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(marker.read_bytes(), original)
        self.preserved(before)

    def test_merge_refused_before_start_retains_ignored_file_and_reports_reason(self):
        (self.repo/'.gitignore').write_text('.portable-build/\nnew.py\n')
        self.commit(self.repo, 'ignore local data')
        self.advance()
        (self.repo/'new.py').write_text('user ignored file')
        before = self.snapshot()
        # Git otherwise silently replaces an ignored file when merging upstream.
        with self.assertRaisesRegex(RuntimeError, 'merge stopped|merge refused'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual((self.repo/'new.py').read_text(), 'user ignored file')
        self.preserved(before)
        report = json.loads((self.repo/'.portable-build/upstream-conflict.json').read_text())
        self.assertEqual(report['upstream_commit'], self.git(self.up, 'rev-parse', 'HEAD'))
        self.assertFalse((self.repo/'.git/MERGE_HEAD').exists())
        self.assertEqual(self.git(self.repo, 'rev-parse', report['branch']), self.git(self.up, 'rev-parse', 'HEAD'))

    def stale_report(self):
        report = self.repo/'.portable-build/upstream-conflict.json'
        report.parent.mkdir()
        report.write_text(json.dumps({'upstream_commit': '0'*40, 'base_commit': '1'*40,
                                     'branch': 'codex/upstream-stale', 'conflicts': ['old.py']}))
        return report

    def test_new_noop_invalidates_stale_conflict_branch_descriptor(self):
        report = self.stale_report()
        before = self.snapshot()
        self.assertFalse(upstream.sync(self.repo, str(self.up), 'refs/heads/main')['changed'])
        self.assertFalse(report.exists())
        self.preserved(before)

    def test_new_fetch_failure_cannot_leave_a_stale_conflict_descriptor(self):
        report = self.stale_report()
        before = self.snapshot()
        with self.assertRaises(subprocess.CalledProcessError):
            upstream.sync(self.repo, str(self.base/'missing-repo'), 'refs/heads/main')
        self.assertFalse(report.exists())
        self.preserved(before)

    def test_dirty_checkout_refusal_also_invalidates_stale_descriptor(self):
        report = self.stale_report()
        before = self.snapshot()
        (self.repo/'app.py').write_text('user edit')
        with self.assertRaisesRegex(RuntimeError, 'clean'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertFalse(report.exists())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.repo/'app.py').read_text(), 'user edit')

    def test_unrelated_ignored_user_file_does_not_block_merge(self):
        (self.repo/'.gitignore').write_text('.portable-build/\nlocal-data/\n')
        self.commit(self.repo, 'ignore local data')
        local = self.repo/'local-data/private.txt'
        local.parent.mkdir()
        local.write_text('user data')
        self.advance()
        self.assertTrue(upstream.sync(self.repo, str(self.up), 'refs/heads/main')['changed'])
        self.assertEqual(local.read_text(), 'user data')
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_ignored_parent_file_is_not_replaced_by_upstream_directory(self):
        (self.repo/'.gitignore').write_text('.portable-build/\nlocal-data\n')
        self.commit(self.repo, 'ignore local data')
        (self.repo/'local-data').write_text('user data')
        (self.up/'local-data').mkdir()
        (self.up/'local-data/feature.py').write_text('upstream code')
        self.commit(self.up, 'new upstream directory')
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual((self.repo/'local-data').read_text(), 'user data')
        self.preserved(before)

    def test_ignored_empty_directory_is_not_replaced_by_upstream_file(self):
        (self.repo/'.gitignore').write_text('.portable-build/\nlocal-data/\n')
        self.commit(self.repo, 'ignore local data')
        (self.repo/'local-data').mkdir()
        (self.up/'local-data').write_text('upstream file')
        self.commit(self.up, 'new upstream file')
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertTrue((self.repo/'local-data').is_dir())
        self.preserved(before)

    def test_release_tag_cannot_claim_a_different_cmake_version(self):
        self.advance('0.1.40')
        self.git(self.up, 'tag', 'v0.1.41')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'version|tag'):
            upstream.sync(self.repo, str(self.up), 'refs/tags/v0.1.41')
        self.preserved(before)
        self.assertFalse((self.repo/'README-UPSTREAM.md').exists())
        self.assertFalse((self.repo/'.git/MERGE_HEAD').exists())

    def test_main_cmake_downgrade_cannot_reduce_published_version(self):
        self.advance('0.1.38')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'version|downgrade'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.preserved(before)

    def test_matching_release_and_same_version_main_are_valid(self):
        self.advance()
        self.git(self.up, 'tag', 'v0.1.39')
        result = upstream.sync(self.repo, str(self.up), 'refs/tags/v0.1.39')
        self.assertEqual(result['version'], '0.1.39-t8.2')
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))


class StableSourceVersions(unittest.TestCase):
    def test_only_complete_stable_upstream_tags_are_selected(self):
        for tag in ('v', 'vv', 'v.', 'v1', 'v1.2', 'v1.2.3.4.5', 'v01.2.3', 'v1.2.3-beta', 1):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                upstream.select_ref('release', tag)
        self.assertEqual(upstream.select_ref('release', 'v0.1.39'), 'refs/tags/v0.1.39')
        self.assertEqual(upstream.select_ref('release', 'v0.1.40.1'), 'refs/tags/v0.1.40.1')


if __name__ == '__main__': unittest.main()

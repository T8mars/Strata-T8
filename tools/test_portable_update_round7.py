"""Seventh audit: exact file moves, delayed identities and shared Git state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from tools import portable_update as update
from tools import sync_upstream as upstream
from tools import test_portable_update as fixtures
from tools import test_portable_update_round4 as fourth
from tools import test_portable_update_round5 as fifth
from tools import test_sync_upstream as git_fixtures


@unittest.skipUnless(os.name == 'nt', 'actual Windows apply')
class ExactMoves(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    plan = fourth.ApplyTopology.plan
    apply = fixtures.WindowsApply.apply
    wrapper = fifth.ApplyBoundaries.wrapper

    def test_backup_target_directory_does_not_swallow_the_old_file(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        body = '''function New-Item { param($ItemType,$Path,[switch]$Force)
$value = Microsoft.PowerShell.Management\\New-Item -ItemType $ItemType -Path $Path -Force:$Force
if ([IO.Path]::GetFileName($Path) -eq 'backup') { [IO.Directory]::CreateDirectory((Join-Path $Path 'app.py')) | Out-Null }
return $value
}'''
        result = self.wrapper(path, body, T8_BACKUP=self.base/'backup')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertTrue((self.base/'backup/app.py').is_dir())
        self.assertFalse((self.base/'backup/app.py/app.py').exists())

    def test_backup_target_file_is_not_overwritten(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        body = '''function New-Item { param($ItemType,$Path,[switch]$Force)
$value = Microsoft.PowerShell.Management\\New-Item -ItemType $ItemType -Path $Path -Force:$Force
if ([IO.Path]::GetFileName($Path) -eq 'backup') { [IO.File]::WriteAllText((Join-Path $Path 'app.py'),'foreign backup') }
return $value
}'''
        result = self.wrapper(path, body, T8_BACKUP=self.base/'backup')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertEqual((self.base/'backup/app.py').read_text(), 'foreign backup')

    def test_rollback_destination_directory_retains_backup_and_reports_incomplete(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        body = '''function Copy-Item { param([string]$LiteralPath,[string]$Destination)
if ($LiteralPath.EndsWith('app.py')) {
  [IO.Directory]::CreateDirectory($env:T8_TARGET) | Out-Null
  [IO.File]::WriteAllText((Join-Path $env:T8_TARGET 'user.txt'),'user content')
  throw 'simulated concurrent directory and copy failure'
}
Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination
}'''
        result = self.wrapper(path, body, T8_TARGET=root/'app.py')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertTrue(report['rollback_errors'], result.stdout)
        self.assertEqual((self.base/'backup/app.py').read_text(), 'old')
        self.assertEqual((root/'app.py/user.txt').read_text(), 'user content')
        self.assertFalse((root/'app.py/app.py').exists())
        self.assertNotIn('Previous installation preserved.', result.stdout)

    def test_rollback_destination_file_is_not_overwritten(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        body = '''function Copy-Item { param([string]$LiteralPath,[string]$Destination)
if ($LiteralPath.EndsWith('app.py')) {
  [IO.File]::WriteAllText($env:T8_TARGET,'new user file')
  throw 'simulated concurrent file and copy failure'
}
Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination
}'''
        result = self.wrapper(path, body, T8_TARGET=root/'app.py')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertTrue(report['rollback_errors'], result.stdout)
        self.assertEqual((root/'app.py').read_text(), 'new user file')
        self.assertEqual((self.base/'backup/app.py').read_text(), 'old')


@unittest.skipUnless(os.name == 'nt', 'actual Windows apply')
class DelayedInstallationIdentity(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    plan = fourth.ApplyTopology.plan
    apply = fixtures.WindowsApply.apply

    def installed_version(self, root, version, edition=None):
        meta_path = root/'meta.json'
        meta = json.loads(meta_path.read_text()); meta['version'] = version
        if edition is not None: meta['edition'] = edition
        meta_path.write_text(json.dumps(meta))
        manifest_path = root/'PACKAGE-MANIFEST.json'
        manifest = json.loads(manifest_path.read_text()); manifest['version'] = version
        if edition is not None: manifest['edition'] = edition
        entry = next(e for e in manifest['files'] if e['path'] == 'meta.json')
        entry.update(size=meta_path.stat().st_size, sha256=hashlib.sha256(meta_path.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest))
        # The plan has no installed-version binding in old releases. Rebuild its
        # old entry list to isolate ordering from an unrelated entry-hash check.
        plan_path = root/'.portable-update/plan.json'
        plan = json.loads(plan_path.read_text()); plan['old'] = manifest['files']
        plan_path.write_text(json.dumps(plan))

    def refused(self, result, root):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertFalse((self.base/'backup').exists())

    def test_delayed_apply_cannot_downgrade_the_installed_version(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.installed_version(root, '0.1.39-t8.3')
        self.refused(self.apply(path), root)
        self.assertEqual(json.loads((root/'meta.json').read_text())['version'], '0.1.39-t8.3')

    def test_delayed_apply_same_version_and_edition_is_refused(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.installed_version(root, '0.1.39-t8.2')
        self.refused(self.apply(path), root)

    def test_installed_metadata_version_must_match_installed_manifest(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        (root/'meta.json').write_text(json.dumps({'version': '0.1.39-t8.99'}))
        self.refused(self.apply(path), root)

    def test_installed_metadata_edition_must_match_installed_manifest(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        meta = json.loads((root/'meta.json').read_text()); meta['edition'] = 'VisionReady-NoMainModel'
        (root/'meta.json').write_text(json.dumps(meta))
        self.refused(self.apply(path), root)

    def test_prepared_installed_manifest_binding_is_checked_at_apply(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        manifest_path = root/'PACKAGE-MANIFEST.json'
        plan = json.loads(path.read_text())
        plan['installed_manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        path.write_text(json.dumps(plan))
        manifest_path.write_bytes(manifest_path.read_bytes() + b' ')
        self.refused(self.apply(path), root)

    def test_unchanged_bound_installed_manifest_remains_valid(self):
        root, _, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        plan = json.loads(path.read_text())
        plan['installed_manifest_sha256'] = hashlib.sha256((root/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()
        path.write_text(json.dumps(plan))
        result = self.apply(path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_delayed_apply_numeric_revision_order_remains_valid(self):
        root, incoming, path = self.plan({'app.py': 'old'}, {'app.py': 'new'})
        self.installed_version(root, '0.1.39-t8.9')
        manifest = json.loads((incoming/'PACKAGE-MANIFEST.json').read_text())
        meta_path = incoming/'meta.json'; meta = json.loads(meta_path.read_text()); meta['version'] = '0.1.39-t8.10'
        meta_path.write_text(json.dumps(meta)); manifest['version'] = meta['version']
        entry = next(e for e in manifest['files'] if e['path'] == 'meta.json')
        entry.update(size=meta_path.stat().st_size, sha256=hashlib.sha256(meta_path.read_bytes()).hexdigest())
        (incoming/'PACKAGE-MANIFEST.json').write_text(json.dumps(manifest))
        plan = json.loads(path.read_text()); plan.update(new=manifest['files'], version=meta['version'],
            manifest_sha256=hashlib.sha256((incoming/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest())
        path.write_text(json.dumps(plan))
        result = self.apply(path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PrepareInstallationIdentity(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    fixture = fixtures.ReleaseValidation.fixture

    def test_prepare_binds_the_exact_installed_manifest(self):
        root, release, fetch = self.fixture()
        expected = hashlib.sha256((root/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()
        stage = self.base/'stage'; stage.mkdir()
        with mock.patch.object(update, 'fetch', side_effect=fetch), \
             mock.patch.object(update, 'running_processes', return_value=[]), \
             mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)):
            plan = update.prepare(root, release)
        self.assertEqual(plan['installed_manifest_sha256'], expected)

    def test_prepare_does_not_publish_from_a_mismatched_installed_identity(self):
        root, release, fetch = self.fixture()
        path = root/'PACKAGE-MANIFEST.json'; manifest = json.loads(path.read_text())
        manifest['version'] = '0.1.39-t8.0'; path.write_text(json.dumps(manifest))
        stage = self.base/'stage'; stage.mkdir()
        with mock.patch.object(update, 'fetch', side_effect=fetch), \
             mock.patch.object(update, 'running_processes', return_value=[]), \
             mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)):
            with self.assertRaisesRegex(ValueError, 'Installed|installed|metadata'):
                update.prepare(root, release)
        self.assertFalse((root/'.portable-update/plan.json').exists())

    def test_installation_advancing_during_download_cannot_publish_an_older_plan(self):
        root, release, fetch = self.fixture()
        def advanced(url, destination=None, limit=None):
            result = fetch(url, destination, limit)
            if destination:
                fixtures.package(root, {'app.py': 'newer', 'obsolete.py': 'old'}, '0.1.39-t8.3')
            return result
        stage = self.base/'stage'; stage.mkdir()
        with mock.patch.object(update, 'fetch', side_effect=advanced), \
             mock.patch.object(update, 'running_processes', return_value=[]), \
             mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)):
            with self.assertRaisesRegex(ValueError, 'Installed version|changed'):
                update.prepare(root, release)
        self.assertEqual((root/'app.py').read_text(), 'newer')
        self.assertFalse((root/'.portable-update/plan.json').exists())


class SharedGitTransactions(unittest.TestCase):
    git = git_fixtures.UpstreamSync.git
    identity = git_fixtures.UpstreamSync.identity
    commit = git_fixtures.UpstreamSync.commit
    tearDown = git_fixtures.UpstreamSync.tearDown

    def setUp(self):
        git_fixtures.UpstreamSync.setUp(self)
        (self.repo/'.gitignore').write_text('.portable-build/\n')
        self.commit(self.repo, 'ignore owned diagnostics')

    def advance(self):
        (self.up/'README.md').write_text('new upstream readme')
        (self.up/'new.py').write_text('upstream feature')
        self.commit(self.up, 'upstream change')
        return self.git(self.up, 'rev-parse', 'HEAD')

    def config_bytes(self):
        path = Path(self.git(self.repo, 'rev-parse', '--git-path', 'config'))
        return (path if path.is_absolute() else self.repo/path).read_bytes()

    def test_noop_does_not_add_a_persistent_shared_merge_driver(self):
        before = self.config_bytes()
        self.assertFalse(upstream.sync(self.repo, str(self.up), 'refs/heads/main')['changed'])
        self.assertEqual(self.config_bytes(), before)

    def test_linked_worktree_noop_preserves_the_shared_config(self):
        linked = self.base/'linked'
        self.git(self.repo, 'worktree', 'add', '-b', 'user-linked', str(linked), 'HEAD')
        before = self.config_bytes()
        self.assertFalse(upstream.sync(linked, str(self.up), 'refs/heads/main')['changed'])
        self.assertEqual(self.config_bytes(), before)

    def test_fetch_failure_preserves_the_users_merge_driver(self):
        self.git(self.repo, 'config', 'merge.t8-keep.driver', 'user-original-command')
        before = self.config_bytes()
        with self.assertRaises(subprocess.CalledProcessError):
            upstream.sync(self.repo, str(self.base/'missing'), 'refs/heads/main')
        self.assertEqual(self.config_bytes(), before)

    def test_success_preserves_the_users_merge_driver_and_T8_readme(self):
        self.git(self.repo, 'config', 'merge.t8-keep.driver', 'false')
        before = self.config_bytes(); self.advance()
        self.assertTrue(upstream.sync(self.repo, str(self.up), 'refs/heads/main')['changed'])
        self.assertEqual(self.config_bytes(), before)
        self.assertEqual((self.repo/'README.md').read_text(), 'T8 readme')

    def test_other_fetch_cannot_change_the_commit_selected_for_sync(self):
        other = self.base/'other'
        self.git(self.base, 'clone', str(self.up), str(other)); self.identity(other)
        (other/'other.py').write_text('another remote feature'); self.commit(other, 'other commit')
        expected = self.advance()
        original = upstream.git
        def interleaved(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if 'fetch' in args and str(self.up) in args:
                original(root, 'fetch', '--no-tags', str(other), 'refs/heads/main')
            return result
        with mock.patch.object(upstream, 'git', side_effect=interleaved):
            result = upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(result['upstream_commit'], expected)
        self.assertTrue((self.repo/'new.py').exists())
        self.assertFalse((self.repo/'other.py').exists())
        refs = self.git(self.repo, 'for-each-ref', '--format=%(refname)', 'refs/strata-t8')
        self.assertEqual(refs, '')

    def test_annotated_release_reports_the_commit_instead_of_tag_object(self):
        expected = self.advance()
        self.git(self.up, 'tag', '-a', 'v0.1.39', '-m', 'release annotation')
        result = upstream.sync(self.repo, str(self.up), 'refs/tags/v0.1.39')
        self.assertEqual(result['upstream_commit'], expected)
        self.assertEqual(json.loads((self.repo/'meta.json').read_text())['upstream_commit'], expected)
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_annotated_release_conflict_is_aborted_and_branch_points_to_commit(self):
        (self.up/'app.py').write_text('upstream conflict'); self.commit(self.up, 'upstream conflict')
        expected = self.git(self.up, 'rev-parse', 'HEAD')
        self.git(self.up, 'tag', '-a', 'v0.1.39', '-m', 'release annotation')
        (self.repo/'app.py').write_text('fork conflict'); self.commit(self.repo, 'fork conflict')
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            upstream.sync(self.repo, str(self.up), 'refs/tags/v0.1.39')
        report = json.loads((self.repo/'.portable-build/upstream-conflict.json').read_text())
        self.assertEqual(report['upstream_commit'], expected)
        self.assertEqual(self.git(self.repo, 'rev-parse', report['branch']), expected)
        self.assertFalse((self.repo/'.git/MERGE_HEAD').exists())
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_conflict_abort_precedes_a_diagnostic_path_from_upstream(self):
        (self.up/'app.py').write_text('upstream conflict')
        (self.up/'.portable-build').write_text('upstream tracked file')
        self.commit(self.up, 'conflict and diagnostic parent file')
        (self.repo/'app.py').write_text('fork conflict'); self.commit(self.repo, 'fork conflict')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertFalse((self.repo/'.git/MERGE_HEAD').exists())
        self.assertEqual((self.repo/'app.py').read_text(), 'fork conflict')
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))
        self.assertTrue((self.repo/'.portable-build/upstream-conflict.json').is_file())

    def test_diagnostic_write_failure_still_aborts_our_merge(self):
        (self.up/'app.py').write_text('upstream conflict'); self.commit(self.up, 'upstream conflict')
        (self.repo/'app.py').write_text('fork conflict'); self.commit(self.repo, 'fork conflict')
        original = Path.write_text
        def fail_report(path, *args, **kwargs):
            if path.name == 'upstream-conflict.json': raise OSError('simulated diagnostic write failure')
            return original(path, *args, **kwargs)
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with mock.patch.object(Path, 'write_text', fail_report), self.assertRaises(OSError):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertFalse((self.repo/'.git/MERGE_HEAD').exists())
        self.assertEqual((self.repo/'app.py').read_text(), 'fork conflict')
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))

    def test_replacement_user_merge_is_preserved_instead_of_aborted(self):
        self.git(self.repo, 'switch', '-c', 'user-other')
        (self.repo/'user-only.py').write_text('user branch')
        self.commit(self.repo, 'user branch change')
        user_commit = self.git(self.repo, 'rev-parse', 'HEAD')
        self.git(self.repo, 'switch', 'main')
        (self.up/'app.py').write_text('upstream conflict'); self.commit(self.up, 'upstream conflict')
        (self.repo/'app.py').write_text('fork conflict'); self.commit(self.repo, 'fork conflict')
        original = upstream.git
        def replace_merge(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if 'merge' in args and '--no-commit' in args:
                original(root, 'merge', '--abort')
                original(root, 'merge', '--no-ff', '--no-commit', 'user-other')
            return result
        with mock.patch.object(upstream, 'git', side_effect=replace_merge):
            with self.assertRaisesRegex(RuntimeError, 'Concurrent Git operation preserved'):
                upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'MERGE_HEAD'), user_commit)
        self.assertEqual((self.repo/'user-only.py').read_text(), 'user branch')
        self.assertFalse((self.repo/'.portable-build/upstream-conflict.json').exists())

    def test_conflict_report_never_reuses_a_branch_for_another_commit(self):
        (self.up/'app.py').write_text('upstream conflict'); self.commit(self.up, 'upstream conflict')
        expected = self.git(self.up, 'rev-parse', 'HEAD')
        (self.repo/'app.py').write_text('fork conflict'); self.commit(self.repo, 'fork conflict')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        existing = ['codex/upstream-'+expected[:12], 'codex/upstream-'+expected]
        for branch in existing: self.git(self.repo, 'branch', branch, before)
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        report = json.loads((self.repo/'.portable-build/upstream-conflict.json').read_text())
        self.assertEqual(self.git(self.repo, 'rev-parse', report['branch']), expected)
        for branch in existing: self.assertEqual(self.git(self.repo, 'rev-parse', branch), before)

    def test_existing_diagnostic_branch_for_the_same_commit_is_reused(self):
        (self.up/'app.py').write_text('upstream conflict'); self.commit(self.up, 'upstream conflict')
        expected = self.git(self.up, 'rev-parse', 'HEAD')
        (self.repo/'app.py').write_text('fork conflict'); self.commit(self.repo, 'fork conflict')
        self.git(self.repo, 'fetch', str(self.up), 'refs/heads/main')
        branch = 'codex/upstream-'+expected[:12]; self.git(self.repo, 'branch', branch, expected)
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'):
            upstream.sync(self.repo, str(self.up), 'refs/heads/main')
        report = json.loads((self.repo/'.portable-build/upstream-conflict.json').read_text())
        self.assertEqual(report['branch'], branch)


if __name__ == '__main__': unittest.main()

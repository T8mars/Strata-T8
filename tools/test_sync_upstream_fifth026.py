"""Interrupted local upstream synchronization preserves the correct Git state."""
import json
from pathlib import Path
import unittest
from unittest import mock

from tools import sync_upstream as sync
from tools import test_sync_upstream as fixture


class SyncCancellation(unittest.TestCase):
    git = fixture.UpstreamSync.git
    identity = fixture.UpstreamSync.identity
    commit = fixture.UpstreamSync.commit
    tearDown = fixture.UpstreamSync.tearDown

    def setUp(self):
        fixture.UpstreamSync.setUp(self)
        self.git(self.repo, 'config', 'index.threads', '1')
        self.git(self.repo, 'config', 'core.fsmonitor', 'false')
        (self.repo/'.gitignore').write_text('.portable-build/\n')
        self.commit(self.repo, 'ignore local reports')
        (self.up/'README.md').write_text('new upstream readme')
        (self.up/'new.py').write_text('new upstream code')
        self.commit(self.up, 'new upstream code')

    def snapshot(self):
        return (self.git(self.repo, 'rev-parse', 'HEAD'),
                (self.repo/'meta.json').read_bytes(),
                (self.repo/'README-UPSTREAM.md').read_bytes() if (self.repo/'README-UPSTREAM.md').exists() else None)

    def assert_restored(self, before):
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(sync.operation_path(self.repo, 'MERGE_HEAD').exists())
        self.assertEqual(self.git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual((self.repo/'README.md').read_text(), 'T8 readme')
        self.assertEqual((self.repo/'portable.py').read_text(), 'T8 launcher')
        self.assertEqual(self.git(self.repo, 'for-each-ref', '--format=%(refname)', 'refs/strata-t8'), '')

    def test_cancel_during_generation_restores_existing_documentation(self):
        (self.repo/'README-UPSTREAM.md').write_text('saved upstream documentation')
        self.commit(self.repo, 'save upstream documentation')
        before = self.snapshot()
        with mock.patch.object(sync, 'source_version', side_effect=KeyboardInterrupt('fixture cancel during generation')):
            with self.assertRaisesRegex(KeyboardInterrupt, 'during generation'):
                sync.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assert_restored(before)

    def test_cancel_before_commit_removes_only_generated_files(self):
        before = self.snapshot()
        original = sync.git
        def interrupted(root, *args, **kwargs):
            if args and args[0] == 'commit':
                raise KeyboardInterrupt('fixture cancel before commit')
            return original(root, *args, **kwargs)
        with mock.patch.object(sync, 'git', side_effect=interrupted):
            with self.assertRaisesRegex(KeyboardInterrupt, 'before commit'):
                sync.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assert_restored(before)

    def test_cancel_after_merge_process_still_aborts_our_pending_merge(self):
        before = self.snapshot()
        original = sync.git
        def interrupted(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if 'merge' in args and '--no-commit' in args:
                raise KeyboardInterrupt('fixture cancel after merge')
            return result
        with mock.patch.object(sync, 'git', side_effect=interrupted):
            with self.assertRaisesRegex(KeyboardInterrupt, 'after merge'):
                sync.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assert_restored(before)

    def test_cancel_after_successful_commit_preserves_the_completed_sync(self):
        before = self.snapshot()
        original = sync.git
        def interrupted(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if args and args[0] == 'commit':
                raise KeyboardInterrupt('fixture cancel after commit')
            return result
        with mock.patch.object(sync, 'git', side_effect=interrupted):
            with self.assertRaisesRegex(KeyboardInterrupt, 'after commit'):
                sync.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertNotEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before[0])
        self.assertFalse(sync.operation_path(self.repo, 'MERGE_HEAD').exists())
        self.assertEqual(self.git(self.repo, 'status', '--porcelain'), '')
        meta = json.loads((self.repo/'meta.json').read_text('utf-8'))
        self.assertEqual(meta['upstream_commit'], self.git(self.up, 'rev-parse', 'HEAD'))
        self.assertEqual((self.repo/'README-UPSTREAM.md').read_text().strip(), 'new upstream readme')
        self.assertEqual((self.repo/'portable.py').read_text(), 'T8 launcher')

    def test_cancel_never_aborts_a_replacement_user_merge(self):
        self.git(self.repo, 'switch', '-c', 'user-other')
        (self.repo/'user-only.py').write_text('user branch')
        self.commit(self.repo, 'user branch')
        user_commit = self.git(self.repo, 'rev-parse', 'HEAD')
        self.git(self.repo, 'switch', 'main')
        original = sync.git
        def replaced(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if 'merge' in args and '--no-commit' in args:
                original(root, 'merge', '--abort')
                original(root, 'merge', '--no-ff', '--no-commit', 'user-other')
                raise KeyboardInterrupt('fixture cancellation after user replacement')
            return result
        with mock.patch.object(sync, 'git', side_effect=replaced):
            with self.assertRaisesRegex(RuntimeError, 'Concurrent Git operation preserved'):
                sync.sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'MERGE_HEAD'), user_commit)
        self.assertEqual((self.repo/'user-only.py').read_text(), 'user branch')
        self.assertEqual((self.repo/'portable.py').read_text(), 'T8 launcher')


if __name__ == '__main__':
    unittest.main()

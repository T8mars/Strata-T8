"""Use local Git histories to validate upstream merges and conflict preservation."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.sync_upstream import sync, select_ref


class UpstreamSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.up = self.base/'upstream'
        self.up.mkdir()
        self.git(self.up, 'init', '-b', 'main')
        self.identity(self.up)
        (self.up/'CMakeLists.txt').write_text('project(strata VERSION 0.1.39)')
        (self.up/'README.md').write_text('original readme')
        (self.up/'app.py').write_text('original')
        self.commit(self.up, 'base')
        self.repo = self.base/'fork'
        self.git(self.base, 'clone', str(self.up), str(self.repo))
        self.identity(self.repo)
        (self.repo/'meta.json').write_text(json.dumps({'upstream_version': '0.1.39', 'revision': 1, 'version': '0.1.39-t8.1'}))
        (self.repo/'README.md').write_text('T8 readme')
        (self.repo/'.gitattributes').write_text('README.md merge=t8-keep\n')
        (self.repo/'portable.py').write_text('T8 launcher')
        self.commit(self.repo, 'T8 changes')
    def tearDown(self): self.tmp.cleanup()
    def git(self, root, *args):
        return subprocess.check_output(['git', *args], cwd=root, text=True, encoding='utf-8', stderr=subprocess.DEVNULL).strip()
    def identity(self, root):
        self.git(root, 'config', 'user.name', 'Test')
        self.git(root, 'config', 'user.email', 'test@example.invalid')
    def commit(self, root, message):
        self.git(root, 'add', '.')
        self.git(root, 'commit', '-m', message)

    def test_release_and_main_selection(self):
        self.assertEqual(select_ref('main'), 'refs/heads/main')
        self.assertEqual(select_ref('release', 'v0.1.39'), 'refs/tags/v0.1.39')
        with self.assertRaises(ValueError): select_ref('release', 'main;anything')
    def test_already_synced_is_noop(self):
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        self.assertFalse(sync(self.repo, str(self.up), 'refs/heads/main')['changed'])
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)
    def test_normal_merge_retains_T8_readme_and_updates_version(self):
        (self.up/'README.md').write_text('new upstream readme')
        (self.up/'CMakeLists.txt').write_text('project(strata VERSION 0.1.40)')
        (self.up/'new.py').write_text('upstream feature')
        self.commit(self.up, 'new release')
        result = sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(result['version'], '0.1.40-t8.1')
        self.assertEqual((self.repo/'README.md').read_text(), 'T8 readme')
        self.assertEqual((self.repo/'README-UPSTREAM.md').read_text().strip(), 'new upstream readme')
        self.assertEqual((self.repo/'portable.py').read_text(), 'T8 launcher')
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))
    def test_conflict_aborts_without_overwriting_fork(self):
        (self.up/'app.py').write_text('upstream conflict')
        self.commit(self.up, 'upstream conflict')
        (self.repo/'app.py').write_text('fork conflict')
        self.commit(self.repo, 'fork conflict')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        (self.repo/'.gitignore').write_text('.portable-build/\n')
        self.commit(self.repo, 'ignore diagnostics')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(RuntimeError, 'merge stopped'): sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertEqual((self.repo/'app.py').read_text(), 'fork conflict')
        report = json.loads((self.repo/'.portable-build/upstream-conflict.json').read_text())
        self.assertIn('app.py', report['conflicts'])
        self.assertEqual(self.git(self.repo, 'rev-parse', report['branch']), self.git(self.up, 'rev-parse', 'HEAD'))
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))
    def test_dirty_worktree_is_refused(self):
        (self.repo/'app.py').write_text('local edit')
        with self.assertRaisesRegex(RuntimeError, 'clean'): sync(self.repo, str(self.up), 'refs/heads/main')

    def test_generation_failure_restores_the_original_upstream_readme(self):
        (self.repo/'README-UPSTREAM.md').write_text('saved upstream documentation')
        self.commit(self.repo, 'record original upstream README')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        (self.up/'README.md').write_text('new upstream README')
        (self.up/'new.py').write_text('new code')
        self.commit(self.up, 'new upstream commit')
        with mock.patch('tools.sync_upstream.source_version', side_effect=RuntimeError('unsupported source version')):
            with self.assertRaises(RuntimeError): sync(self.repo, str(self.up), 'refs/heads/main')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertEqual((self.repo/'README-UPSTREAM.md').read_text(), 'saved upstream documentation')
        self.assertFalse(self.git(self.repo, 'status', '--porcelain'))


if __name__ == '__main__': unittest.main()

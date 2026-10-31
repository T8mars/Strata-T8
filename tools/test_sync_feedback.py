"""Keep preflight diagnostics and tested Git history without workflow-write tokens."""
import hashlib
import json
import subprocess
import unittest
from unittest import mock

from tools import sync_upstream as sync
from tools import test_sync_upstream as fixtures


class SyncFeedback(unittest.TestCase):
    setUp=fixtures.UpstreamSync.setUp
    tearDown=fixtures.UpstreamSync.tearDown
    git=fixtures.UpstreamSync.git
    identity=fixtures.UpstreamSync.identity
    commit=fixtures.UpstreamSync.commit

    def prepare(self):
        (self.repo/'.gitignore').write_text('.portable-build/\n')
        meta=json.loads((self.repo/'meta.json').read_bytes())
        meta['upstream_repository']='fixture/upstream'
        (self.repo/'meta.json').write_text(json.dumps(meta))
        self.commit(self.repo,'ignore export and record upstream')
        return self.git(self.repo,'rev-parse','HEAD')

    def test_asset_preflight_failure_replaces_stale_conflict_report(self):
        base=self.prepare()
        directory=self.repo/'.portable-build';directory.mkdir()
        (directory/'upstream-conflict.json').write_text('{"branch":"old-branch"}')
        release={'tag_name':'v0.2.9','assets':[]}
        with mock.patch.object(sync,'ROOT',self.repo),mock.patch('sys.argv',['sync_upstream.py']),mock.patch.object(sync.subprocess,'check_output',return_value=json.dumps(release)):
            with self.assertRaisesRegex(ValueError,'missing Windows components'):sync.main()
        report=json.loads((directory/'upstream-conflict.json').read_bytes())
        self.assertEqual(report['stage'],'release_assets')
        self.assertEqual(report['base_commit'],base)
        self.assertIsNone(report['branch'])
        self.assertEqual(self.git(self.repo,'rev-parse','HEAD'),base)

    def test_release_api_timeout_is_reported_without_changing_checkout(self):
        base=self.prepare()
        with mock.patch.object(sync,'ROOT',self.repo),mock.patch('sys.argv',['sync_upstream.py']),mock.patch.object(sync.subprocess,'check_output',side_effect=subprocess.TimeoutExpired('gh',60)):
            with self.assertRaises(subprocess.TimeoutExpired):sync.main()
        report=json.loads((self.repo/'.portable-build/upstream-conflict.json').read_bytes())
        self.assertEqual(report['stage'],'release_metadata')
        self.assertEqual(self.git(self.repo,'rev-parse','HEAD'),base)

    def test_workflow_candidate_bundle_imports_exact_history_without_updating_stable(self):
        base=self.prepare()
        consumer=self.base/'consumer'
        self.git(self.base,'clone',str(self.repo),str(consumer))
        workflow=self.repo/'.github/workflows/review.yml';workflow.parent.mkdir(parents=True)
        workflow.write_text('name: reviewed workflow\n')
        self.commit(self.repo,'candidate with workflow change')
        head=self.git(self.repo,'rev-parse','HEAD')
        result=sync.export_candidate(self.repo,base)
        bundle=self.repo/'.portable-build/upstream-candidate.bundle'
        self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(),result['sha256'])
        self.assertTrue(result['workflow_changes'])
        self.assertFalse(result['stable_branch_updated'])
        self.git(consumer,'fetch',str(bundle),'HEAD:refs/heads/review')
        self.assertEqual(self.git(consumer,'rev-parse','review'),head)
        self.assertEqual(self.git(consumer,'rev-parse','HEAD'),base)
        self.assertEqual(self.git(consumer,'show','review:.github/workflows/review.yml'),'name: reviewed workflow')

    def test_private_path_in_removed_history_is_not_exported(self):
        base=self.prepare()
        (self.repo/'roadmap.md').write_text('private sentinel')
        self.commit(self.repo,'private planning must never be bundled')
        (self.repo/'roadmap.md').unlink();self.commit(self.repo,'remove private planning')
        with self.assertRaisesRegex(RuntimeError,'history contains private'):sync.export_candidate(self.repo,base)
        self.assertFalse((self.repo/'.portable-build/upstream-candidate.bundle').exists())

    def test_reverted_workflow_in_candidate_history_still_requires_review(self):
        base=self.prepare()
        workflow=self.repo/'.github/workflows/review.yml';workflow.parent.mkdir(parents=True)
        workflow.write_text('name: temporary workflow\n');self.commit(self.repo,'add workflow')
        workflow.unlink();self.commit(self.repo,'remove workflow')
        self.assertFalse(self.git(self.repo,'diff','--name-only',base,'HEAD','--','.github/workflows'))
        self.assertTrue(sync.export_candidate(self.repo,base)['workflow_changes'])

    def test_dirty_candidate_or_unknown_base_is_rejected(self):
        base=self.prepare()
        with self.assertRaises(ValueError):sync.export_candidate(self.repo,'main')
        (self.repo/'app.py').write_text('uncommitted local change')
        with self.assertRaisesRegex(RuntimeError,'clean checkout'):sync.export_candidate(self.repo,base)


if __name__=='__main__':unittest.main()

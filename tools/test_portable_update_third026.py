"""Real Windows retry after a complete updater rollback."""
import json
import os
from pathlib import Path
import unittest
from unittest import mock

from tools import test_portable_update as fixture
from tools import test_portable_update_round4 as fourth
from tools import test_portable_update_round5 as fifth
from tools import portable_restore as restore
from tools import test_portable_restore_postrelease026 as restore_fixture


@unittest.skipUnless(os.name == 'nt', 'real Windows updater')
class RetryAfterRollback(unittest.TestCase):
    setUp = fixture.ReleaseValidation.setUp
    tearDown = fixture.ReleaseValidation.tearDown
    plan = fourth.ApplyTopology.plan
    apply = fixture.WindowsApply.apply
    wrapper = fifth.ApplyBoundaries.wrapper

    def owned_plan(self):
        root, incoming, path = self.plan({'nested/app.py':'old'}, {'nested/app.py':'new'})
        plan = json.loads(path.read_text(encoding='utf-8'))
        plan['stage_owner'] = 'a'*32
        path.write_text(json.dumps(plan), encoding='utf-8')
        return root, incoming, path, plan

    def test_copy_failure_complete_rollback_allows_same_plan_retry(self):
        root, _, path, plan = self.owned_plan()
        result = self.wrapper(path, "function Copy-Item { throw 'controlled initial copy failure' }")
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'nested/app.py').read_text(), 'old')
        report = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(report['rollback_errors'], [])
        self.assertTrue(path.is_file())
        retry = self.apply(path)
        self.assertEqual(retry.returncode, 0, retry.stdout+retry.stderr)
        self.assertEqual((root/'nested/app.py').read_text(), 'new')

    def test_plan_delete_failure_does_not_leave_a_successful_receipt(self):
        root, _, path, plan = self.owned_plan()
        body = '''function Remove-Item { param([string]$LiteralPath,[switch]$Force)
if ($LiteralPath -eq $env:T8_PLAN) { throw 'controlled plan deletion denied' }
Microsoft.PowerShell.Management\\Remove-Item -LiteralPath $LiteralPath -Force:$Force
}'''
        result = self.wrapper(path, body)
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'nested/app.py').read_text(), 'old')
        receipt = root/'.portable-update/history'/(plan['stage_owner']+'.json')
        self.assertFalse(receipt.exists(), 'rolled-back update still advertises a successful receipt')
        retry = self.apply(path)
        self.assertEqual(retry.returncode, 0, retry.stdout+retry.stderr)
        self.assertEqual((root/'nested/app.py').read_text(), 'new')

    def test_legacy_plan_without_receipt_can_retry_after_rollback(self):
        root, _, path = self.plan({'app.py':'old'}, {'app.py':'new'})
        first = self.wrapper(path, "function Copy-Item { throw 'controlled copy failure' }")
        self.assertNotEqual(first.returncode, 0)
        retry = self.apply(path)
        self.assertEqual(retry.returncode, 0, retry.stdout+retry.stderr)
        self.assertEqual((root/'app.py').read_text(), 'new')

    def test_backup_creation_error_after_creating_directories_can_retry(self):
        root, _, path, plan = self.owned_plan()
        body = '''function New-Item { param($ItemType,$Path,[switch]$Force)
$value = Microsoft.PowerShell.Management\\New-Item -ItemType $ItemType -Path $Path -Force:$Force
throw 'controlled post-create failure'
}'''
        first = self.wrapper(path, body, T8_BACKUP=plan['backup'])
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual((root/'nested/app.py').read_text(), 'old')
        retry = self.apply(path)
        self.assertEqual(retry.returncode, 0, retry.stdout+retry.stderr)

    def test_user_added_backup_file_or_empty_directory_is_preserved(self):
        for empty_directory in (False, True):
            with self.subTest(empty_directory=empty_directory):
                # The fixture owns exact vacant paths, so use a fresh directory
                # for each subcase rather than reusing a completed transaction.
                subcase = self.base/str(empty_directory); subcase.mkdir()
                original_base = self.base
                self.base = subcase
                try:
                    root, _, path, plan = self.owned_plan()
                    change = ("[IO.Directory]::CreateDirectory((Join-Path $env:T8_BACKUP 'user-empty')) | Out-Null"
                              if empty_directory else
                              "[IO.File]::WriteAllText((Join-Path $env:T8_BACKUP 'user.txt'),'keep user content')")
                    body = "function Copy-Item {\n"+change+"\nthrow 'controlled copy failure'\n}"
                    result = self.wrapper(path, body, T8_BACKUP=plan['backup'])
                    self.assertNotEqual(result.returncode, 0)
                    backup = Path(plan['backup'])
                    if empty_directory:
                        self.assertTrue((backup/'user-empty').is_dir())
                    else:
                        self.assertEqual((backup/'user.txt').read_text(), 'keep user content')
                    self.assertEqual((root/'nested/app.py').read_text(), 'old')
                    self.assertTrue(path.is_file())
                finally:
                    self.base = original_base

    def test_changed_receipt_is_not_deleted_during_rollback(self):
        root, _, path, plan = self.owned_plan()
        receipt = root/'.portable-update/history'/(plan['stage_owner']+'.json')
        body = '''function Remove-Item { param([string]$LiteralPath,[switch]$Force)
if ($LiteralPath -eq $env:T8_PLAN) {
  [IO.File]::WriteAllText($env:T8_RECEIPT,'user changed receipt')
  throw 'controlled plan deletion denied'
}
Microsoft.PowerShell.Management\\Remove-Item -LiteralPath $LiteralPath -Force:$Force
}'''
        result = self.wrapper(path, body, T8_RECEIPT=receipt)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt.read_text(), 'user changed receipt')
        self.assertEqual((root/'nested/app.py').read_text(), 'old')
        report = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertTrue(report['rollback_errors'])

    def test_incomplete_rollback_retains_real_old_files_and_user_target(self):
        root, _, path, plan = self.owned_plan()
        body = '''function Copy-Item {
[IO.File]::WriteAllText($env:T8_TARGET,'concurrent user file')
throw 'controlled copy failure'
}'''
        result = self.wrapper(path, body, T8_TARGET=root/'nested/app.py')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((root/'nested/app.py').read_text(), 'concurrent user file')
        self.assertEqual((Path(plan['backup'])/'nested/app.py').read_text(), 'old')
        report = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertTrue(report['rollback_errors'])

    def test_preexisting_receipt_is_never_deleted_as_this_attempts_output(self):
        root, _, path, plan = self.owned_plan()
        receipt = root/'.portable-update/history'/(plan['stage_owner']+'.json')
        receipt.parent.mkdir()
        receipt.write_text('preexisting user receipt')
        result = self.apply(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt.read_text(), 'preexisting user receipt')
        self.assertEqual((root/'nested/app.py').read_text(), 'old')


class RestoreListingIO(unittest.TestCase):
    setUp = restore_fixture.PendingRestoreStages.setUp

    def test_broken_receipt_link_does_not_hide_valid_backup(self):
        history = self.root/'.portable-update/history'
        (history/('b'*32+'.json')).symlink_to(history/'missing-receipt-target')
        result = restore.list_backups(self.root)
        self.assertEqual([row['id'] for row in result['backups']], [self.identity])
        self.assertTrue(result['backups'][0]['available'])

    def test_receipt_stat_error_does_not_hide_valid_backup(self):
        bad = self.root/'.portable-update/history'/('b'*32+'.json')
        bad.write_text('{}')
        stat = Path.stat
        def unavailable(path, *args, **kwargs):
            if path == bad:
                raise PermissionError('receipt unavailable')
            return stat(path, *args, **kwargs)
        with mock.patch.object(Path, 'stat', unavailable):
            result = restore.list_backups(self.root)
        self.assertEqual([row['id'] for row in result['backups']], [self.identity])


if __name__ == '__main__':
    unittest.main()

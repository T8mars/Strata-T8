"""Cancellation must preserve paired update controls and reclaim only unpublished stages."""
import json
from contextlib import nullcontext
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import portable_update as update
from tools import test_portable_update as fixtures
from tools import test_portable_restore_postrelease026 as restore_fixture
from tools import test_component_updates as component_fixture
from tools import portable_restore as restore
import portable_update as restore_update


class ControlPublication(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown

    def fixture(self, existing=True):
        root = self.base/'installed'; (root/'tools').mkdir(parents=True)
        (root/'tools/apply_portable_update.ps1').write_bytes(b'new verified executor')
        directory = root/'.portable-update'; directory.mkdir()
        if existing:
            (directory/'plan.json').write_bytes(b'{"previous":"verified plan"}')
            (directory/'apply.ps1').write_bytes(b'previous verified executor')
        original = {p:p.read_bytes() if p.exists() else None
                    for p in (directory/'plan.json', directory/'apply.ps1')}
        return root, directory, original

    def assert_original(self, original):
        for path, data in original.items():
            self.assertEqual(path.read_bytes() if path.exists() else None, data)

    def test_interrupt_before_descriptor_write_restores_previous_executor(self):
        root, directory, original = self.fixture()
        with mock.patch.object(update, 'atomic_json', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                update.publish_control_pair(root, directory, {'new':'plan'})
        self.assert_original(original)
        self.assertEqual(list(directory.glob('.apply-*')), [])

    def test_interrupt_after_descriptor_replace_restores_the_complete_previous_pair(self):
        root, directory, original = self.fixture()
        write = update.atomic_json
        def interrupted_after_commit(path, value):
            write(path, value)
            raise KeyboardInterrupt
        with mock.patch.object(update, 'atomic_json', side_effect=interrupted_after_commit):
            with self.assertRaises(KeyboardInterrupt):
                update.publish_control_pair(root, directory, {'new':'plan'})
        self.assert_original(original)

    def test_cancelled_first_publication_leaves_no_plan_or_executor(self):
        root, directory, original = self.fixture(existing=False)
        with mock.patch.object(update, 'atomic_json', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                update.publish_control_pair(root, directory, {'new':'plan'})
        self.assert_original(original)

    def test_io_failure_after_descriptor_commit_restores_previous_pair(self):
        root, directory, original = self.fixture()
        write = update.atomic_json
        def failed_after_commit(path, value):
            write(path, value)
            raise OSError('descriptor cleanup failed after replacement')
        with mock.patch.object(update, 'atomic_json', side_effect=failed_after_commit):
            with self.assertRaisesRegex(OSError, 'after replacement'):
                update.publish_control_pair(root, directory, {'new':'plan'})
        self.assert_original(original)


class PrepareCancellation(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    fixture = fixtures.ReleaseValidation.fixture

    def prepare(self, root, release, fetch, **patches):
        stage = self.base/'stage'; stage.mkdir()
        with mock.patch.object(update, 'fetch', side_effect=fetch), \
                mock.patch.object(update, 'running_processes', return_value=[]), \
                mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)), \
                mock.patch.multiple(update, **patches) if patches else nullcontext():
            return update.prepare(root, release)

    def test_cancelled_download_removes_only_new_unpublished_stage(self):
        root, release, fetch = self.fixture()
        (root/'portable-settings.json').write_text('user settings')
        def cancelled(url, destination=None, limit=None):
            if destination is not None:
                destination.write_bytes(b'incomplete download')
                raise KeyboardInterrupt
            return fetch(url, destination, limit)
        with self.assertRaises(KeyboardInterrupt):
            self.prepare(root, release, cancelled)
        self.assertFalse((self.base/'stage').exists())
        self.assertFalse((root/'.portable-update/plan.json').exists())
        self.assertEqual((root/'portable-settings.json').read_text(), 'user settings')
        self.assertEqual((root/'app.py').read_text(), 'old')


    def test_cancelled_publication_reclaims_new_stage_and_keeps_previous_controls(self):
        root, release, fetch = self.fixture()
        directory = root/'.portable-update'; directory.mkdir()
        (directory/'plan.json').write_text('{"previous":true}')
        (directory/'apply.ps1').write_text('previous executor')
        with self.assertRaises(KeyboardInterrupt):
            self.prepare(root, release, fetch, atomic_json=mock.Mock(side_effect=KeyboardInterrupt))
        self.assertFalse((self.base/'stage').exists())
        self.assertEqual((directory/'plan.json').read_text(), '{"previous":true}')
        self.assertEqual((directory/'apply.ps1').read_text(), 'previous executor')

    def test_post_publication_cleanup_interrupt_preserves_current_stage(self):
        root, release, fetch = self.fixture()
        with self.assertRaises(KeyboardInterrupt):
            self.prepare(root, release, fetch,
                         discard_owned_pending_stage=mock.Mock(side_effect=KeyboardInterrupt))
        plan = json.loads((root/'.portable-update/plan.json').read_text(encoding='utf-8'))
        stage = Path(plan['stage'])
        update.validate_manifest(stage, json.loads((stage/'PACKAGE-MANIFEST.json').read_bytes()))
        self.assertEqual((root/'app.py').read_text(), 'old')


class RestorePublicationCancellation(unittest.TestCase):
    setUp = restore_fixture.PendingRestoreStages.setUp
    prepare = restore_fixture.PendingRestoreStages.prepare

    def test_second_restore_cancelled_after_commit_keeps_previous_stage_and_controls(self):
        first = self.prepare()
        directory = self.root/'.portable-update'
        previous = {path:path.read_bytes() for path in (directory/'plan.json', directory/'apply.ps1')}
        write = restore_update.atomic_json
        def interrupted_after_commit(path, value):
            write(path, value)
            raise KeyboardInterrupt
        with mock.patch.object(restore_update, 'atomic_json', side_effect=interrupted_after_commit):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        for path, raw in previous.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(Path(first['stage']).is_dir())
        self.assertEqual(len(list(self.base.glob('Strata-T8-update-*'))), 1)

    def test_first_restore_cancelled_after_commit_leaves_no_pending_controls_or_stage(self):
        write = restore_update.atomic_json
        def interrupted_after_commit(path, value):
            write(path, value)
            raise KeyboardInterrupt
        with mock.patch.object(restore_update, 'atomic_json', side_effect=interrupted_after_commit):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertFalse((self.root/'.portable-update/plan.json').exists())
        self.assertFalse((self.root/'.portable-update/apply.ps1').exists())
        self.assertEqual(list(self.base.glob('Strata-T8-update-*')), [])
        self.assertTrue(self.backup.is_dir())


@unittest.skipUnless(os.name == 'nt', 'real Windows apply and restore')
class SuccessfulStageCleanup(unittest.TestCase):
    setUp = component_fixture.Components.setUp
    tearDown = component_fixture.Components.tearDown
    fixture = component_fixture.Components.fixture

    def prepare(self, root, release, fetch):
        with mock.patch.object(update, 'fetch', side_effect=fetch), \
                mock.patch.object(update, 'running_processes', return_value=[]):
            plan = update.prepare(root, release)
        owned = Path(plan['stage']).parent.resolve()
        self.assertEqual(owned.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(owned.name.startswith('Strata-T8-update-'))
        self.addCleanup(lambda: shutil.rmtree(owned) if owned.exists() else None)
        return plan

    def apply(self, root, *, added=''):
        script = root/'.portable-update/apply.ps1'
        descriptor = root/'.portable-update/plan.json'
        if added:
            wrapper = self.base/'cleanup-wrapper.ps1'
            wrapper.write_text(added+'\n& $env:T8_APPLY -PlanPath $env:T8_PLAN\nexit $LASTEXITCODE\n', encoding='utf-8')
            script = wrapper
        result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
                                  *(['-PlanPath',str(descriptor)] if not added else [])],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40,
            env={**os.environ, 'T8_APPLY':str(root/'.portable-update/apply.ps1'),'T8_PLAN':str(descriptor)})
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return result

    def test_update_restore_update_reclaims_stages_and_keeps_independent_backups(self):
        root, _, _, _, assets, _, fetch = self.fixture()
        release = {'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}
        (root/'portable-settings.json').write_text('keep settings')
        first = self.prepare(root, release, fetch)
        self.apply(root)
        self.assertFalse(Path(first['stage']).parent.exists(), 'successful update stranded the complete environment')
        self.assertTrue(Path(first['backup']).is_dir())
        choices = restore.list_backups(root)['backups']
        with mock.patch.object(restore_update, 'running_processes', return_value=[]):
            reverse = restore.prepare(root, choices[0]['id'])
        reverse_stage = Path(reverse['stage']).parent.resolve()
        self.assertEqual(reverse_stage.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(reverse_stage.name.startswith('Strata-T8-update-'))
        self.addCleanup(lambda: shutil.rmtree(reverse_stage) if reverse_stage.exists() else None)
        self.apply(root)
        self.assertFalse(reverse_stage.exists())
        self.assertEqual((root/'app.py').read_text(), 'old app')
        again = self.prepare(root, release, fetch)
        self.apply(root)
        self.assertFalse(Path(again['stage']).parent.exists())
        self.assertEqual((root/'app.py').read_text(), 'new app')
        self.assertEqual((root/'portable-settings.json').read_text(), 'keep settings')
        self.assertTrue(Path(first['backup']).is_dir())

    def test_user_added_staging_content_is_preserved_after_success(self):
        root, _, _, _, assets, _, fetch = self.fixture()
        plan = self.prepare(root, {'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}, fetch)
        private = Path(plan['stage']).parent/'user-notes.txt'
        private.write_text('user notes')
        self.apply(root)
        self.assertEqual(private.read_text(), 'user notes')
        self.assertEqual((root/'app.py').read_text(), 'new app')
        self.assertTrue(Path(plan['backup']).is_dir())

    def test_extra_empty_directory_preserves_complete_stage(self):
        root, _, _, _, assets, _, fetch = self.fixture()
        plan = self.prepare(root, {'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}, fetch)
        extra = Path(plan['stage']).parent/'private-empty-directory'; extra.mkdir()
        self.apply(root)
        self.assertTrue(extra.is_dir())
        self.assertTrue((Path(plan['stage'])/'app.py').is_file())

    def late_change(self, code):
        return '''function Remove-Item { param([string]$LiteralPath,[switch]$Force)
if ($LiteralPath.EndsWith('plan.json')) {
  $pending = Get-Content -LiteralPath $LiteralPath -Raw -Encoding UTF8 | ConvertFrom-Json
  __CHANGE__
}
Microsoft.PowerShell.Management\\Remove-Item -LiteralPath $LiteralPath -Force:$Force
}'''.replace('__CHANGE__', code)

    def test_same_length_file_changed_after_installation_preserves_stage(self):
        root, _, _, _, assets, _, fetch = self.fixture()
        plan = self.prepare(root, {'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}, fetch)
        self.apply(root, added=self.late_change("[IO.File]::WriteAllText((Join-Path $pending.stage 'app.py'),'bad app')"))
        self.assertEqual((Path(plan['stage'])/'app.py').read_text(), 'bad app')
        self.assertTrue((Path(plan['stage']).parent/'stage-owner.json').is_file())
        self.assertEqual((root/'app.py').read_text(), 'new app')

    def test_missing_success_receipt_does_not_authorize_stage_cleanup(self):
        root, _, _, _, assets, _, fetch = self.fixture()
        plan = self.prepare(root, {'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}, fetch)
        code = "Microsoft.PowerShell.Management\\Remove-Item -LiteralPath (Join-Path $pending.root ('.portable-update/history/' + $pending.stage_owner + '.json'))"
        self.apply(root, added=self.late_change(code))
        self.assertTrue((Path(plan['stage'])/'app.py').is_file())
        self.assertTrue(Path(plan['backup']).is_dir())
        self.assertEqual((root/'app.py').read_text(), 'new app')

    def test_foreign_owner_and_linked_stage_content_are_retained(self):
        for linked in (False, True):
            with self.subTest(linked=linked):
                root, _, _, _, assets, _, fetch = self.fixture()
                plan = self.prepare(root, {'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}, fetch)
                parent = Path(plan['stage']).parent
                if linked:
                    target = self.base/'user-directory'; target.mkdir(exist_ok=True)
                    (target/'keep.txt').write_text('preserve external content')
                    (parent/'linked-user-directory').symlink_to(target, target_is_directory=True)
                else:
                    (parent/'stage-owner.json').write_text(json.dumps({'root':str(self.base/'different-app'),
                                                                     'token':plan['stage_owner']}))
                self.apply(root)
                self.assertTrue((Path(plan['stage'])/'app.py').is_file())
                if linked:
                    self.assertEqual((target/'keep.txt').read_text(), 'preserve external content')

    def test_cleanup_lock_does_not_roll_back_completed_update(self):
        root, _, _, _, assets, _, fetch = self.fixture()
        plan = self.prepare(root, {'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}, fetch)
        hook = '''function Remove-Item { param([string]$LiteralPath,[switch]$Force)
if ($LiteralPath.EndsWith('plan.json')) {
  $plan = Get-Content -LiteralPath $LiteralPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $global:cleanupLock = [IO.File]::Open((Join-Path $plan.stage 'app.py'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
}
Microsoft.PowerShell.Management\\Remove-Item -LiteralPath $LiteralPath -Force:$Force
}'''
        self.apply(root, added=hook)
        self.assertEqual((root/'app.py').read_text(), 'new app')
        self.assertTrue(Path(plan['stage']).is_dir())
        self.assertTrue(Path(plan['backup']).is_dir())
        result = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertTrue(result['success'])


if __name__ == '__main__':
    unittest.main()

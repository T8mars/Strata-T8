"""Repeated rollback preparation must not strand complete runtime copies."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import portable_restore as restore
from tools.test_portable_update import package, SOURCE
import portable_update as update


class PendingRestoreStages(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='Strata restore audit ')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.base/'appdata')})
        environment.start(); self.addCleanup(environment.stop)
        temporary_root = mock.patch.object(tempfile, 'gettempdir', return_value=str(self.base))
        temporary_root.start(); self.addCleanup(temporary_root.stop)
        self.root = self.base/'installed'
        executor = (SOURCE/'tools/apply_portable_update.ps1').read_text(encoding='utf-8')
        package(self.root, {'app.py': 'current', 'tools/apply_portable_update.ps1': executor}, '0.2.6-t8.1')
        self.identity = 'a'*32
        self.backup = restore.backup_destination(self.root, self.identity)
        package(self.backup, {'app.py': 'previous', 'tools/apply_portable_update.ps1': executor}, '0.1.45-t8.1')
        history = self.root/'.portable-update/history'; history.mkdir(parents=True)
        receipt = {'id': self.identity, 'root': str(self.root), 'backup': str(self.backup),
                   'from_version': '0.1.45-t8.1', 'to_version': '0.2.6-t8.1',
                   'target_manifest_sha256': hashlib.sha256((self.backup/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest(),
                   'installed_manifest_sha256': hashlib.sha256((self.root/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()}
        (history/(self.identity+'.json')).write_text(json.dumps(receipt), encoding='utf-8')

    def prepare(self):
        with mock.patch.object(update, 'running_processes', return_value=[]):
            return restore.prepare(self.root, self.identity)

    def test_repeated_prepare_reclaims_only_superseded_owned_stage(self):
        first = self.prepare()
        previous_stage = Path(first['stage']).parent
        second = self.prepare()
        self.assertNotEqual(first['stage'], second['stage'])
        self.assertFalse(previous_stage.exists(), 'superseded full runtime copy was stranded')
        self.assertTrue(Path(second['stage']).is_dir())
        self.assertTrue(self.backup.is_dir())
        self.assertEqual((self.root/'app.py').read_text(), 'current')

    def test_stage_with_user_content_is_retained(self):
        first = self.prepare()
        previous_stage = Path(first['stage']).parent
        private = previous_stage/'notes.txt'; private.write_text('keep me')
        self.prepare()
        self.assertEqual(private.read_text(), 'keep me')

    def test_stage_with_existing_apply_backup_is_retained(self):
        first = self.prepare()
        backup = Path(first['backup']); backup.mkdir(parents=True)
        (backup/'recovery.txt').write_text('unfinished apply recovery')
        self.prepare()
        self.assertTrue(Path(first['stage']).is_dir())
        self.assertEqual((backup/'recovery.txt').read_text(), 'unfinished apply recovery')

    def test_foreign_owner_marker_does_not_authorize_stage_removal(self):
        first = self.prepare()
        marker = Path(first['stage']).parent/'stage-owner.json'
        marker.write_text(json.dumps({'root': str(self.base/'another app'), 'token': first['stage_owner']}))
        self.prepare()
        self.assertTrue(Path(first['stage']).is_dir())

    def test_malformed_pending_descriptor_can_be_replaced_without_removing_stage(self):
        first = self.prepare()
        (self.root/'.portable-update/plan.json').write_text('{broken')
        second = self.prepare()
        self.assertTrue(Path(first['stage']).is_dir())
        self.assertTrue(Path(second['stage']).is_dir())

    def test_linked_pending_descriptor_is_rejected_before_staging(self):
        target = self.base/'foreign-plan.json'; target.write_text('unrelated user file')
        descriptor = self.root/'.portable-update/plan.json'
        descriptor.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'Linked'):
            self.prepare()
        self.assertEqual(target.read_text(), 'unrelated user file')
        self.assertEqual(list(self.base.glob('Strata-T8-update-*')), [])

    def test_failed_second_publication_retains_previous_plan_and_stage(self):
        first = self.prepare()
        descriptor = self.root/'.portable-update/plan.json'
        previous = descriptor.read_bytes()
        with mock.patch.object(update, 'publish_control_pair', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.prepare()
        self.assertEqual(descriptor.read_bytes(), previous)
        self.assertTrue(Path(first['stage']).is_dir())
        self.assertEqual(len(list(self.base.glob('Strata-T8-update-*'))), 1)

    def test_interrupted_cleanup_does_not_remove_newly_published_stage(self):
        self.prepare()
        with mock.patch.object(update, 'discard_owned_pending_stage', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        plan = json.loads((self.root/'.portable-update/plan.json').read_bytes())
        incoming = Path(plan['stage'])
        manifest = json.loads((incoming/'PACKAGE-MANIFEST.json').read_bytes())
        update.validate_manifest(incoming, manifest)


if __name__ == '__main__':
    unittest.main()

"""Fourth audit: update identity, transaction reporting and file topology."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock
import zipfile

from tools import portable_update as update
from tools import test_portable_update as fixtures


class UpdateMetadata(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown

    def test_metadata_edition_cannot_disagree_with_manifest(self):
        root = self.base/'incoming'
        manifest = fixtures.package(root, {'app.py': 'new'})
        meta = {'version': manifest['version'], 'edition': 'VisionReady-NoMainModel',
                'weights': {'main': False, 'mtp': False, 'vision': True}, 'models_included': True}
        (root/'meta.json').write_text(json.dumps(meta), encoding='utf-8')
        for entry in manifest['files']:
            if entry['path'] == 'meta.json':
                entry.update(size=(root/'meta.json').stat().st_size,
                             sha256=hashlib.sha256((root/'meta.json').read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'metadata.*edition|edition.*metadata'):
            update.validate_manifest(root, manifest)

    def test_metadata_weight_roles_and_included_flag_must_match(self):
        for change in ({'weights': {'main': True, 'mtp': False, 'vision': False}},
                       {'models_included': True}, {'weights': {'main': 0, 'mtp': False, 'vision': False}}):
            with self.subTest(change=change):
                root = self.base/str(len(list(self.base.iterdir())))
                manifest = fixtures.package(root, {})
                (root/'meta.json').write_text(json.dumps({'version': manifest['version'], **change}))
                entry = manifest['files'][0]
                entry.update(size=(root/'meta.json').stat().st_size,
                             sha256=hashlib.sha256((root/'meta.json').read_bytes()).hexdigest())
                with self.assertRaisesRegex(ValueError, 'metadata'):
                    update.validate_manifest(root, manifest)

    def test_legacy_metadata_without_weight_fields_is_supported(self):
        root = self.base/'incoming'
        manifest = fixtures.package(root, {'app.py': 'new'})
        update.validate_manifest(root, manifest)


class UpdateZipTopology(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown

    def reject(self, members):
        archive = self.base/'release.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            for name, content in members:
                stream.writestr(name, content)
        destination = self.base/'extract'
        destination.mkdir()
        with self.assertRaises(ValueError):
            update.extract_release(archive, destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_zip_root_file_is_rejected_before_writing_first_member(self):
        self.reject([('app/early.py', 'early'), ('app', 'unexpected root file')])

    def test_zip_parent_file_conflict_is_rejected_before_writing(self):
        self.reject([('app/early.py', 'early'), ('app/tools', 'file'), ('app/tools/late.py', 'late')])

    def test_missing_manifest_is_rejected_before_writing(self):
        self.reject([('app/early.py', 'early')])

    def test_noncanonical_root_is_rejected_before_writing(self):
        self.reject([('app/early.py', 'early'), ('./app/late.py', 'late')])


class UpdateControlPair(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    fixture = fixtures.ReleaseValidation.fixture

    def test_failed_plan_publication_retains_previous_apply_and_plan(self):
        root, release, fetch = self.fixture()
        control = root/'.portable-update'; control.mkdir()
        previous_stage = self.base/'previous-stage'
        previous_manifest = fixtures.package(previous_stage, {'app.py':'previous new'})
        previous_plan = json.dumps({'root':str(root), 'stage':str(previous_stage),
                                    'backup':str(self.base/'previous-backup'),
                                    'old':json.loads((root/'PACKAGE-MANIFEST.json').read_text())['files'],
                                    'new':previous_manifest['files'], 'version':previous_manifest['version'],
                                    'manifest_sha256':hashlib.sha256((previous_stage/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()}).encode('utf-8')
        previous_apply = (fixtures.SOURCE/'tools/apply_portable_update.ps1').read_bytes()+b'\n# previous verified executor\n'
        (control/'plan.json').write_bytes(previous_plan)
        (control/'apply.ps1').write_bytes(previous_apply)
        stage = self.base/'stage'; stage.mkdir()
        with mock.patch.object(update, 'fetch', side_effect=fetch), \
             mock.patch.object(update, 'running_processes', return_value=[]), \
             mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)), \
             mock.patch.object(update, 'atomic_json', side_effect=OSError('plan destination locked')):
            with self.assertRaises(OSError): update.prepare(root, release)
        self.assertEqual((control/'plan.json').read_bytes(), previous_plan)
        self.assertEqual((control/'apply.ps1').read_bytes(), previous_apply)
        self.assertFalse(stage.exists())
        self.assertEqual({p.name for p in control.iterdir()}, {'plan.json', 'apply.ps1'})
        if os.name == 'nt':
            result = subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(control/'apply.ps1'),
                                     '-PlanPath',str(control/'plan.json')], capture_output=True,text=True,errors='replace',timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertEqual((root/'app.py').read_text(), 'previous new')

    def test_partial_apply_copy_failure_retains_previous_executor(self):
        root, release, fetch = self.fixture()
        control = root/'.portable-update'; control.mkdir()
        previous = b'# previous complete executor\n'
        (control/'apply.ps1').write_bytes(previous)
        (control/'plan.json').write_text('{"previous":true}')
        stage = self.base/'stage'; stage.mkdir()
        def partial(source, target):
            Path(target).write_bytes(b'# partial copy')
            raise OSError('copy interrupted')
        with mock.patch.object(update, 'fetch', side_effect=fetch), \
             mock.patch.object(update, 'running_processes', return_value=[]), \
             mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)), \
             mock.patch.object(update.shutil, 'copy2', side_effect=partial):
            with self.assertRaises(OSError): update.prepare(root, release)
        self.assertEqual((control/'apply.ps1').read_bytes(), previous)
        self.assertFalse(stage.exists())


class PrepareTopology(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown

    def prepare(self, old_files, new_files, user=None):
        root = self.base/'installed'; incoming = self.base/'source'
        fixtures.package(root, old_files, '0.1.39-t8.1')
        fixtures.package(incoming, new_files)
        (root/'tools').mkdir(exist_ok=True)
        shutil.copy2(fixtures.SOURCE/'tools/apply_portable_update.ps1', root/'tools/apply_portable_update.ps1')
        if user:
            for name, value in user.items():
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as stream:
            for file in incoming.rglob('*'):
                if file.is_file(): stream.write(file, 'app/'+file.relative_to(incoming).as_posix())
        content = payload.getvalue(); name = fixtures.archive_name('0.1.39-t8.2')
        release = {'tag_name': 'v0.1.39-t8.2', 'assets': [
            {'name': name, 'size': len(content), 'browser_download_url': 'https://github.com/zip'},
            {'name': name+'.sha256', 'browser_download_url': 'https://github.com/hash'}]}
        def fetch(url, destination=None, limit=None):
            if destination:
                destination.write_bytes(content); return len(content)
            return f'{hashlib.sha256(content).hexdigest()}  {name}'.encode('ascii')
        stage = self.base/'stage'; stage.mkdir()
        with mock.patch.object(update, 'fetch', side_effect=fetch), \
             mock.patch.object(update, 'running_processes', return_value=[]), \
             mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)):
            return update.prepare(root, release)

    def test_prepare_managed_directory_to_file(self):
        self.assertIsNotNone(self.prepare({'engine/bin.exe': 'old'}, {'engine':'new'}))

    def test_prepare_managed_file_to_directory(self):
        self.assertIsNotNone(self.prepare({'engine':'old'}, {'engine/bin.exe':'new'}))

    def test_prepare_directory_to_file_keeps_user_file(self):
        with self.assertRaisesRegex(ValueError, 'user file'):
            self.prepare({'engine/bin.exe':'old'}, {'engine':'new'}, {'engine/user.txt':'user'})
        self.assertEqual((self.base/'installed/engine/user.txt').read_text(), 'user')
        self.assertFalse((self.base/'stage').exists())

    def test_prepare_unmanaged_file_parent_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'user file parent'):
            self.prepare({'app.py':'old'}, {'engine/bin.exe':'new'}, {'engine':'user'})
        self.assertEqual((self.base/'installed/engine').read_text(), 'user')
        self.assertFalse((self.base/'stage').exists())


@unittest.skipUnless(os.name == 'nt', 'real Windows updater')
class ApplyTopology(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    apply = fixtures.WindowsApply.apply

    def plan(self, old_files, new_files):
        root = self.base/'installed'; incoming = self.base/'incoming'
        old = fixtures.package(root, old_files, '0.1.39-t8.1')
        new = fixtures.package(incoming, new_files)
        control = root/'.portable-update'; control.mkdir()
        path = control/'plan.json'
        path.write_text(json.dumps({'root': str(root), 'stage': str(incoming),
                                   'backup': str(self.base/'backup'), 'old': old['files'], 'new': new['files'],
                                   'version': new['version'], 'manifest_sha256': hashlib.sha256(
                                       (incoming/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()}))
        return root, incoming, path

    def test_managed_file_can_become_directory(self):
        root, _, path = self.plan({'app.py':'old', 'engine':'old engine'},
                                  {'app.py':'new', 'engine/bin.exe':'new engine'})
        result = self.apply(path)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'engine/bin.exe').read_text(), 'new engine')
        self.assertEqual((self.base/'backup/engine').read_text(), 'old engine')

    def test_managed_directory_can_become_file(self):
        root, _, path = self.plan({'app.py':'old', 'engine/sub/bin.exe':'old engine'},
                                  {'app.py':'new', 'engine':'new engine'})
        result = self.apply(path)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'engine').read_text(), 'new engine')
        self.assertEqual((self.base/'backup/engine/sub/bin.exe').read_text(), 'old engine')

    def test_directory_with_user_file_cannot_become_file(self):
        root, _, path = self.plan({'app.py':'old', 'engine/bin.exe':'old engine'},
                                  {'app.py':'new', 'engine':'new engine'})
        (root/'engine/user.txt').write_text('user file')
        result = self.apply(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.base/'backup').exists())
        self.assertEqual((root/'engine/user.txt').read_text(), 'user file')
        self.assertEqual((root/'app.py').read_text(), 'old')

    def test_directory_with_user_empty_directory_cannot_become_file(self):
        root, _, path = self.plan({'engine/bin.exe':'old engine'}, {'engine':'new engine'})
        (root/'engine/user-empty').mkdir()
        result = self.apply(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((root/'engine/user-empty').is_dir())
        self.assertFalse((self.base/'backup').exists())

    def test_topology_change_rolls_back_when_result_write_is_locked(self):
        root, _, path = self.plan({'engine/bin.exe':'old engine'}, {'engine':'new engine'})
        result_path = root/'.portable-update/result.json'; result_path.write_text('previous report')
        wrapper = self.base/'locked-result.ps1'
        wrapper.write_text('$lock = [IO.File]::Open($env:T8_RESULT_PATH, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)\n& $env:T8_APPLY_SCRIPT -PlanPath $env:T8_PLAN\nexit $LASTEXITCODE\n')
        env = dict(os.environ, T8_RESULT_PATH=str(result_path), T8_APPLY_SCRIPT=str(fixtures.SOURCE/'tools/apply_portable_update.ps1'), T8_PLAN=str(path))
        result = subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                                env=env,capture_output=True,text=True,errors='replace',timeout=45)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((root/'engine/bin.exe').read_text(), 'old engine')
        self.assertIn('Could not write update result', result.stdout)
        self.assertIn('Previous installation preserved.', result.stdout)
        self.assertEqual(result_path.read_text(), 'previous report')
        self.assertEqual({p.name for p in result_path.parent.iterdir()}, {'plan.json','result.json'})

    def test_result_write_failure_reports_completed_rollback(self):
        root, _, path = self.plan({'app.py':'old'}, {'app.py':'new'})
        result_path = root/'.portable-update/result.json'; result_path.write_text('previous report')
        wrapper = self.base/'locked-result.ps1'
        wrapper.write_text('$lock = [IO.File]::Open($env:T8_RESULT_PATH, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)\n& $env:T8_APPLY_SCRIPT -PlanPath $env:T8_PLAN\nexit $LASTEXITCODE\n')
        env = dict(os.environ, T8_RESULT_PATH=str(result_path), T8_APPLY_SCRIPT=str(fixtures.SOURCE/'tools/apply_portable_update.ps1'), T8_PLAN=str(path))
        result = subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                                env=env,capture_output=True,text=True,errors='replace',timeout=45)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertIn('Could not write update result', result.stdout)
        self.assertIn('Previous installation preserved.', result.stdout)
        self.assertEqual(result_path.read_text(), 'previous report')

    def test_copied_plan_cannot_apply_a_different_installation(self):
        root, _, path = self.plan({'app.py':'old'}, {'app.py':'new'})
        copied = self.base/'copied/plan.json'; copied.parent.mkdir(); shutil.copy2(path, copied)
        result = self.apply(copied)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Plan path differs from installation', result.stdout)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertTrue(copied.exists())
        self.assertFalse((self.base/'backup').exists())

    def test_topology_file_to_directory_rolls_back_when_new_copy_fails(self):
        root, incoming, path = self.plan({'engine':'old engine'}, {'engine/bin.exe':'new engine'})
        wrapper = self.base/'failed-copy.ps1'
        wrapper.write_text('function Copy-Item { throw "simulated new-file copy failure" }\n& $env:T8_APPLY_SCRIPT -PlanPath $env:T8_PLAN\nexit $LASTEXITCODE\n')
        env = dict(os.environ, T8_APPLY_SCRIPT=str(fixtures.SOURCE/'tools/apply_portable_update.ps1'), T8_PLAN=str(path))
        result = subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                                env=env,capture_output=True,text=True,errors='replace',timeout=45)
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'engine').read_text(), 'old engine')
        self.assertIn('Previous installation preserved.', result.stdout)
        report = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertFalse(report['success']); self.assertEqual(report['rollback_errors'], [])

    def test_two_equal_length_created_directories_are_both_removed_on_rollback(self):
        root, _, path = self.plan({'a':'old a', 'b':'old b'}, {'a/new.py':'new a', 'b/new.py':'new b'})
        wrapper = self.base/'failed-manifest-copy.ps1'
        wrapper.write_text('function Copy-Item { param([string]$LiteralPath, [string]$Destination)\nif ($LiteralPath.EndsWith("PACKAGE-MANIFEST.json")) { throw "simulated manifest copy failure" }\nMicrosoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination\n}\n& $env:T8_APPLY_SCRIPT -PlanPath $env:T8_PLAN\nexit $LASTEXITCODE\n')
        env = dict(os.environ, T8_APPLY_SCRIPT=str(fixtures.SOURCE/'tools/apply_portable_update.ps1'), T8_PLAN=str(path))
        result = subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                                env=env,capture_output=True,text=True,errors='replace',timeout=45)
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'a').read_text(), 'old a'); self.assertEqual((root/'b').read_text(), 'old b')
        report = json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertFalse(report['success']); self.assertEqual(report['rollback_errors'], [])


if __name__ == '__main__': unittest.main()

"""Release staging and real Windows file replacement/rollback, without downloads."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import portable_update as update
from tools.portable_version import archive_name, version_key

SOURCE = Path(__file__).resolve().parents[1]


def package(root, files, version='0.1.39-t8.2'):
    root.mkdir(parents=True, exist_ok=True)
    files = dict(files, **{'meta.json': json.dumps({'version': version, 'repository': 'T8mars/Strata-T8'})})
    entries = []
    for name, content in files.items():
        file = root/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding='utf-8')
        entries.append({'path': name, 'size': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    manifest = {'version': version, 'models_included': False, 'files': entries}
    (root/'PACKAGE-MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


class ReleaseValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='Strata update 中文 ')
        self.base = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()

    def test_version_order_and_stable_only(self):
        self.assertGreater(version_key('v0.1.40-t8.1'), version_key('0.1.39-t8.9'))
        self.assertGreater(version_key('0.1.39-t8.10'), version_key('0.1.39-t8.2'))
        for version in ('nightly', 'v0.1.39', '0.1.39-t8.1-beta'):
            with self.assertRaises(ValueError): version_key(version)

    def test_paths_cannot_touch_user_data_or_escape(self):
        for path in ('../outside', 'tools/../../outside', 'C:/x', '/x', 'a\\b', 'logs/x', 'models/x', 'mtp/x', 'Strata-data/x', 'portable-settings.json', 'strata-iq3_s.json', 'NUL.txt', 'a./x', 'a//b'):
            with self.subTest(path=path), self.assertRaises(ValueError): update.safe_path(self.base, path)

    def test_checksum_missing_duplicate_and_extra_files_rejected(self):
        root = self.base/'incoming'
        manifest = package(root, {'app.py': 'new'})
        update.validate_manifest(root, manifest)
        (root/'app.py').write_text('bad', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'checksum'): update.validate_manifest(root, manifest)
        (root/'app.py').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'): update.validate_manifest(root, manifest)
        manifest = package(root, {'app.py': 'new'})
        (root/'unlisted').write_text('unlisted')
        with self.assertRaisesRegex(ValueError, 'unlisted'): update.validate_manifest(root, manifest)
        (root/'unlisted').unlink()
        manifest['files'].append(dict(manifest['files'][0], path='APP.py'))
        with self.assertRaisesRegex(ValueError, 'Duplicate'): update.validate_manifest(root, manifest)

    def test_weight_file_rejected(self):
        root = self.base/'incoming'
        manifest = package(root, {'accidental.gguf': 'weight'})
        with self.assertRaisesRegex(ValueError, 'Model'): update.validate_manifest(root, manifest)

    def test_zip_traversal_and_symlink_rejected(self):
        for name, islink in [('app/../outside', False), ('app/tools/link', True)]:
            archive = self.base/'bad.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                info = zipfile.ZipInfo(name)
                if islink: info.external_attr = 0o120777 << 16
                z.writestr(info, 'outside')
            with self.assertRaises(ValueError): update.extract_release(archive, self.base/'extract')
        self.assertFalse((self.base/'outside').exists())

    def fixture(self):
        root = self.base/'installed'
        package(root, {'app.py': 'old', 'obsolete.py': 'old'}, '0.1.39-t8.1')
        (root/'tools').mkdir()
        shutil.copy2(SOURCE/'tools/apply_portable_update.ps1', root/'tools/apply_portable_update.ps1')
        incoming = self.base/'source'
        package(incoming, {'app.py': 'new'})
        zipbytes = io.BytesIO()
        with zipfile.ZipFile(zipbytes, 'w') as z:
            for file in incoming.rglob('*'):
                if file.is_file(): z.write(file, 'app/'+file.relative_to(incoming).as_posix())
        payload = zipbytes.getvalue()
        name = archive_name('0.1.39-t8.2')
        release = {'tag_name': 'v0.1.39-t8.2', 'assets': [{'name': name, 'size': len(payload), 'browser_download_url': 'https://github.com/zip'}, {'name': name+'.sha256', 'browser_download_url': 'https://github.com/hash'}]}
        def fetch(url, destination=None, limit=None):
            if destination:
                destination.write_bytes(payload)
                return len(payload)
            return f'{hashlib.sha256(payload).hexdigest()}  {name}'.encode('ascii')
        return root, release, fetch

    def test_prepare_real_zip_and_preserve_configuration(self):
        root, release, fetch = self.fixture()
        (root/'portable-settings.json').write_text('my config')
        with mock.patch.object(update, 'fetch', side_effect=fetch), mock.patch.object(update, 'running_processes', return_value=[]), mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(self.base/'stage')):
            (self.base/'stage').mkdir()
            plan = update.prepare(root, release)
        self.assertTrue((root/'.portable-update/plan.json').is_file())
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertEqual((root/'portable-settings.json').read_text(), 'my config')
        self.assertEqual(plan['version'], '0.1.39-t8.2')

    def test_prepare_refuses_busy_git_and_custom_file_collision(self):
        root, release, fetch = self.fixture()
        with mock.patch.object(update, 'running_processes', return_value=[123]):
            with self.assertRaisesRegex(RuntimeError, 'Exit'): update.prepare(root, release)
        (root/'.git').mkdir()
        with self.assertRaisesRegex(RuntimeError, 'Git checkout'): update.prepare(root, release)
        (root/'.git').rmdir()
        old = json.loads((root/'PACKAGE-MANIFEST.json').read_text())
        old['files'] = [e for e in old['files'] if e['path'] != 'app.py']
        (root/'PACKAGE-MANIFEST.json').write_text(json.dumps(old))
        (self.base/'stage').mkdir()
        with mock.patch.object(update, 'fetch', side_effect=fetch), mock.patch.object(update, 'running_processes', return_value=[]), mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(self.base/'stage')):
            with self.assertRaisesRegex(ValueError, 'user file'): update.prepare(root, release)

    def test_archive_checksum_failure_cannot_prepare_plan(self):
        root, release, fetch = self.fixture()
        def corrupted(url, destination=None, limit=None):
            if not destination: return (('0'*64)+'  '+archive_name('0.1.39-t8.2')).encode()
            return fetch(url, destination, limit)
        (self.base/'stage').mkdir()
        with mock.patch.object(update, 'fetch', side_effect=corrupted), mock.patch.object(update, 'running_processes', return_value=[]), mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(self.base/'stage')):
            with self.assertRaisesRegex(ValueError, 'SHA256'): update.prepare(root, release)
        self.assertFalse((root/'.portable-update/plan.json').exists())

    def test_offline_check_is_nonfatal(self):
        root, _, _ = self.fixture()
        with mock.patch.object(update, 'latest_release', side_effect=TimeoutError):
            self.assertIsNone(update.check_update(root))

    def test_windows_pseudo_process_is_not_application_process(self):
        pseudo = mock.Mock(pid=424, info={'exe': 'Registry'})
        real = mock.Mock(pid=999, info={'exe': str(self.base/'runtime/python.exe')})
        with mock.patch('psutil.process_iter', return_value=[pseudo, real]):
            self.assertEqual(update.running_processes(self.base), [999])


@unittest.skipUnless(os.name == 'nt', 'real PowerShell replacement needs Windows')
class WindowsApply(unittest.TestCase):
    setUp = ReleaseValidation.setUp
    tearDown = ReleaseValidation.tearDown
    def plan(self, extra=None):
        root = self.base/'installed'
        old = package(root, {'app.py': 'old', 'obsolete.py': 'old'}, '0.1.39-t8.1')
        incoming = self.base/'stage'
        new = package(incoming, {'app.py': 'new', **(extra or {})})
        (root/'.portable-update').mkdir()
        for name, content in {'portable-settings.json': 'config', 'strata-iq3_s.json': 'settings', 'Strata-data/model.gguf': 'model', 'logs/user.log': 'log'}.items():
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        plan = {'root': str(root), 'stage': str(incoming), 'backup': str(self.base/'backup'), 'new': new['files'], 'old': old['files'], 'version': new['version']}
        path = root/'.portable-update/plan.json'
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        return root, incoming, path

    def apply(self, path):
        return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SOURCE/'tools/apply_portable_update.ps1'), '-PlanPath', str(path)], capture_output=True, text=True, errors='replace', timeout=45)

    def test_real_apply_preserves_data_and_removes_obsolete_files(self):
        root, _, path = self.plan()
        result = self.apply(path)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'new')
        self.assertFalse((root/'obsolete.py').exists())
        self.assertEqual((root/'portable-settings.json').read_text(), 'config')
        self.assertEqual((root/'strata-iq3_s.json').read_text(), 'settings')
        self.assertEqual((root/'Strata-data/model.gguf').read_text(), 'model')
        self.assertEqual((root/'logs/user.log').read_text(), 'log')
        self.assertEqual((self.base/'backup/app.py').read_text(), 'old')
        self.assertTrue(json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))['success'])

    def test_failure_after_first_replacement_rolls_back(self):
        root, _, path = self.plan({'blocked.py': 'new'})
        (root/'blocked.py').mkdir()
        result = self.apply(path)
        self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertEqual((root/'obsolete.py').read_text(), 'old')
        self.assertTrue((root/'blocked.py').is_dir())
        self.assertEqual(json.loads((root/'PACKAGE-MANIFEST.json').read_text())['version'], '0.1.39-t8.1')

    def test_staged_corruption_changes_nothing(self):
        root, incoming, path = self.plan()
        (incoming/'app.py').write_text('bad')
        result = self.apply(path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((root/'app.py').read_text(), 'old')

    def test_locked_old_file_is_preserved_and_previous_replacement_rolled_back(self):
        root, _, path = self.plan({'locked.py': 'new'})
        (root/'locked.py').write_text('old locked')
        wrapper = self.base/'lock.ps1'
        wrapper.write_text('$lock = [IO.File]::Open($env:T8_LOCKED_FILE, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)\n& $env:T8_APPLY_SCRIPT -PlanPath $env:T8_PLAN\nexit $LASTEXITCODE\n', encoding='utf-8')
        env = dict(os.environ, T8_LOCKED_FILE=str(root/'locked.py'), T8_APPLY_SCRIPT=str(SOURCE/'tools/apply_portable_update.ps1'), T8_PLAN=str(path))
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(wrapper)], env=env, capture_output=True, timeout=45)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((root/'app.py').read_text(), 'old')
        self.assertEqual((root/'locked.py').read_text(), 'old locked')

    def test_live_process_blocks_replacement(self):
        root, _, path = self.plan()
        command = root/'cmd.exe'
        shutil.copy2(Path(os.environ['SystemRoot'])/'System32/cmd.exe', command)
        process = subprocess.Popen([str(command), '/c', 'set /p T8_WAIT=waiting'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.assertIsNone(process.poll())
            self.assertIn(process.pid, update.running_processes(root))
            result = self.apply(path)
            self.assertEqual(result.returncode, 1)
            self.assertEqual((root/'app.py').read_text(), 'old')
        finally:
            process.terminate()
            process.wait(timeout=10)
            process.stdin.close()

    @unittest.skipUnless((SOURCE/'runtime/python/python.exe').exists(), 'bundled runtime available in packaging job')
    def test_batch_entry_preserves_preparation_error_exit_status(self):
        root = self.base/'batch'
        runtime = root/'runtime/python'
        runtime.mkdir(parents=True)
        for file in (SOURCE/'runtime/python').iterdir():
            if file.is_file() and file.suffix.lower() in ('.exe', '.dll', '.zip', '._pth'):
                shutil.copy2(file, runtime/file.name)
        (root/'tools').mkdir()
        shutil.copy2(SOURCE/'UPDATE-PORTABLE.bat', root/'UPDATE-PORTABLE.bat')
        shutil.copy2(SOURCE/'tools/run_portable_update.ps1', root/'tools/run_portable_update.ps1')
        (root/'tools/portable_update.py').write_text('raise SystemExit(2)\n', encoding='utf-8')
        result = subprocess.run(['cmd', '/d', '/c', 'UPDATE-PORTABLE.bat'], cwd=root, input='\n', capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 2, result.stdout+result.stderr)

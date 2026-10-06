"""Run the real Windows entry points with an isolated, tiny embedded runtime."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT/'runtime/python'


@unittest.skipUnless(os.name == 'nt' and (RUNTIME/'python.exe').exists(), 'packaging Windows runtime')
class BatchEntries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='Strata entries 中文 ')
        self.root = Path(self.tmp.name)
        runtime = self.root/'runtime/python'
        runtime.mkdir(parents=True)
        for file in RUNTIME.iterdir():
            if file.is_file() and file.suffix.lower() in ('.exe', '.dll', '.zip', '._pth'):
                shutil.copy2(file, runtime/file.name)
        (self.root/'tools').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_entry(self, name, options=''):
        return subprocess.run(['cmd', '/d', '/c', name+' '+options], cwd=self.root,
                              input='\n\n\n', capture_output=True, text=True, errors='replace', timeout=45)

    def test_all_failed_python_entries_preserve_failure_code(self):
        for name, script in [('START-PORTABLE.bat', 'portable.py'), ('IMPORT-MODEL.bat', 'portable.py'),
                             ('CHECK-ENV.bat', 'portable.py'), ('PREPARE-MODEL.bat', 'tools/prepare_portable_model.py'),
                             ('VERIFY-PACKAGE.bat', 'tools/verify_portable.py')]:
            with self.subTest(entry=name):
                shutil.copy2(ROOT/name, self.root/name)
                (self.root/script).write_text('raise SystemExit(7)\n', encoding='utf-8')
                result = self.run_entry(name)
                self.assertEqual(result.returncode, 7, result.stdout+result.stderr)

    def update_fixture(self):
        for name in ['INSTALL-VISION.bat', 'UPDATE-PORTABLE.bat', 'tools/run_portable_update.ps1', 'tools/update_worker.cmd']:
            shutil.copy2(ROOT/name, self.root/name)
        (self.root/'.portable-update').mkdir()
        (self.root/'.portable-update/plan.json').write_text('{}')
        (self.root/'.portable-update/apply.ps1').write_text("param([string]$PlanPath)\nSet-Content -LiteralPath (Join-Path $PSScriptRoot 'applied') -Value 'yes'\nexit 0\n")
        (self.root/'tools/portable_update.py').write_text("import json,sys\nfrom pathlib import Path\nPath('options.json').write_text(json.dumps(sys.argv[1:]))\n", encoding='utf-8')

    def test_install_vision_entry_forwards_edition_and_applies_plan(self):
        self.update_fixture()
        result = self.run_entry('INSTALL-VISION.bat')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(json.loads((self.root/'options.json').read_text()), ['--edition', 'VisionReady-NoMainModel'])
        self.assertTrue((self.root/'.portable-update/applied').exists())

    def test_check_entry_does_not_apply_plan(self):
        self.update_fixture()
        result = self.run_entry('UPDATE-PORTABLE.bat', '--check')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(json.loads((self.root/'options.json').read_text()), ['--check'])
        self.assertFalse((self.root/'.portable-update/applied').exists())

    def test_unknown_update_options_fail_before_python(self):
        self.update_fixture()
        result = self.run_entry('UPDATE-PORTABLE.bat', '--edition Arbitrary')
        self.assertEqual(result.returncode, 2, result.stdout+result.stderr)
        self.assertFalse((self.root/'options.json').exists())


if __name__ == '__main__':
    unittest.main()

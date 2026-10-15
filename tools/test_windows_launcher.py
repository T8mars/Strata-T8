"""Run the native EXE against a tiny interpreter fixture; never start a model."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from tools import build_windows_launcher as builder

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'runtime/python'

@unittest.skipUnless(os.name=='nt' and (ROOT/'Strata-T8.exe').is_file() and (RUNTIME/'python.exe').is_file(),
                     'Built Windows EXE and embedded runtime required')
class WindowsLauncher(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='T8 EXE 中文 & 空格 ')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        runtime=self.root/'runtime/python';runtime.mkdir(parents=True)
        for path in RUNTIME.iterdir():
            if path.is_file() and (path.suffix.lower() in ('.exe','.dll','.pyd','.zip','._pth')):
                shutil.copy2(path,runtime/path.name)
        shutil.copy2(ROOT/'Strata-T8.exe',self.root/'Strata-T8.exe')
        (self.root/'portable.py').write_text('''import json,os,sys
from pathlib import Path
Path('argv.json').write_text(json.dumps({'argv':sys.argv[1:],'python':sys.executable,
    'pythonhome':os.environ.get('PYTHONHOME'),'pythonpath':os.environ.get('PYTHONPATH'),
    'cwd':str(Path.cwd())},ensure_ascii=False),'utf-8')
raise SystemExit(7 if '--fixture-fail' in sys.argv else 0)
''','utf-8')

    def run_exe(self,*args):
        env={**os.environ,'PYTHONHOME':'X:/not-a-python','PYTHONPATH':'X:/not-packages'}
        return subprocess.run([str(self.root/'Strata-T8.exe'),*args],cwd=ROOT,env=env,
            stdin=subprocess.DEVNULL,capture_output=True,timeout=15)

    def receipt(self):
        return json.loads((self.root/'argv.json').read_text('utf-8'))

    def test_double_click_defaults_to_start_and_uses_its_own_python_and_directory(self):
        result=self.run_exe()
        self.assertEqual(result.returncode,0,result.stderr)
        value=self.receipt()
        self.assertEqual(value['argv'],['start'])
        self.assertEqual(Path(value['python']).resolve(),(self.root/'runtime/python/python.exe').resolve())
        self.assertEqual(Path(value['cwd']).resolve(),self.root.resolve())
        self.assertIsNone(value['pythonhome']);self.assertIsNone(value['pythonpath'])

    def test_arguments_round_trip_quotes_spaces_unicode_and_backslashes_without_a_shell(self):
        args=['--data-dir','中文 模型 "quoted" & ! %PATH% \\','--no-browser']
        self.assertEqual(self.run_exe(*args).returncode,0)
        self.assertEqual(self.receipt()['argv'],['start',*args])

    def test_explicit_actions_and_shortcuts_do_not_duplicate_start(self):
        for supplied,expected in [('settings','settings'),('--settings','settings'),('--check','check'),('check','check')]:
            with self.subTest(action=supplied):
                self.assertEqual(self.run_exe(supplied).returncode,0)
                self.assertEqual(self.receipt()['argv'],[expected])

    def test_missing_runtime_refuses_to_fall_back_to_system_python_and_preserves_exit_codes(self):
        self.assertEqual(self.run_exe('--fixture-fail').returncode,7)
        (self.root/'runtime/python/python.exe').unlink()
        result=self.run_exe('--no-browser')
        self.assertEqual(result.returncode,2)
        self.assertIn(b'Extract the complete package',result.stderr)

    def test_build_record_and_static_dependencies_match_the_current_source(self):
        self.assertTrue(builder.verify(ROOT)['static_runtime'])
        from tools.hip.dll_closure import pe_imports
        dependencies=pe_imports(ROOT/'Strata-T8.exe')
        self.assertTrue(dependencies)
        self.assertFalse(any(name.lower().startswith(('vcruntime','msvcp')) for name in dependencies),dependencies)

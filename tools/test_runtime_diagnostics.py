"""Startup errors name the import; an actual missing decoder DLL is detectable offline."""
import builtins
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import portable
from tools import runtime_diagnostics as diag


class ImportErrors(unittest.TestCase):
    def test_installation_report_whitelists_configuration_and_never_copies_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'portable-settings.json').write_text(json.dumps({'portable_config':'strata-fixture.json','other_secret':'do-not-copy'}))
            (root/'strata-fixture.json').write_text(json.dumps({'model_name':'fixture-model','port':None,'api_key':'private-key',
                'args':['--max-context','131072','--api-key','secret-argument','--mmap-experts']}))
            result = diag.selected_configuration(root)
            text = json.dumps(result)
            for secret in ('private-key','secret-argument','do-not-copy'):
                self.assertNotIn(secret,text)
            self.assertEqual(result['selected']['memory_options']['--max-context'],'131072')
            self.assertEqual(result['selected']['memory_flags'],['--mmap-experts'])

    def test_launcher_names_video_import_and_saves_traceback_without_configuration(self):
        original = builtins.__import__
        def fail_av(name, *args, **kwargs):
            if name == 'av':
                raise ImportError('DLL load failed while importing _core: missing module')
            return original(name, *args, **kwargs)
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(portable, 'ROOT', Path(temporary)), mock.patch('builtins.__import__', side_effect=fail_av):
            with self.assertRaisesRegex(RuntimeError, 'av \\(PyAV video decoder\\)') as caught:
                portable.environment_check()
            self.assertIn('CHECK-ENV.bat', str(caught.exception))
            log = Path(temporary)/'logs/runtime-import-error.log'
            self.assertIn('ImportError', log.read_text('utf-8'))
            self.assertIn('_core', log.read_text('utf-8'))

    def test_missing_inventory_and_decoder_directory_are_named(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = diag.video_dependencies(temporary)
            self.assertIn('av.libs/*.dll', result['missing_package_files'])
            self.assertIn('av/_core*.pyd', result['missing_package_files'])

    @unittest.skipUnless(os.name == 'nt', 'Actual Windows decoder import')
    def test_actual_missing_wheel_dll_reproduces_core_error_without_a_model(self):
        import av
        with tempfile.TemporaryDirectory(prefix='Strata DLL 中文 ') as temporary:
            root = Path(temporary)
            runtime = root/'runtime/python'
            runtime.mkdir(parents=True)
            for file in Path(sys.executable).parent.iterdir():
                if file.is_file() and (file.suffix.lower() in ('.dll','.pyd','.zip','._pth') or file.name == 'python.exe'):
                    shutil.copy2(file, runtime/file.name)
            base = runtime/'Lib/site-packages'
            source = Path(av.__file__).parent
            shutil.copytree(source, base/'av', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(source.parent/'av.libs', base/'av.libs')
            (root/'licenses/video').mkdir(parents=True)
            sources = Path(diag.ROOT)/'licenses/video/sources.json'
            shutil.copy2(sources, root/'licenses/video/sources.json')
            command = [str(runtime/'python.exe'), '-I', '-c', 'import av; print(av.__version__)']
            env = os.environ.copy()
            env['PATH'] = str(Path(os.environ.get('SYSTEMROOT',r'C:\Windows'))/'System32')
            good = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=15)
            self.assertEqual(good.returncode, 0, good.stderr)
            missing = next((base/'av.libs').glob('avcodec-*.dll'))
            missing.unlink()  # only a disposable fixture file; never the installed package
            broken = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=15)
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn(b'while importing _core', broken.stderr)
            result = diag.video_dependencies(root)
            self.assertIn('av.libs/'+missing.name, result['missing_package_files'])

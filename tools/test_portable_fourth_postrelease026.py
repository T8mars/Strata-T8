"""Model preparation must not enter newly bundled engine components."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from serve import portable_ui as ui
from tools import test_portable_ui as fixture
from tools import runtime_diagnostics as diagnostics


class PreparationComponentBoundaries(unittest.TestCase):
    setUp = fixture.SetupPage.setUp
    tearDown = fixture.SetupPage.tearDown

    def test_preparation_rejects_all_engine_component_folders_before_writing(self):
        with mock.patch.object(ui.threading, 'Thread') as worker:
            for component in ('engine-cuda12','engine-dsv41','engine-hip-rdna2','engine-hip-rdna3'):
                with self.subTest(component=component):
                    self.app.set_job('idle', 'fixture')
                    destination = self.root/component/'new-model'
                    with self.assertRaisesRegex(ui.SetupError, '独立文件夹'):
                        self.app.prepare({'path':str(destination),'accept_download':True})
                    self.assertFalse(destination.exists())
                    worker.assert_not_called()

    def test_separate_user_model_folder_inside_installation_is_still_allowed(self):
        destination = self.root/'my-models'
        with mock.patch.object(ui.threading, 'Thread') as worker:
            self.app.prepare({'path':str(destination),'accept_download':True})
        self.assertTrue((destination/'.strata-model-preparation.json').is_file())
        self.assertEqual(self.app.job['state'], 'preparing')
        worker.return_value.start.assert_called_once()


class DiagnosticDamageRecovery(unittest.TestCase):
    def test_invalid_video_inventory_does_not_discard_import_diagnostics(self):
        for inventory in ('{truncated', '[]'):
            with self.subTest(inventory=inventory), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                python = root/'runtime/python/python.exe'
                python.parent.mkdir(parents=True)
                python.write_bytes(b'fixture')
                source = root/'licenses/video/sources.json'
                source.parent.mkdir(parents=True)
                source.write_text(inventory, encoding='utf-8')
                with mock.patch.object(diagnostics.subprocess, 'run', return_value=subprocess.CompletedProcess([],0,'OK','')), \
                        mock.patch.object(diagnostics, 'machine_resources', return_value={}):
                    result = diagnostics.diagnose(root)
                self.assertEqual(len(result['modules']), len(diagnostics.MODULES))
                self.assertTrue(all(item['returncode'] == 0 for item in result['modules']))
                self.assertIn('error', result['video_dependencies'])
                self.assertIn('inventory', result['video_dependencies']['error'])

    def test_cli_reports_dependency_failures_even_when_imports_pass(self):
        for video, expected in (
                ({'missing_package_files':['av.libs/missing.dll'],'unavailable_system_libraries':[]},1),
                ({'missing_package_files':[],'unavailable_system_libraries':[{'name':'missing-system.dll'}]},1),
                ({'error':'invalid inventory'},1),
                ({'missing_package_files':[],'unavailable_system_libraries':[]},0)):
            with self.subTest(video=video), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = {'modules':[{'module':'av','returncode':0,'detail':'OK'}], 'video_dependencies':video}
                with mock.patch.object(diagnostics, 'diagnose', return_value=result), \
                        mock.patch('sys.argv', ['diagnose','--root',str(root)]), redirect_stdout(io.StringIO()):
                    self.assertEqual(diagnostics.main(), expected)
                saved = json.loads((root/'logs/runtime-diagnostics.json').read_text('utf-8'))
                self.assertEqual(saved['modules'], result['modules'])
                self.assertEqual(saved['video_dependencies'], video)


if __name__ == '__main__':
    unittest.main()

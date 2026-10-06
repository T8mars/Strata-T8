"""Fifth audit: update parsing, independent apply checks, races and staging ownership."""
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
from tools import test_portable_update as fixtures
from tools import test_portable_update_round4 as fourth


class StrictReleaseJson(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown

    def archive(self, change):
        source = self.base/'source'
        fixtures.package(source, {'app.py':'new'})
        change(source)
        archive = self.base/'package.zip'
        with zipfile.ZipFile(archive,'w') as stream:
            for file in source.rglob('*'):
                if file.is_file(): stream.write(file, 'app/'+file.relative_to(source).as_posix())
        return archive

    def test_duplicate_manifest_members_are_rejected(self):
        def change(root):
            path=root/'PACKAGE-MANIFEST.json'
            text=path.read_text()
            path.write_text(text.replace('"version":', '"version":"0.1.39-t8.0","version":',1))
        with self.assertRaisesRegex(ValueError,'duplicate|Duplicate'):
            update.extract_release(self.archive(change), self.base/'incoming')

    def test_duplicate_application_metadata_is_rejected(self):
        def change(root):
            meta=root/'meta.json'
            meta.write_text('{"version":"0.1.39-t8.0","version":"0.1.39-t8.2"}')
            path=root/'PACKAGE-MANIFEST.json'; manifest=json.loads(path.read_text())
            entry=next(e for e in manifest['files'] if e['path']=='meta.json')
            entry.update(size=meta.stat().st_size,sha256=hashlib.sha256(meta.read_bytes()).hexdigest())
            path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'duplicate|Duplicate'):
            update.extract_release(self.archive(change), self.base/'incoming')

    def test_release_api_nonfinite_numbers_are_rejected(self):
        with mock.patch.object(update,'urlopen',return_value=io.BytesIO(b'{"tag_name":"v0.1.39-t8.2","extra":NaN}')):
            with self.assertRaises(ValueError): update.latest_release('T8mars/Strata-T8')

    def test_release_api_duplicate_fields_are_rejected(self):
        with mock.patch.object(update,'urlopen',return_value=io.BytesIO(b'{"tag_name":"bad","tag_name":"v0.1.39-t8.2"}')):
            with self.assertRaises(ValueError): update.latest_release('T8mars/Strata-T8')


@unittest.skipUnless(os.name=='nt','actual Windows updater')
class ApplyBoundaries(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    plan = fourth.ApplyTopology.plan
    apply = fixtures.WindowsApply.apply

    def bind(self, path, incoming, transform):
        plan=json.loads(path.read_text()); manifest=json.loads((incoming/'PACKAGE-MANIFEST.json').read_text())
        transform(plan,manifest)
        (incoming/'PACKAGE-MANIFEST.json').write_text(json.dumps(manifest))
        plan['manifest_sha256']=hashlib.sha256((incoming/'PACKAGE-MANIFEST.json').read_bytes()).hexdigest()
        path.write_text(json.dumps(plan))

    def unchanged(self, result, root):
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((root/'app.py').read_text(),'old')
        self.assertFalse((self.base/'backup').exists())

    def test_scalar_file_list_cannot_be_applied(self):
        root,incoming,path=self.plan({'app.py':'old'},{'app.py':'new'})
        # Two entries normally include meta.json; make both lists syntactically scalar
        # while retaining a valid one-entry old installation for the coercion boundary.
        installed=json.loads((root/'PACKAGE-MANIFEST.json').read_text())
        installed['files']=[e for e in installed['files'] if e['path']=='app.py']
        (root/'PACKAGE-MANIFEST.json').write_text(json.dumps(installed))
        def transform(plan,manifest):
            plan['old']=installed['files'][0]
            manifest['files']=[e for e in manifest['files'] if e['path']=='app.py']
            plan['new']=manifest['files'][0]
        self.bind(path,incoming,transform)
        self.unchanged(self.apply(path),root)

    def test_numeric_string_file_sizes_are_rejected_before_mutation(self):
        root,incoming,path=self.plan({'app.py':'old'},{'app.py':'new'})
        def transform(plan,manifest):
            for entry in manifest['files']: entry['size']=str(entry['size'])
            plan['new']=manifest['files']
        self.bind(path,incoming,transform)
        self.unchanged(self.apply(path),root)

    def test_apply_rejects_protected_paths_even_when_both_manifests_agree(self):
        root,incoming,path=self.plan({'app.py':'old'},{'app.py':'new'})
        (root/'portable-settings.json').write_text('user settings')
        (incoming/'portable-settings.json').write_text('release overwrite')
        installed=json.loads((root/'PACKAGE-MANIFEST.json').read_text())
        old_entry={'path':'portable-settings.json','size':13,'sha256':hashlib.sha256(b'user settings').hexdigest()}
        installed['files'].append(old_entry)
        (root/'PACKAGE-MANIFEST.json').write_text(json.dumps(installed))
        def transform(plan,manifest):
            plan['old']=installed['files']
            manifest['files'].append({'path':'portable-settings.json','size':17,'sha256':hashlib.sha256(b'release overwrite').hexdigest()})
            plan['new']=manifest['files']
        self.bind(path,incoming,transform)
        self.unchanged(self.apply(path),root)
        self.assertEqual((root/'portable-settings.json').read_text(),'user settings')

    def test_non_directory_application_root_is_rejected_cleanly(self):
        root,incoming,path=self.plan({'app.py':'old'},{'app.py':'new'})
        plan=json.loads(path.read_text()); plan['root']=str(root/'app.py'); path.write_text(json.dumps(plan))
        result=self.apply(path)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Update failed:',result.stdout)
        self.assertEqual((root/'app.py').read_text(),'old')

    def test_non_directory_staging_root_is_rejected_cleanly(self):
        root,incoming,path=self.plan({'app.py':'old'},{'app.py':'new'})
        plan=json.loads(path.read_text()); plan['stage']=str(incoming/'app.py'); path.write_text(json.dumps(plan))
        result=self.apply(path)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Update failed:',result.stdout)
        self.assertEqual((root/'app.py').read_text(),'old')

    def test_unlisted_staged_file_is_rejected_before_mutation(self):
        root,incoming,path=self.plan({'app.py':'old'},{'app.py':'new'})
        (incoming/'unlisted.py').write_text('unexpected staging data')
        self.unchanged(self.apply(path),root)

    def test_staged_manifest_without_metadata_is_rejected(self):
        root,incoming,path=self.plan({'app.py':'old'},{'app.py':'new'})
        def transform(plan,manifest):
            manifest['files']=[e for e in manifest['files'] if e['path']!='meta.json']; plan['new']=manifest['files']
        self.bind(path,incoming,transform)
        (incoming/'meta.json').unlink()
        self.unchanged(self.apply(path),root)

    def wrapper(self, path, body, **values):
        wrapper=self.base/'wrapper.ps1'
        wrapper.write_text(body+'\n& $env:T8_APPLY_SCRIPT -PlanPath $env:T8_PLAN\nexit $LASTEXITCODE\n')
        env=dict(os.environ,T8_APPLY_SCRIPT=str(fixtures.SOURCE/'tools/apply_portable_update.ps1'),T8_PLAN=str(path),**{k:str(v) for k,v in values.items()})
        return subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],env=env,capture_output=True,text=True,errors='replace',timeout=45)

    def test_file_created_during_copy_is_not_overwritten_or_deleted_by_rollback(self):
        root,_,path=self.plan({'app.py':'old'},{'app.py':'new','new.py':'package new file'})
        body='''function Copy-Item { param([string]$LiteralPath,[string]$Destination)
if ($LiteralPath.EndsWith('new.py')) { [IO.File]::WriteAllText($env:T8_RACE_TARGET,'user raced file') }
Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination
}'''
        result=self.wrapper(path,body,T8_RACE_TARGET=root/'new.py')
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((root/'new.py').read_text(),'user raced file')
        self.assertEqual((root/'app.py').read_text(),'old')
        report=json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(report['rollback_errors'],[])

    def test_readonly_old_file_is_preserved_during_copy_failure(self):
        root,_,path=self.plan({'app.py':'old'},{'app.py':'new'})
        body='''[IO.File]::SetAttributes($env:T8_READONLY_PATH,[IO.FileAttributes]::ReadOnly)
function Copy-Item { throw 'controlled copy failure' }'''
        try:
            result=self.wrapper(path,body,T8_READONLY_PATH=root/'app.py')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((root/'app.py').read_text(),'old')
        finally:
            subprocess.run(['attrib','-R',str(root/'app.py')],capture_output=True)

    def test_plan_delete_failure_rolls_back_and_retains_retry_plan(self):
        root,_,path=self.plan({'app.py':'old'},{'app.py':'new'})
        body='''function Remove-Item { param([string]$LiteralPath,[switch]$Force)
if ($LiteralPath -eq $env:T8_PLAN) { throw 'controlled plan deletion denied' }
Microsoft.PowerShell.Management\\Remove-Item -LiteralPath $LiteralPath -Force:$Force
}'''
        result=self.wrapper(path,body)
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((root/'app.py').read_text(),'old')
        self.assertTrue(path.exists())
        report=json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
        self.assertFalse(report['success']); self.assertEqual(report['rollback_errors'],[])

    def test_existing_result_is_atomically_replaced_on_success(self):
        root,_,path=self.plan({'app.py':'old'},{'app.py':'new'})
        result_path=root/'.portable-update/result.json'; result_path.write_text('{"success":false,"previous":true}')
        result=self.apply(path)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((root/'app.py').read_text(),'new')
        report=json.loads(result_path.read_text(encoding='utf-8-sig'))
        self.assertTrue(report['success']); self.assertEqual(report['version'],'0.1.39-t8.2')
        self.assertEqual({p.name for p in result_path.parent.iterdir()},{'result.json'})

    def test_readonly_staged_file_can_be_removed_during_rollback(self):
        root,incoming,path=self.plan({'app.py':'old'},{'app.py':'new'})
        body='''[IO.File]::SetAttributes($env:T8_READONLY_PATH,[IO.FileAttributes]::ReadOnly)
function Copy-Item { param([string]$LiteralPath,[string]$Destination)
if ($LiteralPath.EndsWith('PACKAGE-MANIFEST.json')) { throw 'controlled final-copy failure' }
Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $LiteralPath -Destination $Destination
}'''
        try:
            result=self.wrapper(path,body,T8_READONLY_PATH=incoming/'app.py')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((root/'app.py').read_text(),'old')
            report=json.loads((root/'.portable-update/result.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(report['rollback_errors'],[])
        finally:
            for file in (root/'app.py',incoming/'app.py'):
                subprocess.run(['attrib','-R',str(file)],capture_output=True)


class OwnedStaging(unittest.TestCase):
    setUp = fixtures.ReleaseValidation.setUp
    tearDown = fixtures.ReleaseValidation.tearDown
    fixture=fixtures.ReleaseValidation.fixture

    def prepare(self,root,release,fetch,stage):
        with mock.patch.object(update,'fetch',side_effect=fetch),mock.patch.object(update,'running_processes',return_value=[]),mock.patch.object(update.tempfile,'mkdtemp',return_value=str(stage)):
            return update.prepare(root,release)

    def test_repeated_prepare_removes_only_its_superseded_unapplied_owned_stage(self):
        root,release,fetch=self.fixture()
        first=Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
        second=Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
        try:
            self.prepare(root,release,fetch,first)
            self.prepare(root,release,fetch,second)
            self.assertFalse(first.exists())
            self.assertTrue((second/'incoming/app.py').is_file())
        finally:
            for path in (first,second):
                if path.exists(): shutil.rmtree(path)

    def test_superseded_stage_with_backup_is_retained(self):
        root,release,fetch=self.fixture()
        first=Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
        second=Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
        try:
            self.prepare(root,release,fetch,first)
            (first/'backup').mkdir(); (first/'backup/precious.txt').write_text('old backup')
            self.prepare(root,release,fetch,second)
            self.assertEqual((first/'backup/precious.txt').read_text(),'old backup')
        finally:
            for path in (first,second):
                if path.exists(): shutil.rmtree(path)

    def test_noop_removes_only_its_owned_pending_stage(self):
        root,release,fetch=self.fixture()
        stage=Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
        try:
            self.prepare(root,release,fetch,stage)
            release=dict(release,tag_name='v0.1.39-t8.1')
            with mock.patch.object(update,'running_processes',return_value=[]):
                self.assertIsNone(update.prepare(root,release))
            self.assertFalse(stage.exists())
            self.assertFalse((root/'.portable-update/plan.json').exists())
        finally:
            if stage.exists(): shutil.rmtree(stage)

    def test_user_added_file_and_empty_directory_keep_superseded_stage(self):
        for kind in ('file','empty-directory'):
            with self.subTest(kind=kind):
                root,release,fetch=self.fixture()
                first=Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
                second=Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
                try:
                    self.prepare(root,release,fetch,first)
                    user=first/'incoming/user-added'
                    user.write_text('keep') if kind=='file' else user.mkdir()
                    self.prepare(root,release,fetch,second)
                    self.assertTrue(user.exists())
                finally:
                    for path in (first,second):
                        if path.exists(): shutil.rmtree(path)
                    shutil.rmtree(root)
                    shutil.rmtree(self.base/'source')

    def test_unmarked_or_malformed_previous_stage_is_never_deleted(self):
        for malformed in (False,True):
            with self.subTest(malformed=malformed):
                root,release,fetch=self.fixture()
                previous=self.base/'previous-user-directory'; previous.mkdir(); (previous/'keep.txt').write_text('user')
                control=root/'.portable-update'; control.mkdir()
                previous_plan={'stage':None,'stage_owner':'fake'} if malformed else {'stage':str(previous/'incoming'),'backup':str(previous/'backup')}
                (control/'plan.json').write_text(json.dumps(previous_plan))
                stage=Path(tempfile.mkdtemp(prefix='Strata-T8-update-'))
                try:
                    self.assertIsNotNone(self.prepare(root,release,fetch,stage))
                    self.assertEqual((previous/'keep.txt').read_text(),'user')
                    self.assertTrue((stage/'incoming/app.py').is_file())
                finally:
                    if stage.exists(): shutil.rmtree(stage)
                    shutil.rmtree(root); shutil.rmtree(self.base/'source'); shutil.rmtree(previous)


if __name__=='__main__': unittest.main()

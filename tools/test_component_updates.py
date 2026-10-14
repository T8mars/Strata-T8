"""Actual component assembly, selective transport and successful-update reversal."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock
from tools import component_updates as components,portable_update as update,portable_restore as restore
from tools import publish_portable_release as publisher
from tools.test_portable_update import package,SOURCE


class Components(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='Strata component 中文 ')
        self.base=Path(self.tmp.name)
        self.environment=mock.patch.dict(os.environ,{'LOCALAPPDATA':str(self.base/'appdata')})
        self.environment.start()
    def tearDown(self):
        self.environment.stop();self.tmp.cleanup()
    def fixture(self):
        root=self.base/'installed';incoming=self.base/'package';output=self.base/'assets'
        files={'app.py':'old app','START-HERE.bat':'one entry','runtime/python/python.exe':'python',
            'runtime/asr/ASR-CPU.json':'cpu runtime','engine/strata.exe':'cuda','engine-hip/strata.exe':'hip',
            'tools/apply_portable_update.ps1':(SOURCE/'tools/apply_portable_update.ps1').read_text(encoding='utf-8')}
        package(root,files,'0.1.42-t8.8')
        files['app.py']='new app';package(incoming,files,'0.1.42-t8.9')
        index=components.build(incoming,output)
        assets={p.name:{'name':p.name,'size':p.stat().st_size,'browser_download_url':'https://github.com/T8mars/Strata-T8/releases/download/v0.1.42-t8.9/'+p.name} for p in output.iterdir()}
        calls=[]
        def fetch(url,destination=None,limit=2*1024**3):
            name=url.rsplit('/',1)[1];calls.append(name);raw=(output/name).read_bytes()
            if len(raw)>limit:raise ValueError('too big')
            if destination is None:return raw
            destination.write_bytes(raw);return len(raw)
        return root,incoming,output,index,assets,calls,fetch
    def test_app_update_reuses_verified_runtimes_and_keeps_single_entry(self):
        root,_,_,index,assets,calls,fetch=self.fixture()
        assembled=self.base/'assembled';assembled.mkdir()
        manifest,result=components.materialize(root,assembled,index,assets,fetch)
        self.assertEqual(result['downloaded'],['app'])
        self.assertEqual(set(result['reused']),{'python','asr-cpu','cuda','hip'})
        self.assertEqual(len(calls),1)
        self.assertEqual((assembled/'START-HERE.bat').read_text(),'one entry')
        update.validate_manifest(assembled,manifest)
    def test_corrupted_local_runtime_is_downloaded_again(self):
        root,_,_,index,assets,calls,fetch=self.fixture()
        (root/'runtime/python/python.exe').write_text('damaged')
        assembled=self.base/'assembled';assembled.mkdir()
        _,result=components.materialize(root,assembled,index,assets,fetch)
        self.assertEqual(result['downloaded'],['app','python'])
        self.assertEqual((assembled/'runtime/python/python.exe').read_text(),'python')
    def test_index_rejects_cross_component_files_versions_and_main_models(self):
        root,_,_,index,_,_,_=self.fixture()
        bad=copy.deepcopy(index);bad['version']='0.1.42-t8.10'
        with self.assertRaises(ValueError):components.validate(bad,root)
        bad=copy.deepcopy(index);bad['components'][0]['files'].append(copy.deepcopy(bad['components'][1]['files'][0]))
        with self.assertRaises(ValueError):components.validate(bad,root)
        bad=copy.deepcopy(index);bad['manifest']['files'][0]['path']='models/main.gguf'
        with self.assertRaises(ValueError):components.validate(bad,root)
    def test_component_abi_pins_are_checked_after_assembly(self):
        root,_,_,index,assets,_,fetch=self.fixture()
        index['compatibility']['python_version']='incompatible'
        incoming=self.base/'assembled';incoming.mkdir()
        with self.assertRaisesRegex(ValueError,'pins'):components.materialize(root,incoming,index,assets,fetch)
    def test_update_then_offline_restore_preserves_all_private_data(self):
        root,_,_,index,assets,calls,fetch=self.fixture()
        private={'portable-settings.json':'profile','asr-settings.json':'asr model path',
                 'models/main.gguf':'user model','.asr-cache/cues.json':'dialogue','.video-tasks/notes.json':'progress'}
        for name,value in private.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
        release={'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}
        with mock.patch.object(update,'fetch',side_effect=fetch),mock.patch.object(update,'running_processes',return_value=[]):
            plan=update.prepare(root,release)
        def apply():
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'.portable-update/apply.ps1'),'-PlanPath',str(root/'.portable-update/plan.json')],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=35)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        apply()
        self.assertEqual((root/'app.py').read_text(),'new app')
        backups=restore.list_backups(root)['backups']
        self.assertEqual(len(backups),1);self.assertTrue(backups[0]['available'])
        self.assertEqual(backups[0]['from_version'],'0.1.42-t8.8')
        with mock.patch.object(update,'running_processes',return_value=[]):restore.prepare(root,backups[0]['id'])
        apply()
        self.assertEqual((root/'app.py').read_text(),'old app')
        for name,value in private.items():self.assertEqual((root/name).read_text(),value)
        self.assertFalse(any(name.endswith('.zip') and 'component-app' not in name for name in calls))
    def test_tampered_backup_fails_before_replacement(self):
        root,_,_,index,assets,_,fetch=self.fixture()
        release={'tag_name':'v0.1.42-t8.9','assets':list(assets.values())}
        with mock.patch.object(update,'fetch',side_effect=fetch),mock.patch.object(update,'running_processes',return_value=[]):plan=update.prepare(root,release)
        process=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'.portable-update/apply.ps1'),'-PlanPath',str(root/'.portable-update/plan.json')],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=35)
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        (Path(plan['backup'])/'app.py').write_text('corrupt backup')
        with self.assertRaises(ValueError):restore.read_receipt(root,plan['stage_owner'])
        self.assertEqual((root/'app.py').read_text(),'new app')
    def test_publisher_binds_components_to_the_full_offline_archive(self):
        _,package_dir,output,index,_,_,_=self.fixture()
        root=self.base/'publishing';dist=root/'dist';dist.mkdir(parents=True)
        for asset in output.iterdir():shutil.copy2(asset,dist/asset.name)
        version=index['version'];name=update.archive_name(version,'Portable-NoModels')
        with zipfile.ZipFile(dist/name,'w') as zipped:
            for path in package_dir.rglob('*'):
                if path.is_file():zipped.write(path,name.removesuffix('.zip')+'/'+path.relative_to(package_dir).as_posix())
        meta=json.loads((package_dir/'meta.json').read_text(encoding='utf-8'))
        with mock.patch.object(publisher,'EDITIONS',('Portable-NoModels',)):
            assets,digests=publisher.audit_component_assets(root,meta)
            self.assertEqual({p.name for p in assets},set(digests))
            app=next(c for c in index['components'] if c['id']=='app')
            (dist/app['asset']).write_bytes(b'corrupted transport')
            with self.assertRaisesRegex(ValueError,'checksum'):publisher.audit_component_assets(root,meta)


if __name__=='__main__':unittest.main()

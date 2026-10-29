"""Behavioral regressions from upstream PRs 4562, 4563, 4568 and 4571."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from strata_cli.launch import access, cmd, settings as S
from tests.terminal.launch_support import Home, run_cli

ROOT = Path(__file__).resolve().parents[2]


class LaunchFailureIsolation(unittest.TestCase):
    def assert_unreadable(self, row):
        self.assertIsNone(row['config'])
        self.assertEqual(row['host'], '127.0.0.1')
        self.assertEqual(row['port'], S.DEFAULT_PORT)

    def test_unreadable_only_running_profile_has_no_foreign_config(self):
        with mock.patch('strata_control.profiles.list_profiles', return_value=[{'id':'launch-bad'}]), \
                mock.patch('strata_control.runtime.status', return_value={'state':'ready'}), \
                mock.patch('strata_control.profiles.load', side_effect=OSError('fixture unreadable')):
            rows = cmd.running_launches()
        self.assertEqual(len(rows), 1)
        self.assert_unreadable(rows[0])

    def test_unreadable_later_profile_does_not_inherit_previous_launch_config(self):
        good = {'source_config':'fixture-first.json','overrides':{'port':8912,'host':'0.0.0.0'}}
        with mock.patch('strata_control.profiles.list_profiles', return_value=[{'id':'launch-good'},{'id':'launch-bad'}]), \
                mock.patch('strata_control.runtime.status', return_value={'state':'ready'}), \
                mock.patch('strata_control.profiles.load', side_effect=[(good,1), OSError('fixture unreadable')]):
            rows = cmd.running_launches()
        self.assertEqual(rows[0]['config'], good['source_config'])
        self.assertEqual(rows[0]['port'], 8912)
        self.assert_unreadable(rows[1])

    def test_bad_profile_before_or_after_a_good_one_preserves_saved_key(self):
        with Home() as home:
            path = home.base/'saved.json'
            data = b'{"api_key":"sk-strata-fixed-fixture"}'
            path.write_bytes(data)
            bad = {'id':'launch-damaged','error':'fixture unreadable'}
            good = {'id':'launch-existing','source_config':str(path)}
            for listing in ([bad,good], [good,bad]):
                with self.subTest(order=[row['id'] for row in listing]), \
                        mock.patch('strata_control.profiles.list_profiles', return_value=listing):
                    self.assertEqual(access.kept_key(), 'sk-strata-fixed-fixture')
                    self.assertEqual(path.read_bytes(), data)

    def test_failed_stop_reports_failure_and_nonzero_for_all_or_named_launch(self):
        for args in (['launch','--stop'], ['launch','--stop','--preset','Office']):
            with self.subTest(args=args), Home(), \
                    mock.patch('strata_control.profiles.list_profiles', return_value=[{'id':'launch-office'}]), \
                    mock.patch('strata_control.runtime.status', return_value={'state':'ready'}), \
                    mock.patch('strata_control.runtime.stop', return_value={'ok':False,'error':'fixture_not_ended'}) as stop, \
                    mock.patch.object(access, 'stop_tunnel'):
                code, out, err = run_cli(args)
            self.assertEqual(code, 1, err)
            self.assertIn('Could not stop office: fixture_not_ended.', out)
            self.assertNotIn('Nothing from `strata launch` is running.', out)
            stop.assert_called_once_with('launch-office', timeout=180)

    def test_one_successful_stop_does_not_hide_another_failure(self):
        with Home(), mock.patch('strata_control.profiles.list_profiles', return_value=[{'id':'launch-a'},{'id':'launch-b'}]), \
                mock.patch('strata_control.runtime.status', return_value={'state':'ready'}), \
                mock.patch('strata_control.runtime.stop', side_effect=[{'ok':True},{'ok':False,'error':'fixture_not_ended'}]), \
                mock.patch.object(access, 'stop_tunnel'):
            code, out, err = run_cli(['launch','--stop'])
        self.assertEqual(code, 1, err)
        self.assertIn('Stopped a.', out)
        self.assertIn('Could not stop b: fixture_not_ended.', out)
        self.assertNotIn('Nothing from `strata launch` is running.', out)


@unittest.skipUnless(shutil.which('git'), 'source updater requires Git')
class SourceUpdateHistory(unittest.TestCase):
    def engines(self):
        # CALL preserves this batch's EXIT /B status in the parent CMD process.
        result = [('bat', [os.environ.get('COMSPEC','cmd.exe'), '/d', '/c', 'call', 'UPDATE.bat'])] if os.name == 'nt' else []
        shell = shutil.which('sh')
        if not shell and Path('C:/Program Files/Git/bin/sh.exe').is_file():
            shell = 'C:/Program Files/Git/bin/sh.exe'
        if shell:
            result.append(('sh',[shell,'update.sh']))
        self.assertTrue(result, 'at least one source updater must execute')
        return result

    def scenario(self, command, branch, unrelated=False):
        with tempfile.TemporaryDirectory(prefix='strata-updater-ref-') as temp:
            root = Path(temp).resolve()
            env = {key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
            env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', LC_ALL='C',
                       GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                       GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
            if command[-1] == 'update.sh':
                env['PATH'] = str(Path(command[0]).parent)+os.pathsep+env.get('PATH','')

            def git(cwd, *args):
                cwd = Path(cwd).resolve()
                self.assertTrue(cwd == root or cwd.is_relative_to(root))
                return subprocess.run(['git','-c','index.threads=1','-c','core.fsmonitor=false',
                                       '-c','commit.gpgsign=false',*map(str,args)], cwd=cwd,env=env,
                                      check=True,capture_output=True,text=True,timeout=20).stdout.strip()

            def seed(folder):
                folder.mkdir()
                git(folder,'init','-b',branch)
                for name in ('UPDATE.bat','update.sh'):
                    shutil.copyfile(ROOT/name, folder/name)
                (folder/'START-HERE.bat').write_bytes(b'@echo off\r\n> "%~dp0setup-ran" echo fixture\r\nexit /b 0\r\n')
                (folder/'setup.sh').write_text('printf fixture > setup-ran\nexit 0\n',encoding='utf-8')
                git(folder,'add','.')
                git(folder,'commit','-m','initial fixture')

            origin, remote, local = root/'origin.git', root/'remote', root/'local'
            git(root,'init','--bare',origin)
            git(origin,'symbolic-ref','HEAD','refs/heads/'+branch)
            seed(remote)
            git(remote,'remote','add','origin',origin)
            git(remote,'push','-u','origin',branch)
            if unrelated:
                seed(local)
                # Distinct root commit even when files and timestamp match.
                git(local,'commit','--amend','-m','independent old history')
                git(local,'remote','add','origin',origin)
                git(local,'fetch','origin')
                git(local,'branch','--set-upstream-to','origin/'+branch,branch)
            else:
                git(root,'clone',origin,local)
                (local/'local.txt').write_text('local',encoding='utf-8')
                git(local,'add','local.txt')
                git(local,'commit','-m','local change')
                (remote/'remote.txt').write_text('remote',encoding='utf-8')
                git(remote,'add','remote.txt')
                git(remote,'commit','-m','remote change')
                git(remote,'push')
            model = local/'Strata-data'/'sentinel'
            model.parent.mkdir()
            model.write_bytes(b'private fixture data')
            before = git(local,'rev-parse','HEAD')
            result = subprocess.run(command,cwd=local,env=env,input='\n',capture_output=True,
                                    text=True,errors='replace',timeout=30,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            output = result.stdout+result.stderr
            self.assertEqual(model.read_bytes(), b'private fixture data')
            if unrelated:
                self.assertEqual(result.returncode, 0, output)
                self.assertIn('old history', output)
                self.assertEqual(git(local,'rev-parse','HEAD'),git(remote,'rev-parse','HEAD'))
                self.assertEqual(git(local,'rev-parse','pre-cleanup-backup'),before)
                self.assertTrue((local/'setup-ran').is_file())
            else:
                self.assertEqual(result.returncode, 1, output)
                self.assertNotIn('old history', output)
                self.assertIn('git pull did not succeed', output)
                self.assertEqual(git(local,'rev-parse','HEAD'),before)
                self.assertEqual(git(local,'branch','--list','pre-cleanup-backup*'),'')
                self.assertFalse((local/'setup-ran').exists())

    def test_diverged_fork_without_remote_main_is_not_a_history_rewrite(self):
        for name, command in self.engines():
            with self.subTest(engine=name):
                self.scenario(command,'memory-core')

    def test_normal_divergence_with_remote_main_preserves_local_history(self):
        for name, command in self.engines():
            with self.subTest(engine=name):
                self.scenario(command,'main')

    def test_actual_unrelated_history_still_recovers_and_keeps_backup(self):
        for name, command in self.engines():
            with self.subTest(engine=name):
                self.scenario(command,'main',unrelated=True)


if __name__ == '__main__':
    unittest.main()

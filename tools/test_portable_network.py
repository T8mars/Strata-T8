"""LAN configuration, owned service restarts, secret redaction and real HTTP authentication."""
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest import mock

import portable
from serve import api_access
from serve import portable_ui as ui
from tools import test_portable_ui as fixtures


class NetworkSettings(unittest.TestCase):
    setUp = fixtures.SetupPage.setUp
    tearDown = fixtures.SetupPage.tearDown
    request = fixtures.SetupPage.request
    model = fixtures.SetupPage.model
    payload = fixtures.SetupPage.payload
    wait_job = fixtures.SetupPage.wait_job
    fake_popen = fixtures.SetupPage.fake_popen

    def test_default_remains_local_and_saving_lan_requires_a_key_before_any_stop(self):
        self.assertEqual(self.app.snapshot()['network']['scope'],'local')
        with mock.patch.object(self.app,'stop_backend',side_effect=AssertionError('must not stop')):
            for key in ('','short','中文'*16,'a'*16+'\n','a'*257):
                status,_,body=self.request('/api/network',{'scope':'lan','api_key':key})
                self.assertEqual(status,400)
                if key and len(key)>16:self.assertNotIn(key,json.dumps(body))
        self.assertFalse(portable.STATE.exists())

    def test_saves_before_model_exists_and_never_returns_the_key(self):
        key='private-fixture-key-12345'
        self.assertEqual(self.request('/api/network',{'scope':'lan','api_key':key})[0],202)
        self.assertEqual(self.wait_job(('idle','error')),'idle')
        self.assertEqual(portable.read_state()['portable_api'],{'scope':'lan','api_key':key})
        self.assertNotIn(key,json.dumps(self.request('/api/status')[2]))
        self.assertIsNone(self.app.process)
        self.assertEqual(self.http.server_address[0],'127.0.0.1')
        self.assertEqual(self.request('/api/network',{'scope':'lan','clear_api_key':True})[0],400)
        self.assertEqual(self.request('/api/network',{'scope':'local','clear_api_key':True})[0],202)
        self.assertEqual(self.wait_job(('idle','error')),'idle')
        self.assertEqual(portable.read_state()['portable_api']['api_key'],'')

    def test_token_is_required_and_invalid_requests_do_not_change_files(self):
        for body in ({'scope':'lan','api_key':'x'*20,'extra':True},{'scope':'all'}, {'scope':'local','clear_api_key':1}):
            self.assertEqual(self.request('/api/network',body)[0],400)
        self.assertEqual(self.request('/api/network',{'scope':'lan','api_key':'x'*20},
                                      {'X-Strata-Setup-Token':'wrong'})[0],403)
        self.assertFalse(portable.STATE.exists())

    def test_apply_restart_switch_and_local_revert_use_global_settings(self):
        spawn,created=self.fake_popen()
        key='private-fixture-key-12345'
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn) as calls:
            self.assertEqual(self.request('/api/import',self.payload())[0],202)
            self.assertEqual(self.wait_job(),'ready',self.app.job)
            first=self.app.process
            self.assertEqual(self.request('/api/network',{'scope':'lan','api_key':key})[0],202)
            self.assertEqual(self.wait_job(),'ready')
            self.assertIsNotNone(first.poll())
            self.assertIsNot(self.app.process,first)
            cfg=portable.read_config(self.app.current_config)
            self.assertEqual((cfg['host'],cfg['api_key']),('0.0.0.0',key))
            self.assertEqual(calls.call_args.args[0][calls.call_args.args[0].index('--host')+1],'0.0.0.0')
            self.assertNotIn(key,repr(calls.call_args))
            with mock.patch.object(api_access,'ipv4_addresses',return_value=['192.168.1.23']):
                snap=self.app.snapshot()
                self.assertEqual(snap['api_connection']['lan_base_urls'],[f'http://192.168.1.23:{self.app.backend_port}/v1'])
                self.assertNotIn(key,json.dumps(snap))
            self.assertEqual(self.request('/api/import',self.payload())[0],202)
            self.assertEqual(self.wait_job(),'ready',self.app.job)
            self.assertEqual(portable.read_config(self.app.current_config)['api_key'],key)
            self.assertEqual(self.request('/api/network',{'scope':'local','clear_api_key':True})[0],202)
            self.assertEqual(self.wait_job(),'ready')
            cfg=portable.read_config(self.app.current_config)
            self.assertEqual(cfg['host'],'127.0.0.1');self.assertNotIn('api_key',cfg)

    def test_failed_restart_restores_state_and_config_and_stops_failed_child(self):
        spawn,_=self.fake_popen()
        with mock.patch.object(ui.subprocess,'Popen',side_effect=spawn):
            self.request('/api/import',self.payload());self.assertEqual(self.wait_job(),'ready')
        before=portable.STATE.read_bytes();path=self.app.current_config;config=path.read_bytes()
        with mock.patch.object(self.app,'_start_backend',side_effect=OSError('fixture failure')):
            self.assertEqual(self.request('/api/network',{'scope':'lan','api_key':'secret-fixture-key'})[0],202)
            self.assertEqual(self.wait_job(),'error',self.app.job)
        self.assertIn('原设置已保留',self.app.job['message'])
        self.assertEqual(portable.STATE.read_bytes(),before)
        self.assertEqual(path.read_bytes(),config)
        self.assertIsNone(self.app.process)

    def test_ipv4_addresses_only_include_active_private_interfaces(self):
        import psutil
        addr=lambda ip:SimpleNamespace(family=socket.AF_INET,address=ip)
        addresses={'wifi':[addr('192.168.0.5'),addr('127.0.0.1'),addr('169.254.1.3'),addr('8.8.8.8'),addr('240.0.0.1')],
                   'vpn':[addr('10.4.5.6')],'off':[addr('192.168.0.9')]}
        stats={name:SimpleNamespace(isup=name!='off') for name in addresses}
        with mock.patch.object(psutil,'net_if_addrs',return_value=addresses),mock.patch.object(psutil,'net_if_stats',return_value=stats):
            self.assertEqual(api_access.ipv4_addresses(),['192.168.0.5','10.4.5.6'])


class LanAuthentication(unittest.TestCase):
    def test_headless_applies_saved_lan_key_and_ignores_an_inherited_key(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cfg=root/'strata-test.json';state=root/'state.json'
            portable.save_json(cfg,{'port':8081})
            fingerprint={'gpu':[0],'version':'fixture'}
            portable.save_json(state,{'portable_config':cfg.name,'portable_fingerprint':fingerprint,
                                      'portable_api':{'scope':'lan','api_key':'configured-fixture-key'}})
            with mock.patch.object(portable,'ROOT',root),mock.patch.object(portable,'STATE',state),\
                 mock.patch('sys.argv',['portable.py','start','--no-browser']),mock.patch('os.chdir'),\
                 mock.patch.object(portable,'environment_check'),mock.patch.object(portable,'isolate_setup'),\
                 mock.patch.object(portable,'data_path',return_value=root/'model'),mock.patch.object(portable,'model_delivery'),\
                 mock.patch.object(portable,'fingerprint',return_value=fingerprint),mock.patch('threading.Thread'),\
                 mock.patch.dict(os.environ,{'STRATA_API_KEY':'unexpected-inherited-key'}),\
                 mock.patch('subprocess.call',return_value=0) as launch:
                self.assertEqual(portable.main(),0)
                self.assertNotIn('STRATA_API_KEY',launch.call_args.kwargs['env'])
                self.assertNotIn('configured-fixture-key',str(launch.call_args))
            saved=portable.read_json(cfg)
            self.assertEqual((saved['host'],saved['api_key']),('0.0.0.0','configured-fixture-key'))

    def test_actual_api_rejects_missing_wrong_keys_and_accepts_bearer_on_lan_binding(self):
        from serve.server import ByteTokenizer,MockEngine,Service,serve
        from serve.frontend import ChatTemplate
        tok=ByteTokenizer();service=Service(MockEngine(tok,'ok'),tok,ChatTemplate(ui.ROOT/'serve/chat_template.jinja'))
        service.api_key='lan-auth-fixture-key-123'
        http=serve(service,host='0.0.0.0',port=0)
        try:
            hosts=['127.0.0.1',*api_access.ipv4_addresses()[:1]]
            for host in hosts:
                for headers,expected in (({},401),({'Authorization':'Bearer wrong'},401),
                        ({'Authorization':'Bearer '+service.api_key},200),({'x-api-key':service.api_key},200)):
                    conn=HTTPConnection(host,http.server_port,timeout=5)
                    try:
                        conn.request('GET','/v1/models',headers=headers);response=conn.getresponse();response.read()
                        self.assertEqual(response.status,expected)
                    finally:conn.close()
        finally:http.shutdown();http.server_close()


if __name__=='__main__':unittest.main()

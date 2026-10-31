"""Interrupted public-asset downloads must recover without accepting mixed bytes."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import os
import subprocess
from unittest import mock

from tools import portable_update as update


class Response(io.BytesIO):
    def __init__(self, data, *, total=None, status=200, offset=0, interrupt=False):
        super().__init__(data)
        self.status=status
        self.headers={'Content-Length':str(len(data) if total is None else total-offset)}
        if status==206:self.headers['Content-Range']=f'bytes {offset}-{total-1}/{total}'
        self.interrupt=interrupt

    def read1(self, size):
        if self.interrupt and self.tell():raise TimeoutError('fixture connection lost')
        return super().read(min(size,3))


class Downloads(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.target=self.root/'component.zip'
        self.console=io.StringIO()
        self.silence=contextlib.redirect_stdout(self.console)
        self.silence.__enter__()
        self.sleep=mock.patch.object(update.time,'sleep')
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop();self.silence.__exit__(None,None,None);self.temporary.cleanup()

    def test_interrupted_file_resumes_at_written_offset_and_matches_full_digest(self):
        payload=b'0123456789'
        with mock.patch.object(update,'urlopen',side_effect=[Response(payload,total=10,interrupt=True),Response(payload[3:],total=10,status=206,offset=3)]) as network:
            self.assertEqual(update.fetch('https://github.com/fixture/component.zip',self.target),10)
        self.assertEqual(network.call_args_list[1].args[0].get_header('Range'),'bytes=3-')
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).digest(),hashlib.sha256(payload).digest())
        self.assertIn('retry 2/3',self.console.getvalue())

    def test_ignored_range_restarts_instead_of_appending_complete_body(self):
        payload=b'0123456789'
        with mock.patch.object(update,'urlopen',side_effect=[Response(payload,total=10,interrupt=True),Response(payload)]):
            self.assertEqual(update.fetch('https://github.com/fixture/component.zip',self.target),10)
        self.assertEqual(self.target.read_bytes(),payload)

    def test_wrong_range_does_not_append_or_publish_a_plan(self):
        with mock.patch.object(update,'urlopen',side_effect=[Response(b'0123456789',total=10,interrupt=True),Response(b'456789',total=10,status=206,offset=4)]):
            with self.assertRaisesRegex(ValueError,'range differs'):update.fetch('https://github.com/fixture/component.zip',self.target)
        self.assertEqual(self.target.read_bytes(),b'012')
        self.assertFalse((self.root/'plan.json').exists())

    def test_premature_eof_with_content_length_is_retried(self):
        with mock.patch.object(update,'urlopen',side_effect=[Response(b'012',total=10),Response(b'3456789',total=10,status=206,offset=3)]):
            self.assertEqual(update.fetch('https://github.com/fixture/component.zip',self.target),10)
        self.assertEqual(self.target.read_bytes(),b'0123456789')

    def test_metadata_retry_discards_partial_memory(self):
        with mock.patch.object(update,'urlopen',side_effect=[Response(b'{"ok":true}',interrupt=True),Response(b'{"ok":true}')]):
            self.assertEqual(json.loads(update.fetch('https://github.com/fixture/index.json')),{'ok':True})

    def test_nonretryable_http_error_and_transient_retry_limit_are_explicit(self):
        for status,attempts in ((404,1),(503,3)):
            with self.subTest(status=status),mock.patch.object(update,'urlopen',side_effect=urllib.error.HTTPError('https://github.com/fixture',status,'fixture',{},None)) as network:
                with self.assertRaisesRegex(RuntimeError,'installation is unchanged'):update.fetch('https://github.com/fixture/index.json')
                self.assertEqual(network.call_count,attempts)

    def test_oversized_headers_and_body_cannot_be_saved_as_valid_download(self):
        for response in (Response(b'012345',total=6),Response(b'012345')):
            with self.subTest(response=response),mock.patch.object(update,'urlopen',return_value=response):
                with self.assertRaisesRegex(ValueError,'Oversized'):update.fetch('https://github.com/fixture/component.zip',self.target,limit=5)
        self.assertFalse(self.target.exists())

    def test_total_deadline_is_bounded_even_when_data_keeps_arriving(self):
        with mock.patch.object(update,'urlopen',return_value=Response(b'data')),mock.patch.object(update,'DOWNLOAD_DEADLINE',-1):
            with self.assertRaisesRegex(RuntimeError,'exceeded one hour'):update.fetch('https://github.com/fixture/component.zip',self.target)

    def test_real_http_disconnect_resumes_and_verifies_every_byte(self):
        payload=b'bounded offline HTTP fixture'*4096
        ranges=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                value=self.headers.get('Range');ranges.append(value)
                start=int(value.removeprefix('bytes=').removesuffix('-')) if value else 0
                self.send_response(206 if value else 200)
                self.send_header('Content-Length',str(len(payload)-start))
                if value:self.send_header('Content-Range',f'bytes {start}-{len(payload)-1}/{len(payload)}')
                self.end_headers()
                self.wfile.write(payload[start:] if value else payload[:1024]);self.wfile.flush()
                self.close_connection=True
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def local_open(request,timeout):
            return urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}/fixture',headers=dict(request.header_items())),timeout=timeout)
        try:
            with mock.patch.object(update,'urlopen',side_effect=local_open):
                self.assertEqual(update.fetch('https://github.com/offline-fixture/component.zip',self.target),len(payload))
            self.assertEqual(ranges,[None,'bytes=1024-'])
            self.assertEqual(self.target.read_bytes(),payload)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)


@unittest.skipUnless(os.name=='nt','Windows native update path checks')
class NativePathChecks(unittest.TestCase):
    def test_long_and_dangling_junctions_are_refused_without_following_them(self):
        with tempfile.TemporaryDirectory(prefix='T8 path checks ') as temporary:
            base=Path(temporary).resolve();root=base/'app';root.mkdir()
            outside=base/'outside';outside.mkdir();(outside/'keep.txt').write_text('private sentinel')
            deep=root
            while len(str(deep))<285:deep=deep/'long-directory-0123456789'
            deep.mkdir(parents=True)
            links=[deep/'linked',root/'dangling']
            gone=base/'gone';gone.mkdir()
            try:
                for link,target in zip(links,[outside,gone]):
                    result=subprocess.run(['cmd','/d','/c','mklink','/J','\\\\?\\'+str(link),'\\\\?\\'+str(target)],capture_output=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                gone.rmdir()
                config=base/'paths.json';config.write_text(json.dumps({'root':str(root),'links':[str(p) for p in links]}))
                script=base/'check.ps1'
                script.write_text('''param([string]$Source,[string]$Config)
$ErrorActionPreference='Stop'
$text=[IO.File]::ReadAllText($Source)
$native=[regex]::Match($text,"(?s)Add-Type -TypeDefinition @'\\r?\\n(.*?)\\r?\\n'@").Groups[1].Value
Add-Type -TypeDefinition $native
$tokens=$null;$errors=$null
$tree=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
$definition=$tree.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'ScopedPath'},$true).Extent.Text
Invoke-Expression $definition
$paths=Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
foreach($link in $paths.links) {
    if(![T8Paths]::IsReparse($link)){throw 'Junction was treated as a missing or ordinary path'}
    $relative=$link.Substring($paths.root.Length+1).Replace('\\','/')
    $blocked=$false
    try {$null=ScopedPath $paths.root ($relative+'/keep.txt')}
    catch {if($_.Exception.Message -notlike 'Linked path:*'){throw};$blocked=$true}
    if(!$blocked){throw 'ScopedPath accepted a junction'}
}
if([T8Paths]::IsReparse($paths.root)){throw 'Ordinary directory rejected'}
Write-Output 'path-guards-ok'
''',encoding='utf-8')
                source=Path(__file__).resolve().parent/'apply_portable_update.ps1'
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Source',str(source),'-Config',str(config)],capture_output=True,timeout=25)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn(b'path-guards-ok',result.stdout)
                self.assertEqual((outside/'keep.txt').read_text(),'private sentinel')
            finally:
                for link in links:
                    if link.is_junction():link.rmdir()  # Remove the link itself, never its target.


if __name__=='__main__':unittest.main()

"""Real Windows update-helper handoff in a temporary, model-free application."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows update helper')
class WindowsUpdateHelper(unittest.TestCase):
    def exercise(self, fail=False):
        with tempfile.TemporaryDirectory(prefix='T8 更新 中文 空格 ') as temporary:
            root=Path(temporary)/'app'; (root/'runtime/python').mkdir(parents=True)
            (root/'tools').mkdir()
            python=Path(sys.executable)
            # The bundled interpreter's standard library is enough for this
            # fixture; no CUDA DLLs, site-packages, model or network download.
            for source in python.parent.iterdir():
                if source.is_file() and (source.suffix.lower() in ('.dll','.pyd','.zip','._pth') or source.name=='python.exe'):
                    shutil.copy2(source,root/'runtime/python'/source.name)
            (root/'logs').mkdir()
            status=Path(temporary)/'handoff.json'
            settings=root/'portable-settings.json';settings.write_bytes(b'{"portable_data_dir":"D:/my model"}')
            original=settings.read_bytes()
            (root/'tools/portable_update.py').write_text('''from pathlib import Path
import json
r=Path(__file__).resolve().parents[1];p=r/'.portable-update';p.mkdir(exist_ok=True)
print('[Update] fixture download progress',flush=True)
(p/'plan.json').write_text('{}')
(p/'apply.ps1').write_text("param([string]$PlanPath)\\nSet-Content -LiteralPath (Join-Path (Split-Path $PlanPath) 'applied.txt') -Value 'applied'\\nexit 0\\n")
''',encoding='utf-8')
            if fail:
                (root/'tools/portable_update.py').write_text("print('[Update] fixture download progress',flush=True)\nraise SystemExit(7)\n",encoding='utf-8')
            (root/'portable.py').write_text('''import json,sys
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
r=Path.cwd();(r/'restart-args.json').write_text(json.dumps(sys.argv[1:]))
assert (r/'.portable-update/applied.txt').exists()
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  b=json.dumps({'service':'strata-portable-ui','app_root':str(r)}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def log_message(self,*a):pass
s=HTTPServer(('127.0.0.1',int(sys.argv[-1])),H);s.handle_request();s.server_close()
''',encoding='utf-8')
            if fail:
                script=root/'portable.py'
                script.write_text(script.read_text().replace("assert (r/'.portable-update/applied.txt').exists()","assert not (r/'.portable-update/applied.txt').exists()"),encoding='utf-8')
            with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            parent=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.5)'])
            try:
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/gui_portable_update.ps1'),
                    '-AppRoot',str(root),'-ParentId',str(parent.pid),'-UIPort',str(port),'-NonInteractive',
                    '-StatusFile',str(status),'-HandoffNonce','a'*32,'-LogPath',str(root/'logs/update-helper.log')],
                    capture_output=True,timeout=55,creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode,1 if fail else 0,result.stdout+result.stderr)
                if fail:
                    deadline=time.monotonic()+8
                    while True:
                        try:
                            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/identity',timeout=1) as response:response.read()
                            break
                        except OSError:
                            if time.monotonic()>deadline:raise
                            time.sleep(.05)
                    time.sleep(.2)
                self.assertEqual(settings.read_bytes(),original)
                self.assertEqual(json.loads((root/'restart-args.json').read_text())[-3:],['settings','--ui-port',str(port)])
                receipt=json.loads(status.read_text('utf-8-sig'))
                self.assertEqual(receipt['state'],'error' if fail else 'completed')
                self.assertEqual(receipt['nonce'],'a'*32)
                self.assertEqual(receipt['parent_id'],parent.pid)
                if not fail:self.assertIn('Opening model settings',(root/'logs/update-helper.log').read_text('utf-8-sig'))
                self.assertIn('fixture download progress',(root/'logs/update-helper.log').read_text('utf-8-sig'))
                self.assertEqual(json.loads((root/'.portable-update/gui-result.json').read_text('utf-8-sig'))['state'],'error' if fail else 'completed')
            finally:
                if parent.poll() is None:parent.terminate()
                parent.wait(timeout=5)

    def test_wait_download_apply_and_restart_preserve_user_settings(self):
        self.exercise()

    def test_failed_download_reopens_settings_and_retains_error_and_private_data(self):
        self.exercise(fail=True)

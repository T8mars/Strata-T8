"""The launcher must remain usable when an updater never starts or returns a foreign receipt."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from serve import portable_ui as ui


class Handoff(unittest.TestCase):
    def test_no_receipt_times_out_instead_of_closing_launcher(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(ui,'UPDATE_READY_SECONDS',0):
            with self.assertRaisesRegex(ui.SetupError,'未就绪'):
                ui.wait_update_helper(mock.Mock(poll=lambda:None),Path(temporary)/'missing.json','a'*32,Path(temporary))

    def test_receipt_must_match_current_parent_installation_nonce_and_ready_phase(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path=root/'status.json'
            base = {'state':'waiting_parent','nonce':'a'*32,'app_root':str(root),'parent_id':os.getpid()}
            for replacement in ({'nonce':'b'*32},{'app_root':'other'},{'parent_id':-1},{'state':'error','error':'blocked by policy'}):
                with self.subTest(replacement=replacement):
                    path.write_text(json.dumps({**base,**replacement}))
                    with self.assertRaises(ui.SetupError):
                        ui.wait_update_helper(mock.Mock(poll=lambda:None),path,'a'*32,root)
            path.write_text(json.dumps(base))
            ui.wait_update_helper(mock.Mock(poll=lambda:None),path,'a'*32,root)
            with self.assertRaisesRegex(ui.SetupError,'提前退出'):
                ui.wait_update_helper(mock.Mock(poll=lambda:1),path,'a'*32,root)

    @unittest.skipUnless(os.name=='nt','Actual Windows PowerShell exit')
    def test_actual_powershell_early_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            process=subprocess.Popen(['powershell.exe','-NoProfile','-Command','exit 7'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                with self.assertRaisesRegex(ui.SetupError,'提前退出.*7'):
                    ui.wait_update_helper(process,Path(temporary)/'missing.json','a'*32,Path(temporary))
            finally:
                if process.poll() is None:process.terminate()
                process.wait(timeout=5)

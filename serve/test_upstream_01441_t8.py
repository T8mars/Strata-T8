"""Upstream startup/cache improvements retain T8 cancellation and temporary-file ownership."""
import copy
from pathlib import Path
import sys,tempfile,threading,unittest
from unittest import mock
from serve import server
from serve.frontend import ChatTemplate

ROOT=Path(__file__).resolve().parents[1]


class Compatibility(unittest.TestCase):
    def test_cancel_during_transient_retry_never_starts_a_second_child(self):
        event=threading.Event()
        class CancelInBackoff:
            def is_set(self):return event.is_set()
            def wait(self,timeout):event.set();return True
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'fake_engine.py';script.write_text('import sys; sys.exit(0)')
            processes=[];popen=server.subprocess.Popen
            def spawn(command,**kwargs):
                process=popen([sys.executable,str(script),*command[1:]],**kwargs)
                processes.append(process);return process
            with mock.patch.object(server.subprocess,'Popen',side_effect=spawn),\
                 mock.patch.object(server,'START_RETRIES',2):
                with self.assertRaises(server.RequestCancelled):
                    server.StrataEngine('strata',[],log=str(Path(directory)/'start.log'),
                                        cancel=CancelInBackoff(),start_timeout_s=30)
            self.assertEqual(len(processes),1)
            self.assertIsNotNone(processes[0].poll())

    def test_retry_backoff_respects_the_original_start_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'fake_engine.py';script.write_text('import sys; sys.exit(0)')
            popen=server.subprocess.Popen;processes=[]
            def spawn(command,**kwargs):
                process=popen([sys.executable,str(script),*command[1:]],**kwargs)
                processes.append(process);return process
            with mock.patch.object(server.subprocess,'Popen',side_effect=spawn),\
                 mock.patch.object(server,'START_RETRIES',2):
                with self.assertRaises(server.EngineStarting):
                    server.StrataEngine('strata',[],log=str(Path(directory)/'start.log'),start_timeout_s=.5)
            self.assertEqual(len(processes),1)
            self.assertIsNotNone(processes[0].poll())

    def test_image_cache_keys_and_snapshot_survive_eviction_then_clear_together(self):
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);tok=server.ByteTokenizer()
            class Encoder:
                dir=d
                def alive(self):return True
                def encode(self,source,**kwargs):
                    path=d/('0123456789abcdef'+'0'*48+'.sve')
                    path.write_bytes(b'protected image rows');return path,3
            service=server.Service(server.MockEngine(tok,'ok',max_context=4096),tok,
                ChatTemplate(ROOT/'serve/chat_template.jinja'),vision=Encoder())
            messages=[{'role':'user','content':[{'type':'text','text':'look'},{'type':'image','source':'x.png'}]}]
            service.prepare(copy.deepcopy(messages),None,{},16)
            snapshot=service.embeddings.path
            self.assertEqual(service.embeddings.keys,['0123456789abcdef'])
            (d/('0123456789abcdef'+'0'*48+'.sve')).unlink()  # another turn evicts the encoder cache
            self.assertEqual(snapshot.read_bytes(),b'protected image rows')
            service.discard_embeddings()
            self.assertFalse(snapshot.exists())
            self.assertIsNone(service.embeddings.keys)

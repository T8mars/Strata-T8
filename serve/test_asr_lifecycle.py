"""Exercise real owned subprocess cancellation, faults and cache boundaries without models."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import numpy as np
from serve.asr import config, contract
from serve.asr.manager import AsrManager
from serve.asr.integration import gpu_scope


class OwnedWorker(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.worker=self.root/'serve/asr/worker.py'
        # These workers do not use real ASR devices. Exercise real file locks
        # inside the fixture without competing with a user's running service.
        local_state=mock.patch.dict('os.environ',{'LOCALAPPDATA':str(self.root/'appdata')})
        local_state.start();self.addCleanup(local_state.stop)
        self.worker.parent.mkdir(parents=True)
        self.manager=AsrManager(self.root)
        extracted={'status':'ready','pcm':np.ones(32000,np.float32),'window_start_s':0,'window_end_s':2}
        for patch in (mock.patch('serve.media_jobs.extract_audio',return_value=extracted),
                      mock.patch('serve.asr.media.source_identity',return_value='same-media'),
                      mock.patch.object(config,'availability',return_value={'supported':True}),
                      mock.patch.object(config,'runtime_paths',return_value=(Path(sys.executable),self.root/'native')),
                      mock.patch.object(self.manager,'_signature',return_value={'model':'fixture'})):
            patch.start();self.addCleanup(patch.stop)

    def write_worker(self,code):
        self.worker.write_text('import json,sys,time\nfrom pathlib import Path\njob=json.loads(Path(sys.argv[1]).read_text())\n'+code,encoding='utf-8')

    def complete_worker(self):
        self.write_worker("Path(job['result']).write_text(json.dumps({'status':'completed','audio_samples_16k':32000,'text':'hello','segments':[{'start_sample':0,'end_sample':32000,'text':'hello'}]}))\n")

    def test_cancel_terminates_owned_worker_and_leaves_no_completed_cache(self):
        self.write_worker("time.sleep(30)\n")
        cancel=threading.Event();started=threading.Event();processes=[];errors=[]
        real=subprocess.Popen
        def spawn(*args,**kwargs):
            process=real(*args,**kwargs);processes.append(process);started.set();return process
        def run():
            try:self.manager.transcribe(io.BytesIO(b'fixture'),cancel=cancel)
            except BaseException as error:errors.append(error)
        with mock.patch('subprocess.Popen',side_effect=spawn):
            thread=threading.Thread(target=run);thread.start()
            self.assertTrue(started.wait(5));cancel.set();thread.join(12)
        self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],InterruptedError)
        self.assertIsNotNone(processes[0].poll());self.assertEqual(self.manager.progress()['phase'],'cancelled')
        self.assertEqual(list((self.root/'.asr-cache').glob('*.json')),[])

    def test_worker_failure_releases_lock_and_next_job_succeeds(self):
        self.write_worker("raise RuntimeError('fixture worker failure')\n")
        with self.assertRaisesRegex(RuntimeError,'fixture worker failure'):self.manager.transcribe(io.BytesIO(b'x'))
        self.complete_worker();result=self.manager.transcribe(io.BytesIO(b'x'))
        self.assertEqual(result['cues'][0]['text'],'hello');self.assertTrue(result['requires_review'])

    def test_cache_is_complete_only_and_worker_source_changes_invalidate_it(self):
        self.complete_worker();self.manager.transcribe(io.BytesIO(b'x'))
        with mock.patch('subprocess.Popen',side_effect=AssertionError('cache should avoid worker')):
            self.assertTrue(self.manager.transcribe(io.BytesIO(b'x'))['cache_hit'])
        self.worker.write_text(self.worker.read_text()+'# changed worker\n',encoding='utf-8')
        self.assertFalse(self.manager.transcribe(io.BytesIO(b'x'))['cache_hit'])

    def test_oversized_worker_result_is_rejected_before_json_decode(self):
        self.write_worker("Path(job['result']).write_text('x'*(2*1024**2+1))\n")
        with self.assertRaisesRegex(RuntimeError,'oversized'):self.manager.transcribe(io.BytesIO(b'x'))


class Boundaries(unittest.TestCase):
    def test_partial_pe_file_is_rejected_instead_of_staged_as_ready(self):
        from tools.stage_asr_runtime import verify_pe
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'unfinished.pyd';path.write_bytes(b'\0'*2048)
            with self.assertRaisesRegex(ValueError,'Incomplete'):verify_pe(path)

    def test_vad_is_external_and_asr_configuration_is_never_shipped(self):
        from tools.portable_update import safe_path
        with tempfile.TemporaryDirectory() as root:
            speech = Path(root).resolve()/'external-speech'
            self.assertEqual(config.vad_path(root,{**config.DEFAULTS,'model_dir':str(speech)}),speech/'FireRedVAD-ONNX')
            for name in ('asr-settings.json','.asr-cache/transcript.json'):
                with self.assertRaises(ValueError):safe_path(Path(root),name)

    def test_gpu_asr_does_not_unload_busy_or_parallel_main_engine(self):
        for busy,batch in ((True,0),(False,2)):
            service=mock.Mock();service.fifo=threading.Lock();service.status_lock=threading.Lock()
            service.status={'busy':busy};service.engine.batch=batch
            with self.assertRaises(RuntimeError),gpu_scope(service):pass
            service.engine.unload.assert_not_called()
            self.assertTrue(service.fifo.acquire(blocking=False));service.fifo.release()

    def test_imported_confucius_review_status_and_ownership_are_preserved(self):
        raw={'status':'requires_review','audio_samples_16k':32000,'text':'hello',
             'segments':[{'start_sample':0,'end_sample':16000,'speech_start_sample':4000,'speech_end_sample':12000,'text':'hello'}]}
        self.assertTrue(contract.from_confucius(raw)['cues'][0]['requires_review'])
        raw['segments'][0]['speech_end_sample']=20000
        with self.assertRaisesRegex(ValueError,'interval'):contract.from_confucius(raw)
        with self.assertRaises(ValueError):contract.subtitle_text('1\n00:00:65,000 --> 00:02:00,000\ninvalid')


if __name__=='__main__':unittest.main()

"""Real disposable decoder jobs, bounded IPC and server survival; no models."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
import numpy as np
from serve import media_jobs, video
from serve import test_asr as asr_fixtures
from tools.test_strata_video import write_video
from serve import test_video as video_fixtures


class IsolatedMedia(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.path=self.root/'source.mp4';write_video(self.path,[0,.5,1,1.5],rate=2)
        self.data=self.path.read_bytes()
        self.options={'start_s':0,'end_s':2,'sampling_fps':2,'max_frames':8,'mode':'frames','allow_empty_segment':False}
        self.processes=[]

    def fixture_worker(self,code):
        path=self.root/'fixture_worker.py'
        path.write_text('import os,sys,time,json\nfrom pathlib import Path\njob=Path(sys.argv[1])\n'+code,encoding='utf-8')
        return path

    def spawn(self,args,**kwargs):
        process=self.real_spawn(args,**kwargs);self.processes.append(process);return process

    def assert_exited(self):
        self.assertTrue(self.processes)
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_real_video_roundtrip_frames_native_and_no_parent_decoder_import(self):
        self.real_spawn=subprocess.Popen
        with mock.patch('subprocess.Popen',side_effect=self.spawn),mock.patch('tools.strata_video.sample_video',side_effect=AssertionError('parent native decoding')):
            for mode in ('frames','native'):
                result=media_jobs.sample_video(self.data,{**self.options,'mode':mode})
                self.assertEqual(result['part']['mode'],mode)
                self.assertEqual([frame['timestamp_s'] for frame in result['part']['frames']],[0,.5,1,1.5])
                self.assertEqual(result['manifest']['source']['duration_s'],2)
                self.assertNotIn('media_worker_retries',result)
        self.assert_exited()

    def test_real_audio_pcm_roundtrip_preserves_pts_and_delayed_samples(self):
        fixture=asr_fixtures.AudioClock()
        direct=fixture.wav();source=io.BytesIO(direct.getvalue());source.seek(7)
        with mock.patch('serve.asr.media.extract',side_effect=AssertionError('parent native extraction')):
            result=media_jobs.extract_audio(source,start_s=.5,end_s=1.25)
        self.assertEqual(source.tell(),7)
        self.assertEqual((result['window_start_s'],result['window_end_s'],result['pcm'].size),(.5,1.25,12000))
        self.assertGreater(float(np.median(result['pcm'])),.3)
        result=media_jobs.extract_audio(fixture.offset_media(),start_s=0,end_s=1.5)
        self.assertAlmostEqual(result['source_origin_s'],5)
        self.assertTrue(np.all(result['pcm'][:8000]==0))
        self.assertTrue(np.all(result['pcm'][8000:23000]>.3))

    def test_local_audio_file_and_no_track_keep_original_status(self):
        path=self.root/'sound.wav';path.write_bytes(asr_fixtures.AudioClock().wav().getvalue())
        result=media_jobs.extract_audio(path,start_s=0,end_s=1)
        self.assertEqual(result['pcm'].size,16000)
        self.assertEqual(media_jobs.extract_audio(self.path,start_s=0,end_s=1)['status'],'no_track')

    def test_empty_optin_and_validation_errors_do_not_become_success(self):
        options={**self.options,'start_s':.1,'end_s':.2,'allow_empty_segment':True}
        result=media_jobs.sample_video(self.data,options)
        self.assertEqual(result['part'],None);self.assertTrue(result['empty_segment'])
        with self.assertRaisesRegex(ValueError,'no decodable'):
            media_jobs.sample_video(self.data,{**options,'allow_empty_segment':False})
        with self.assertRaises(ValueError) as invalid:
            media_jobs.sample_video(b'not-media',self.options)
        self.assertNotIn('input.media',str(invalid.exception))
        self.assertNotIn('strata-media-',str(invalid.exception))
        with self.assertRaisesRegex(ValueError,'track'):
            media_jobs.extract_audio(asr_fixtures.AudioClock().wav(),audio_track=1,start_s=0,end_s=1)

    def test_native_exit_is_clear_error_and_next_real_job_recovers(self):
        worker=self.fixture_worker("print('fixture native exit',flush=True)\nos._exit(99)\n")
        self.real_spawn=subprocess.Popen
        with mock.patch.object(media_jobs,'_WORKER_SCRIPT',worker),mock.patch('subprocess.Popen',side_effect=self.spawn):
            with self.assertRaisesRegex(media_jobs.MediaWorkerError,'exit 99'):
                media_jobs.sample_video(self.data,self.options)
        self.assert_exited()
        self.assertEqual(len(self.processes),2)
        self.assertEqual(len(media_jobs.sample_video(self.data,self.options)['part']['frames']),4)

    def test_transient_native_exit_retries_cleanly_then_runs_actual_decoder(self):
        actual=str(media_jobs._WORKER_SCRIPT.resolve())
        worker=self.fixture_worker("counter=job.with_name('attempts'); n=int(counter.read_text()) if counter.exists() else 0\n"
            "counter.write_text(str(n+1))\n"
            "if n==0:\n job.with_name('result.json').write_text('{\"result\":{\"partial\":true}}')\n job.with_name('audio.f32').write_bytes(b'partial')\n os._exit(99)\n"
            "assert not job.with_name('result.json').exists() and not job.with_name('audio.f32').exists()\n"
            "import runpy\nrunpy.run_path("+repr(actual)+",run_name='__main__')\n")
        self.real_spawn=subprocess.Popen
        with mock.patch.object(media_jobs,'_WORKER_SCRIPT',worker),mock.patch('subprocess.Popen',side_effect=self.spawn):
            result=media_jobs.sample_video(self.data,self.options)
            sound=media_jobs.extract_audio(asr_fixtures.AudioClock().wav(),start_s=0,end_s=1)
        self.assertEqual(result['media_worker_retries'],1)
        self.assertEqual([frame['timestamp_s'] for frame in result['part']['frames']],[0,.5,1,1.5])
        self.assertEqual((sound['media_worker_retries'],sound['pcm'].size),(1,16000))
        self.assertEqual(len(self.processes),4);self.assert_exited()

    def test_cancel_after_native_exit_prevents_second_spawn(self):
        worker=self.fixture_worker('os._exit(99)\n')
        self.real_spawn=subprocess.Popen
        cancel=threading.Event();stop=media_jobs._stop
        def stopped(process):
            stop(process);cancel.set()
        with mock.patch.object(media_jobs,'_WORKER_SCRIPT',worker),mock.patch('subprocess.Popen',side_effect=self.spawn), \
                mock.patch.object(media_jobs,'_stop',side_effect=stopped):
            with self.assertRaises(InterruptedError):
                media_jobs.sample_video(self.data,self.options,cancel)
        self.assertEqual(len(self.processes),1);self.assert_exited()
        self.assertFalse(media_jobs._MEDIA_LOCK.locked())

    def test_timeout_terminates_and_waits_for_owned_worker(self):
        worker=self.fixture_worker('time.sleep(30)\n')
        self.real_spawn=subprocess.Popen
        with mock.patch.object(media_jobs,'_WORKER_SCRIPT',worker),mock.patch('subprocess.Popen',side_effect=self.spawn):
            with self.assertRaisesRegex(TimeoutError,'time budget'):
                media_jobs.sample_video(self.data,self.options,timeout_s=.5)
        self.assert_exited()
        self.assertEqual(len(self.processes),1)
        self.assertFalse(media_jobs._MEDIA_LOCK.locked())

    def test_cancel_terminates_owned_worker_and_does_not_publish_result(self):
        worker=self.fixture_worker('time.sleep(30)\n')
        self.real_spawn=subprocess.Popen
        cancel=threading.Event();started=threading.Event();errors=[]
        def spawn(*args,**kwargs):
            process=self.spawn(*args,**kwargs);started.set();return process
        def run():
            try:media_jobs.sample_video(self.data,self.options,cancel)
            except BaseException as error:errors.append(error)
        with mock.patch.object(media_jobs,'_WORKER_SCRIPT',worker),mock.patch('subprocess.Popen',side_effect=spawn):
            thread=threading.Thread(target=run);thread.start();self.assertTrue(started.wait(5));cancel.set();thread.join(8)
        self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],InterruptedError)
        self.assert_exited();self.assertFalse(media_jobs._MEDIA_LOCK.locked())

    def test_queued_wait_is_cancellable_before_second_child_starts(self):
        cancel=threading.Event();errors=[]
        media_jobs._MEDIA_LOCK.acquire()
        def run():
            try:media_jobs.sample_video(self.data,self.options,cancel)
            except BaseException as error:errors.append(error)
        try:
            with mock.patch('subprocess.Popen',side_effect=AssertionError('queued worker must not spawn')):
                thread=threading.Thread(target=run);thread.start();time.sleep(.1);cancel.set();thread.join(2)
        finally:media_jobs._MEDIA_LOCK.release()
        self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],InterruptedError)

    def test_video_and_audio_share_one_owned_child_budget(self):
        self.real_spawn=subprocess.Popen;errors=[]
        def run(kind):
            try:
                if kind=='video':media_jobs.sample_video(self.data,self.options)
                else:media_jobs.extract_audio(asr_fixtures.AudioClock().wav(),start_s=0,end_s=1)
            except BaseException as error:errors.append(error)
        def spawn(*args,**kwargs):
            self.assertTrue(all(process.poll() is not None for process in self.processes))
            return self.spawn(*args,**kwargs)
        with mock.patch('subprocess.Popen',side_effect=spawn):
            threads=[threading.Thread(target=run,args=(kind,)) for kind in ('video','audio')]
            for thread in threads:thread.start()
            for thread in threads:thread.join(10)
        self.assertEqual(errors,[]);self.assertEqual(len(self.processes),2);self.assert_exited()

    def test_invalid_or_oversized_results_and_pcm_are_rejected(self):
        for code,operation in [("job.with_name('result.json').write_text('{}')\n",'video'),
              ("p=job.with_name('result.json'); p.open('wb').truncate(1024*1024+1)\n",'audio'),
              ("job.with_name('result.json').write_text(json.dumps({'result':{'status':'ready','window_start_s':0,'window_end_s':1}}))\njob.with_name('audio.f32').write_bytes(b'x')\n",'audio')]:
            worker=self.fixture_worker(code)
            self.real_spawn=subprocess.Popen;self.processes=[]
            with self.subTest(operation=operation),mock.patch.object(media_jobs,'_WORKER_SCRIPT',worker),mock.patch('subprocess.Popen',side_effect=self.spawn):
                with self.assertRaises(media_jobs.MediaWorkerError):
                    media_jobs._run(operation,self.data,self.options)
            self.assertEqual(len(self.processes),1);self.assert_exited()

    def test_local_input_budgets_and_pre_cancel_fail_before_worker(self):
        cancel=threading.Event();cancel.set()
        with mock.patch('subprocess.Popen',side_effect=AssertionError('invalid media spawn')):
            with self.assertRaises(InterruptedError):media_jobs.sample_video(self.data,self.options,cancel)
            with self.assertRaises(ValueError):media_jobs.extract_audio('https://invalid.example/video')
            with mock.patch.object(media_jobs,'MAX_VIDEO_BYTES',1):
                with self.assertRaises(ValueError):media_jobs.sample_video(self.data,self.options)

    def test_expired_input_copy_budget_does_not_spawn_a_worker(self):
        write=media_jobs._write_source
        def slow(*args,**kwargs):
            write(*args,**kwargs);time.sleep(.1)
        with mock.patch.object(media_jobs,'_write_source',side_effect=slow),mock.patch('subprocess.Popen',side_effect=AssertionError('expired media spawn')):
            with self.assertRaisesRegex(TimeoutError,'input preparation'):
                media_jobs.sample_video(self.data,self.options,timeout_s=.05)
        self.assertFalse(media_jobs._MEDIA_LOCK.locked())

    def test_malformed_empty_metadata_is_not_accepted_as_completed_sampling(self):
        good=media_jobs.sample_video(self.data,{**self.options,'start_s':.1,'end_s':.2,'allow_empty_segment':True})
        encoded=json.dumps(good)
        for field,value in [('source',[]),('sampling',{}),('coverage',{}),('frames',[{}])]:
            wrong=json.loads(encoded);wrong['manifest'][field]=value
            with self.subTest(field=field),self.assertRaises(media_jobs.MediaWorkerError):
                media_jobs._validate_empty_video(wrong,{**self.options,'start_s':.1,'end_s':.2,'allow_empty_segment':True})
        wrong=json.loads(encoded);wrong['manifest']['sampling']['end_s']=1
        with self.assertRaises(media_jobs.MediaWorkerError):
            media_jobs._validate_empty_video(wrong,{**self.options,'start_s':.1,'end_s':.2,'allow_empty_segment':True})


class WorkerSurvivalHTTP(unittest.TestCase):
    setUpClass=classmethod(video_fixtures.VideoUploadHTTP.setUpClass.__func__)
    tearDownClass=classmethod(video_fixtures.VideoUploadHTTP.tearDownClass.__func__)
    setUp=video_fixtures.VideoUploadHTTP.setUp
    tearDown=video_fixtures.VideoUploadHTTP.tearDown
    post=video_fixtures.VideoUploadHTTP.post

    def test_real_faulted_decoder_returns_502_and_parent_next_request_succeeds(self):
        spawn=subprocess.Popen;processes=[]
        def owned(*args,**kwargs):
            process=spawn(*args,**kwargs);processes.append(process);return process
        with tempfile.TemporaryDirectory() as temporary:
            worker=Path(temporary)/'failure.py';worker.write_text('import os\nos._exit(99)\n')
            with mock.patch.object(media_jobs,'_WORKER_SCRIPT',worker),mock.patch('subprocess.Popen',side_effect=owned):
                status,result=self.post({'data':self.encoded})
            self.assertEqual(status,502,result);self.assertEqual(result['error']['type'],'media_decode_failed')
            self.assertIn('exit 99',result['error']['message'])
            self.assertEqual(len(processes),2)
            self.assertTrue(all(process.poll() is not None for process in processes))
        status,result=self.post({'data':self.encoded})
        self.assertEqual(status,200,result);self.assertEqual(len(result['part']['frames']),3)

if __name__=='__main__':unittest.main()

"""Video persistence and real isolated decode, with no inference model."""
import base64
import copy
from pathlib import Path
import tempfile
import threading
import unittest
import json
import urllib.request
import urllib.error
from unittest import mock
from serve.video_tasks import VideoStore, validate_state
from tools.test_strata_video import write_video
from serve.media_jobs import sample_video
from serve.video import sampling_options
from serve.test_video import FakeVision
from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer,MockEngine,Service,serve
from tools import strata_video

OWNER='a'*64
PLAN={'start_s':0,'end_s':12,'segments':[{'index':i,'start_s':i*4,'end_s':(i+1)*4,'max_frames':8,'sampling_fps':2} for i in range(3)]}


class VideoTasks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.time=[1000]
        self.store=VideoStore(self.root,clock=lambda:self.time[0])
    def tearDown(self):
        self.tmp.cleanup()
    def create(self):
        source=self.store.upload(OWNER,base64.b64encode(b'original').decode(),persistent=True)
        task=self.store.create(OWNER,source['id'],{'plan':PLAN,'question':'describe'},'engine-a')
        return source,task
    def state(self):
        return {'notes':[{'start_s':0,'end_s':4,'frames':2,'text':'first window'}], 'times':[0,3], 'empty_segments':0,'phase':'observe'}
    def test_restart_resumes_only_committed_progress(self):
        source,task=self.create()
        saved=self.store.checkpoint(OWNER,task['id'],0,self.state(),'engine-a')
        restarted=VideoStore(self.root,clock=lambda:self.time[0])
        self.assertEqual(restarted.get(OWNER,task['id'],'engine-a')['state'],saved['state'])
        self.assertEqual(len(list((self.root/'.video-tasks').glob('*.media'))),1)
        with self.assertRaises(ValueError):restarted.get('b'*64,task['id'],'engine-a')
        with self.assertRaises(ValueError):restarted.get(OWNER,task['id'],'engine-b')
    def test_conflicting_pages_cannot_rewrite_committed_windows(self):
        _,task=self.create()
        self.store.checkpoint(OWNER,task['id'],0,self.state(),'engine-a')
        with self.assertRaises(ValueError):self.store.checkpoint(OWNER,task['id'],0,self.state(),'engine-a')
        state=self.state();state['notes'][0]['text']='rewritten'
        with self.assertRaises(ValueError):self.store.checkpoint(OWNER,task['id'],1,state,'engine-a')
    def test_incomplete_window_cannot_claim_completion_or_frame_times(self):
        state=self.state();state['phase']='complete'
        with self.assertRaises(ValueError):validate_state(state,PLAN)
        state=self.state();state['times']=[0,6]
        with self.assertRaises(ValueError):validate_state(state,PLAN)
        state=self.state();state['times']=[]
        with self.assertRaises(ValueError):validate_state(state,PLAN)
    def test_modified_original_invalidates_resume(self):
        source,task=self.create()
        (self.root/'.video-tasks'/(source['id']+'.media')).write_bytes(b'modified')
        with self.assertRaises(ValueError):self.store.get(OWNER,task['id'],'engine-a')
    def test_expiry_quota_and_cancel_leave_no_partial_source(self):
        source,task=self.create();self.time[0]+=86401;self.store.cleanup()
        self.assertEqual(list((self.root/'.video-tasks').glob('*')),[])
        small=VideoStore(self.root,quota=2)
        with self.assertRaises(ValueError):small.upload(OWNER,'YWJj')
        event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):self.store.upload(OWNER,'YWJj',cancel=event)
        self.assertEqual(list((self.root/'.video-tasks').glob('*')),[])
    def test_clear_removes_original_and_private_records(self):
        source,task=self.create();self.store.delete(OWNER,task['id'])
        self.assertEqual(list((self.root/'.video-tasks').glob('*')),[])
    def test_source_handles_are_not_paths_and_owner_is_not_stored(self):
        source,task=self.create()
        self.assertNotIn(OWNER,(self.root/'.video-tasks'/(task['id']+'.task.json')).read_text())
        with self.assertRaises(ValueError):
            with self.store.source('../outside',OWNER):pass
    def test_real_twelve_second_video_reuses_one_source_for_all_windows(self):
        video=self.root/'fixture.mp4';write_video(video,[i/24 for i in range(288)],rate=24)
        source=self.store.upload(OWNER,base64.b64encode(video.read_bytes()).decode(),persistent=True)
        all_times=[]
        for segment in PLAN['segments']:
            request={**segment,'mode':'frames','allow_empty_segment':True}
            with self.store.source(source['id'],OWNER) as (path,_):
                result=sample_video(path,sampling_options(request))
            origin=result['manifest']['source']['time_origin_s']
            all_times.extend(f['source_timestamp_s']-origin for f in result['part']['frames'])
        self.assertEqual(len(all_times),24)
        self.assertEqual(all_times[0],0)
        self.assertGreater(all_times[-1],11)
        self.assertEqual(len(list((self.root/'.video-tasks').glob('*.media'))),1)
    def test_http_restart_keeps_owned_task_and_source_sampling_without_loading(self):
        import serve.server as server
        tok=ByteTokenizer();engine=MockEngine(tok,'no inference')
        vision=FakeVision(self.root);svc=Service(engine,tok,ChatTemplate(Path(server.__file__).with_name('chat_template.jinja')),vision=vision)
        svc.api_key='secret'
        video=self.root/'fixture.mp4';write_video(video,[i/24 for i in range(288)],rate=24)
        with mock.patch.object(server,'ROOT',self.root),mock.patch.object(engine,'generate',side_effect=AssertionError('must not infer')):
            http=serve(svc,port=0)
            def post(path,body,owner=OWNER,key='secret'):
                request=urllib.request.Request(f'http://127.0.0.1:{http.server_port}'+path,data=json.dumps({**body,'owner':owner}).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
                try:
                    with urllib.request.urlopen(request,timeout=10) as response:return response.status,json.loads(response.read())
                except urllib.error.HTTPError as error:return error.code,json.loads(error.read())
            try:
                status,source=post('/v1/video/sources',{'data':base64.b64encode(video.read_bytes()).decode(),'persistent':True})
                self.assertEqual(status,200)
                status,task=post('/v1/video/tasks',{'source_id':source['id'],'config':{'plan':PLAN,'question':'describe'}})
                self.assertEqual(status,200)
                status,saved=post('/v1/video/tasks/checkpoint',{'id':task['id'],'revision':0,'state':self.state()})
                self.assertEqual(status,200,saved)
                self.assertEqual(post('/v1/video/tasks/get',{'id':task['id']},owner='b'*64)[0],400)
                self.assertEqual(post('/v1/video/tasks/get',{'id':task['id']},key='wrong')[0],401)
                for segment in PLAN['segments']:
                    status,result=post('/v1/video/sample',{'source_id':source['id'],**segment,'allow_empty_segment':True})
                    self.assertEqual(status,200,result)
                    self.assertAlmostEqual(result['manifest']['sampling']['end_s'],segment['end_s'],delta=1/24)
                http.shutdown();http.server_close();http=serve(svc,port=0)
                self.assertEqual(post('/v1/video/tasks/get',{'id':task['id']})[1]['state'],self.state())
                self.assertEqual(vision.calls,[])
            finally:http.shutdown();http.server_close()
    def test_adaptive_real_decode_keeps_coverage_and_brackets_a_scene_change(self):
        from fractions import Fraction
        import av
        import numpy as np
        path=self.root/'cut.mp4'
        with av.open(str(path),'w') as output:
            stream=output.add_stream('libx264',rate=24);stream.width=96;stream.height=64;stream.pix_fmt='yuv420p'
            stream.codec_context.options={'bf':'0','threads':'1','preset':'ultrafast'}
            for i in range(192):
                frame=av.VideoFrame.from_ndarray(np.full((64,96,3),30 if i<57 else 220,dtype=np.uint8),format='rgb24')
                frame.pts=i;frame.time_base=Fraction(1,24)
                for packet in stream.encode(frame):output.mux(packet)
            for packet in stream.encode():output.mux(packet)
        original=strata_video.sample_video(path,sampling_fps=1,max_frames=8)
        unchanged=strata_video.sample_video(path,sampling_fps=1,max_frames=8,sampling_strategy='uniform')
        self.assertEqual(original.manifest,unchanged.manifest)
        self.assertEqual([f.image.tobytes() for f in original.frames],[f.image.tobytes() for f in unchanged.frames])
        adaptive=strata_video.sample_video(path,sampling_fps=1,max_frames=8,sampling_strategy='adaptive')
        strata_video.validate_manifest(adaptive.manifest,8)
        times=[f.source_timestamp_s for f in adaptive.frames]
        self.assertTrue(any(2.1<t<2.375 for t in times),times)
        self.assertTrue(any(2.375<=t<2.7 for t in times),times)
        self.assertGreater(times[-1],7.5)
        self.assertEqual(adaptive.manifest['sampling']['end_s'],8)


if __name__=='__main__':unittest.main()

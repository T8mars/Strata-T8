"""Rounds R13–R20: real duration, sparse windows and speech clocks; CPU only."""
import base64
from fractions import Fraction
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import av
import numpy as np
from PIL import Image
from tools import strata_video as V
from tools.test_strata_video import write_video
from serve.video import sample_upload
from serve.asr.media import extract


class DurationAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='strata-duration-audit-')
        cls.dense = Path(cls.temp.name)/'12s-24fps.mp4'
        cls.sparse = Path(cls.temp.name)/'12s-1fps.mp4'
        write_video(cls.dense, [i/24 for i in range(288)], rate=24)
        write_video(cls.sparse, list(range(12)), rate=1)
        cls.data = base64.b64encode(cls.sparse.read_bytes()).decode()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_R13_real_288_frames_have_twelve_seconds_not_six(self):
        with av.open(self.dense) as c:
            self.assertAlmostEqual(V.video_duration(c,c.streams.video[0]),12,places=3)
            self.assertEqual(len(list(c.decode(video=0))),288)
        result=V.sample_video(self.dense,sampling_fps=24,max_frames=64)
        self.assertEqual(len(result.frames),64)
        self.assertAlmostEqual(result.manifest['sampling']['end_s'],12,places=3)
        self.assertGreater(result.frames[-1].timestamp_s,11.7)

    def test_R14_high_target_rate_scans_the_sparse_video_to_its_end(self):
        observed,empty=[],[]
        for i in range(36):
            try:
                r=V.sample_video(self.sparse,start_s=i/3,end_s=(i+1)/3,sampling_fps=24,max_frames=8)
                observed += [f.source_timestamp_s for f in r.frames]
            except V.VideoWindowEmpty as e:
                empty.append(e.manifest)
        self.assertEqual(observed,list(range(12)))
        self.assertEqual(len(empty),24)
        self.assertAlmostEqual(empty[-1]['sampling']['end_s'],12)

    def test_R15_empty_window_keeps_full_duration_and_requested_bounds(self):
        with self.assertRaises(V.VideoWindowEmpty) as raised:
            V.sample_video(self.sparse,start_s=1/3,end_s=2/3,sampling_fps=24,max_frames=8)
        m=raised.exception.manifest
        self.assertAlmostEqual(m['source']['duration_s'],12)
        self.assertEqual(m['sampling']['start_s'],1/3)
        self.assertEqual(m['sampling']['end_s'],2/3)
        self.assertEqual(m['frames'],[])

    def test_R16_empty_window_upload_is_explicit_opt_in(self):
        request={'data':self.data,'start_s':1/3,'end_s':2/3,'sampling_fps':24,'max_frames':8}
        with self.assertRaises(V.VideoWindowEmpty): sample_upload(request)
        result=sample_upload({**request,'allow_empty_segment':True})
        self.assertTrue(result['empty_segment']);self.assertIsNone(result['part'])
        self.assertAlmostEqual(result['manifest']['source']['duration_s'],12)

    def test_R17_corrupt_file_is_not_an_empty_segment(self):
        with self.assertRaises(ValueError) as raised:
            sample_upload({'data':base64.b64encode(b'not a video').decode(),'allow_empty_segment':True})
        self.assertNotIsInstance(raised.exception,V.VideoWindowEmpty)

    def test_R18_cancel_and_invalid_empty_flag_are_never_ignored(self):
        for flag in ('true',1,[],None):
            with self.subTest(flag=flag),self.assertRaisesRegex(ValueError,'boolean'):
                sample_upload({'data':self.data,'allow_empty_segment':flag})
        cancel=threading.Event();cancel.set()
        with self.assertRaises(V.VideoCancelled):
            sample_upload({'data':self.data,'allow_empty_segment':True},cancel=cancel)

    def test_R19_sparse_image_window_is_typed_but_malformed_metadata_is_not(self):
        images=[Image.new('RGB',(32,32)) for _ in range(12)]
        with self.assertRaises(V.VideoWindowEmpty) as raised:
            V.sample_images(images,fps=1,start_s=1/3,end_s=2/3)
        self.assertAlmostEqual(raised.exception.manifest['source']['duration_s'],12)
        with self.assertRaises(ValueError) as malformed:
            V.sample_images(images,fps=1,timestamps=[0],start_s=1/3,end_s=2/3)
        self.assertNotIsInstance(malformed.exception,V.VideoWindowEmpty)

    def test_R20_six_second_audio_track_does_not_cut_twelve_second_video(self):
        path=Path(self.temp.name)/'12s-video-6s-audio.mp4'
        with av.open(path,'w') as c:
            video=c.add_stream('libx264',rate=1);video.width=video.height=32;video.pix_fmt='yuv420p'
            audio=c.add_stream('aac',rate=16000);audio.layout='mono';packets=[]
            for i in range(12):
                frame=av.VideoFrame.from_ndarray(np.full((32,32,3),i*15,np.uint8),format='rgb24')
                frame.pts=i;frame.time_base=Fraction(1,1);packets+=list(video.encode(frame))
            packets+=list(video.encode())
            for i in range(0,6*16000,1024):
                pcm=(.2*np.sin(np.arange(min(1024,6*16000-i))/12)).astype(np.float32)
                frame=av.AudioFrame.from_ndarray(pcm.reshape(1,-1),format='fltp',layout='mono')
                frame.sample_rate=16000;frame.pts=i;frame.time_base=Fraction(1,16000)
                packets+=list(audio.encode(frame))
            packets+=list(audio.encode())
            for p in sorted(packets,key=lambda p:float(p.dts*p.time_base)):c.mux(p)
        result=extract(path,end_s=12)
        self.assertEqual(result['window_end_s'],12)
        self.assertEqual(len(result['pcm']),12*16000)
        self.assertGreater(result['gap_samples'],5*16000)
        self.assertTrue(np.all(result['pcm'][7*16000:]==0))


if __name__=='__main__': unittest.main()

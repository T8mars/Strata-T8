"""New source-clock and terminal CPU-work checks; no GPU/model calls."""
import base64
from fractions import Fraction
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
from PIL import Image
from tools import strata_video as V
from tools.test_strata_video import av, write_video
from serve import test_video as fixtures, video


class LastFrameCancellation(unittest.TestCase):
    def test_images_cancel_during_last_conversion_is_not_success(self):
        event=threading.Event()
        original=V.bounded_image
        def convert(*args,**kwargs):
            image=original(*args,**kwargs)
            event.set()
            return image
        with mock.patch.object(V,'bounded_image',side_effect=convert):
            with self.assertRaises(V.VideoCancelled):
                V.sample_images([Image.new('RGB',(32,32))],max_frames=1,cancel=event)

    def test_native_cancel_during_last_png_save_is_not_success(self):
        event=threading.Event()
        original=Image.Image.save
        part=fixtures.part(count=1)
        def save(image,*args,**kwargs):
            original(image,*args,**kwargs)
            event.set()
        def check():
            if event.is_set(): raise fixtures.RequestCancelled()
        with mock.patch.object(Image.Image,'save',save):
            with self.assertRaises(fixtures.RequestCancelled):
                video.expand_videos([{'role':'user','content':[part]}],fixtures.Vision.load,check)

    def test_cancelled_images_do_not_consume_timestamp_metadata(self):
        event=threading.Event(); event.set()
        def metadata():
            raise AssertionError('Cancelled metadata was consumed')
            yield 0
        with self.assertRaises(V.VideoCancelled):
            V.sample_images([Image.new('RGB',(32,32))],timestamps=metadata(),cancel=event)

    def test_timestamp_iterator_limit_applies_during_consumption(self):
        def metadata():
            for i in range(1_000_001): yield i
            raise AssertionError('Metadata limit did not stop its iterator')
        with self.assertRaisesRegex(ValueError,'timestamps'):
            V.sample_images([Image.new('RGB',(32,32))],timestamps=metadata())

    def test_source_index_iterator_limit_applies_during_consumption(self):
        def metadata():
            for i in range(1_000_001): yield i
            raise AssertionError('Metadata limit did not stop its iterator')
        with self.assertRaisesRegex(ValueError,'indices'):
            V.sample_images([Image.new('RGB',(32,32))],source_indices=metadata())


class TransparentFrames(unittest.TestCase):
    def cases(self):
        rgba=Image.new('RGBA',(32,32),(255,0,0,0))
        rgba.putpixel((0,0),(255,0,0,128))
        la=Image.new('LA',(32,32),(0,0))
        palette=Image.new('P',(32,32),0)
        palette.putpalette([255,0,0]+[0,0,0]*255)
        palette.info['transparency']=0
        return (rgba,la,palette)

    def test_transparent_shared_images_use_white_background(self):
        for image in self.cases():
            with self.subTest(mode=image.mode):
                actual=V.bounded_image(image,1024)
                self.assertEqual(actual.getpixel((1,1)),(255,255,255))
        self.assertEqual(V.bounded_image(self.cases()[0],1024).getpixel((0,0)),(255,127,127))

    def test_transparent_native_png_matches_single_image_preparation(self):
        for image in self.cases():
            out=io.BytesIO(); image.save(out,format='PNG')
            part=fixtures.part(count=1)
            part['frames'][0]['source']='data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()
            result=video.expand_videos([{'role':'user','content':[part]}],fixtures.Vision.load,lambda:None)
            with self.subTest(mode=image.mode),Image.open(io.BytesIO(result[0]['content'][0]['pairs'][0]['pair'][0])) as actual:
                self.assertEqual(actual.getpixel((1,1)),(255,255,255))


@unittest.skipUnless(av is not None,'PyAV required for source-clock fixtures')
class ActualSourceClock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='strata-video-third-')
        cls.mkv=Path(cls.temp.name)/'offset.mkv'
        cls.mp4=Path(cls.temp.name)/'offset.mp4'
        for path in (cls.mkv,cls.mp4):
            write_video(path,[i/10 for i in range(20)],origin=10,rate=10)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_mkv_decimal_trim_has_a_valid_nonnegative_clip_clock(self):
        sampled=V.sample_video(self.mkv,start_s=.1,end_s=1.1,sampling_fps=2,max_frames=2)
        self.assertEqual(sampled.frames[0].timestamp_s,0)
        self.assertEqual([f.source_timestamp_s for f in sampled.frames],[10.1,10.6])
        V.validate_manifest(sampled.manifest,2)

    def test_mkv_offset_is_not_counted_as_video_duration(self):
        sampled=V.sample_video(self.mkv,max_frames=2)
        self.assertEqual(sampled.manifest['source']['duration_s'],2)
        self.assertEqual(sampled.manifest['sampling']['requested_targets_s'],[0,1])
        self.assertEqual([f.source_timestamp_s for f in sampled.frames],[10,11])
        V.validate_manifest(sampled.manifest,2)

    def test_mp4_declared_stream_duration_is_not_offset_adjusted(self):
        sampled=V.sample_video(self.mp4,max_frames=2)
        self.assertEqual(sampled.manifest['source']['duration_s'],2)
        self.assertEqual([f.source_timestamp_s for f in sampled.frames],[10,11])
        V.validate_manifest(sampled.manifest,2)

    def test_offset_upload_decimal_trim_remains_valid(self):
        result=video.sample_upload({'data':base64.b64encode(self.mkv.read_bytes()).decode(),
             'start_s':.1,'end_s':1.1,'sampling_fps':2,'max_frames':2})
        self.assertEqual(result['part']['frames'][0]['timestamp_s'],0)
        V.validate_manifest(result['manifest'],2)

    def test_cancel_during_last_cpu_resize_is_not_success(self):
        event=threading.Event(); original=V.bounded_image
        def convert(*args,**kwargs):
            image=original(*args,**kwargs); event.set(); return image
        with mock.patch.object(V,'bounded_image',side_effect=convert):
            with self.assertRaises(V.VideoCancelled):
                V.sample_video(self.mp4,max_frames=1,cancel=event)

    def test_timeout_during_last_cpu_resize_is_not_success(self):
        clock=[0.]; original=V.bounded_image
        def convert(*args,**kwargs):
            image=original(*args,**kwargs); clock[0]=2.; return image
        with mock.patch.object(V.time,'monotonic',side_effect=lambda:clock[0]),mock.patch.object(V,'bounded_image',side_effect=convert):
            with self.assertRaises(TimeoutError): V.sample_video(self.mp4,max_frames=1,timeout_s=1)

    def test_stream_position_does_not_change_samples(self):
        expected=V.sample_video(self.mp4,max_frames=2)
        stream=io.BytesIO(self.mp4.read_bytes()); stream.seek(19)
        actual=V.sample_video(stream,max_frames=2)
        self.assertEqual(actual.manifest,expected.manifest)
        self.assertEqual([f.image.tobytes() for f in actual.frames],[f.image.tobytes() for f in expected.frames])


class DurationMetadata(unittest.TestCase):
    def obj(self,tag='00:00:12.000000000',origin=10,duration=12):
        return SimpleNamespace(duration=duration*1_000_000,format=SimpleNamespace(name='matroska,webm')),SimpleNamespace(duration=None,start_time=origin*1000,time_base=Fraction(1,1000),metadata={'DURATION':tag})

    def test_matroska_track_end_excludes_origin_and_other_tracks(self):
        container,stream=self.obj(duration=15)
        self.assertEqual(V.video_duration(container,stream),2)

    def test_invalid_track_duration_falls_back_without_unbounded_decode(self):
        for tag in ('bad','99:99:99','00:00:nan',None):
            container,stream=self.obj(tag=tag)
            with self.subTest(tag=tag): self.assertEqual(V.video_duration(container,stream),2)


if __name__=='__main__': unittest.main()

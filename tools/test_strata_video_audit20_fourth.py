"""Actual file colour/alpha and decoder callback boundaries, CPU only."""
import base64
from fractions import Fraction
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import av
import numpy as np
from PIL import Image
from tools import strata_video as V
from serve import video, test_video as F


def write_colour(path, transfer=2, alpha=False):
    with av.open(str(path),'w') as container:
        stream=container.add_stream('ffv1' if alpha else 'libx264',rate=2)
        stream.width=stream.height=32
        stream.pix_fmt='bgra' if alpha else 'yuv420p'
        stream.codec_context.color_trc=transfer
        stream.codec_context.color_primaries=9 if transfer in (16,18) else 1
        stream.codec_context.colorspace=9 if transfer in (16,18) else 1
        pixels=np.full((32,32,4 if alpha else 3),128,dtype=np.uint8)
        if alpha:
            pixels[:]=[255,0,0,0]
            pixels[8:16,8:16]=[0,255,0,255]
            pixels[16:24,8:16]=[255,0,0,128]
        for index in range(4):
            frame=av.VideoFrame.from_ndarray(pixels,format='rgba' if alpha else 'rgb24')
            frame.pts=index; frame.time_base=Fraction(1,2)
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)


class ActualFileColour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='strata-fourth-colour-')
        cls.files={}
        for name,transfer,alpha in (('pq',16,False),('hlg',18,False),('bt709',1,False),
                ('untagged',2,False),('srgb',13,False),('alpha',2,True)):
            path=Path(cls.temp.name)/(name+('.mkv' if alpha else '.mp4'))
            write_colour(path,transfer,alpha); cls.files[name]=path

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_pq_file_rejected_before_rgb_conversion(self):
        with av.open(str(self.files['pq'])) as container:
            self.assertEqual(container.streams.video[0].codec_context.color_trc,16)
        with mock.patch.object(V,'bounded_image') as convert:
            with self.assertRaisesRegex(ValueError,'HDR'):
                V.sample_video(self.files['pq'],max_frames=2)
            convert.assert_not_called()

    def test_hlg_file_rejected_before_rgb_conversion(self):
        with av.open(str(self.files['hlg'])) as container:
            self.assertEqual(container.streams.video[0].codec_context.color_trc,18)
        with mock.patch.object(V,'bounded_image') as convert:
            with self.assertRaisesRegex(ValueError,'HDR'):
                V.sample_video(self.files['hlg'],max_frames=2)
            convert.assert_not_called()

    def test_sdr_and_unspecified_transfers_keep_valid_samples(self):
        for name in ('bt709','untagged','srgb'):
            with self.subTest(name=name):
                result=V.sample_video(self.files[name],max_frames=2)
                V.validate_manifest(result.manifest,2)
                self.assertEqual([frame.timestamp_s for frame in result.frames],[0,1])
                self.assertEqual([frame.image.mode for frame in result.frames],['RGB','RGB'])

    def callback_failure(self,error):
        callback=mock.Mock(side_effect=[False,error])
        with self.assertRaises(type(error)) as caught:
            V.sample_video(self.files['untagged'],max_frames=1,cancel=callback)
        self.assertIs(caught.exception,error)

    def test_interrupt_callback_retains_original_exception(self):
        self.callback_failure(InterruptedError('sampling interrupted'))

    def test_connection_cancel_callback_retains_original_exception(self):
        self.callback_failure(ConnectionAbortedError('sampling connection cancelled'))

    def test_other_callback_io_failure_is_not_mislabelled_as_a_decoder_error(self):
        self.callback_failure(OSError('callback resource unavailable'))

    def test_actual_decoder_io_failure_still_becomes_validation_error(self):
        with mock.patch.object(av,'open',side_effect=OSError('decode fixture I/O')):
            with self.assertRaisesRegex(ValueError,'Video could not be decoded'):
                V.sample_video(self.files['untagged'],max_frames=1)

    def test_lossless_alpha_file_uses_white_and_preserves_visible_pixels(self):
        result=V.sample_video(self.files['alpha'],max_frames=2)
        for frame in result.frames:
            self.assertEqual(frame.image.getpixel((0,0)),(255,255,255))
            self.assertEqual(frame.image.getpixel((10,10)),(0,255,0))
            self.assertEqual(frame.image.getpixel((10,20)),(255,127,127))
        V.validate_manifest(result.manifest,2)

    def test_lossless_alpha_upload_serializes_composited_pixels(self):
        result=video.sample_upload({'data':base64.b64encode(self.files['alpha'].read_bytes()).decode(),'max_frames':2})
        for frame in result['part']['frames']:
            with Image.open(io.BytesIO(F.Vision.load(frame['source']))) as image:
                self.assertEqual(image.getpixel((0,0)),(255,255,255))
                self.assertEqual(image.getpixel((10,10)),(0,255,0))
                self.assertEqual(image.getpixel((10,20)),(255,127,127))

    def test_alpha_file_crop_and_resize_remain_bounded(self):
        result=V.sample_video(self.files['alpha'],crop=(0,0,16,16),max_frames=1,max_pixels=1024)
        self.assertEqual(result.frames[0].image.size,(16,16))
        self.assertEqual(result.frames[0].image.getpixel((0,0)),(255,255,255))
        self.assertEqual(result.frames[0].image.getpixel((10,10)),(0,255,0))


class ColourUploadHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        F.VideoUploadHTTP.setUpClass.__func__(cls)
        cls.pq=Path(cls.directory.name)/'fourth-pq.mp4'; write_colour(cls.pq,16)

    @classmethod
    def tearDownClass(cls): F.VideoUploadHTTP.tearDownClass.__func__(cls)
    setUp=F.VideoUploadHTTP.setUp
    tearDown=F.VideoUploadHTTP.tearDown
    post=F.VideoUploadHTTP.post

    def test_hdr_upload_400_then_sdr_success_without_loading_models(self):
        status,result=self.post({'data':base64.b64encode(self.pq.read_bytes()).decode()})
        self.assertEqual(status,400,result)
        self.assertEqual(result['error']['type'],'invalid_request_error')
        self.assertIn('HDR',result['error']['message'])
        status,result=self.post({'data':self.encoded})
        self.assertEqual(status,200,result)
        V.validate_manifest(result['manifest'],3)


if __name__=='__main__': unittest.main()

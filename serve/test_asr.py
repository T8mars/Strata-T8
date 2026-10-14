"""ASR contracts and CPU audio clocks; no models, downloads or production servers."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from serve.asr import config, contract, media
from serve.asr.manager import AsrManager, resource_lease


class SpeechHTTP(unittest.TestCase):
    def setUp(self):
        from serve import server
        from serve.frontend import ChatTemplate
        self.server_module=server
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.service=server.Service(server.MockEngine(server.ByteTokenizer(),''),server.ByteTokenizer(),
            ChatTemplate(Path(server.__file__).with_name('chat_template.jinja')))
        self.patch=mock.patch.object(server,'ROOT',self.root);self.patch.start()
        self.http=server.serve(self.service,host='127.0.0.1',port=0)
        self.base=f'http://127.0.0.1:{self.http.server_address[1]}'

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.patch.stop();self.temp.cleanup()

    def request(self,path,body=None,headers=None):
        import urllib.request
        request=urllib.request.Request(self.base+path,headers=headers or {},
            data=json.dumps(body).encode() if body is not None else None)
        if body is not None:request.add_header('Content-Type','application/json')
        with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)

    def test_subtitle_parse_uses_no_asr_process_and_preserves_times(self):
        with mock.patch('subprocess.Popen',side_effect=AssertionError('native worker')):
            result=self.request('/v1/asr/transcript/parse',{'text':'1\n00:00:01,000 --> 00:00:02,000\n你好'})
            self.assertEqual(result['cues'][0]['start_s'],1)
            self.assertFalse(hasattr(self.service,'asr_manager'))

    def test_settings_are_independent_and_authenticated(self):
        import urllib.error
        self.service.api_key='fixture-secret'
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/v1/asr/settings')
        self.assertEqual(error.exception.code,401)
        headers={'Authorization':'Bearer fixture-secret'}
        with mock.patch('subprocess.Popen',side_effect=AssertionError('native worker')):
            self.request('/v1/asr/settings',{'model_dir':'E:\\中文 模型','device':'cpu'},headers)
            current=self.request('/v1/asr/settings',headers=headers)
            self.assertEqual(current['settings']['model_dir'],'E:\\中文 模型')
            self.assertFalse(current['capabilities']['supported'])
            self.assertFalse(hasattr(self.service,'asr_manager'))

    def test_audio_and_video_uploads_use_bounded_cancellable_asr_adapter(self):
        fake=mock.Mock();fake.upload.return_value=timed()
        self.service.asr_manager=fake
        for path,limit in (('/v1/video/transcribe',128),('/v1/audio/transcribe',256)):
            self.request(path,{'data':'fixture'})
            args,kwargs=fake.upload.call_args
            self.assertIsInstance(args[1],threading.Event)
            self.assertEqual(kwargs['max_bytes'],limit*1024**2)

    def test_foreign_page_cannot_modify_asr_paths(self):
        import urllib.error
        headers={'Origin':'https://untrusted.example'}
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1/asr/settings',{'model_dir':'D:\\wrong'},headers)
        self.assertEqual(error.exception.code,403)
        self.assertFalse((self.root/'asr-settings.json').exists())

try:
    import av
    import numpy as np
except ImportError:
    av = np = None


def timed(**updates):
    return {'contract_version':1,'timeline':'input','status':'complete',
            'cues':[{'id':'u1','start_s':1,'end_s':10,'text':'一句完整对白','timing_method':'vad_segment'}],**updates}


class SpeechContract(unittest.TestCase):
    def test_long_utterance_keeps_identity_and_whole_text_in_overlapping_visual_chunks(self):
        a,b = contract.relevant(timed(),1,1.333),contract.relevant(timed(),1.333,1.667)
        self.assertEqual(a['cues'][0]['id'], b['cues'][0]['id'])
        self.assertEqual(a['cues'][0]['text'], '一句完整对白')
        self.assertEqual(b['cues'][0]['start_s'], 1)
        self.assertEqual(b['cues'][0]['end_s'], 10)
        self.assertEqual(b['cues'][0]['overlap_start_s'], 1.333)
        self.assertEqual(contract.relevant(timed(),10,11)['cues'], [])

    def test_selected_window_and_subtitle_offsets_are_explicit(self):
        t = timed(timeline='selected_window')
        self.assertEqual(contract.relevant(t,30,32,selected_start_s=30)['cues'][0]['start_s'],31)
        for text in ('1\n00:00:01,000 --> 00:00:02,200\n你好\n',
                     'WEBVTT\n\n00:01.000 --> 00:02.200 align:start\n你好\n'):
            cue = contract.subtitle_text(text,offset_s=.5)['cues'][0]
            self.assertEqual((cue['start_s'],cue['end_s']),(1.5,2.7))

    def test_confucius_samples_cover_full_transcript_without_fabricated_word_times(self):
        raw = {'status':'completed','audio_samples_16k':32000,'text':'你好',
               'segments':[{'segment_id':0,'start_sample':0,'end_sample':32000,
                            'speech_start_sample':8000,'speech_end_sample':24000,'text':'你好','timing_method':'vad_segment'}]}
        result = contract.from_confucius(raw,origin_s=10)
        self.assertEqual((result['cues'][0]['start_s'],result['cues'][0]['end_s']),(10.5,11.5))
        self.assertIsNone(result['cues'][0]['speaker'])
        with self.assertRaisesRegex(ValueError,'cover'):
            contract.from_confucius({**raw,'text':'你好世界'})

    def test_unbounded_or_invalid_cues_are_rejected(self):
        for value in (timed(contract_version=True), timed(status='running'),timed(cues=[{'id':'a','start_s':0,'end_s':float('nan'),'text':'x'}]),
                      timed(cues=[{'id':'a','start_s':2,'end_s':1,'text':'x'}]),timed(status='silence'),
                      timed(cues=[{'id':'u','start_s':1,'end_s':2,'text':'x'}]*2)):
            with self.subTest(value=value),self.assertRaises(ValueError): contract.transcript(value)

    def test_off_configuration_never_hashes_models_or_imports_worker(self):
        with tempfile.TemporaryDirectory() as root, mock.patch('hashlib.file_digest',side_effect=AssertionError('model hashing')):
            self.assertEqual(config.load(root)['device'],'cpu')
            self.assertFalse(config.availability(root)['supported'])
            self.assertFalse(config.availability(root)['default_enabled'])
        with self.assertRaises(ValueError): config.validate({'model_dir':'a\nb'})
        for key in ('quantization','device','language','channel'):
            with self.assertRaises(ValueError):config.validate({key:[]})

    def test_timed_json_accepts_utf8_bom_without_importing_asr(self):
        self.assertEqual(contract.transcript('\ufeff'+json.dumps(timed()))['cues'][0]['id'],'u1')

    def test_resource_lease_is_released_after_error(self):
        with self.assertRaisesRegex(RuntimeError,'fixture'):
            with resource_lease('unit-test-device'):
                raise RuntimeError('fixture')
        with resource_lease('unit-test-device'):
            pass


@unittest.skipUnless(av is not None,'PyAV CPU decoder required')
class AudioClock(unittest.TestCase):
    def offset_media(self):
        from fractions import Fraction
        out=io.BytesIO()
        with av.open(out,'w',format='matroska') as container:
            video=container.add_stream('ffv1',rate=10);video.width=16;video.height=16;video.pix_fmt='yuv420p'
            audio=container.add_stream('pcm_s16le',rate=16000);audio.layout='mono'
            packets=[]
            for i in range(30):
                frame=av.VideoFrame.from_ndarray(np.zeros((16,16,3),np.uint8),format='rgb24')
                frame.pts=50+i;frame.time_base=Fraction(1,10)
                packets.extend(video.encode(frame))
            packets.extend(video.encode())
            sound=av.AudioFrame.from_ndarray(np.full((1,16000),12000,np.int16),format='s16',layout='mono')
            sound.sample_rate=16000;sound.pts=88000;sound.time_base=Fraction(1,16000)
            packets.extend(audio.encode(sound));packets.extend(audio.encode())
            packets.sort(key=lambda packet:float(packet.dts*packet.time_base))
            for packet in packets:container.mux(packet)
        out.seek(0)
        return out

    def wav(self, start=.25, duration=1.5, silence=False):
        out = io.BytesIO()
        with av.open(out,'w',format='wav') as container:
            stream = container.add_stream('pcm_s16le',rate=48000)
            stream.layout='stereo'
            samples = np.zeros((2,round(duration*48000)),np.int16)
            if not silence: samples[:,round(start*48000):] = 12000
            frame = av.AudioFrame.from_ndarray(samples,format='s16p',layout='stereo')
            frame.sample_rate=48000
            for packet in stream.encode(frame): container.mux(packet)
            for packet in stream.encode(): container.mux(packet)
        out.seek(0)
        return out

    def test_resample_and_selected_window_keep_exact_sample_clock(self):
        result = media.extract(self.wav(),start_s=.5,end_s=1.25)
        self.assertEqual(len(result['pcm']),12000)
        self.assertEqual(result['sample_rate'],16000)
        self.assertEqual(result['window_start_s'],.5)
        self.assertEqual(result['window_end_s'],1.25)
        self.assertGreater(float(np.median(result['pcm'])),.3)

    def test_nonzero_video_pts_and_delayed_audio_are_not_collapsed_to_audio_zero(self):
        result=media.extract(self.offset_media(),start_s=0,end_s=1.5)
        self.assertAlmostEqual(result['source_origin_s'],5)
        self.assertEqual(len(result['pcm']),24000)
        self.assertTrue(np.all(result['pcm'][:8000]==0))
        self.assertTrue(np.all(result['pcm'][8000:23000]>.3))
        cropped=media.extract(self.offset_media(),start_s=.75,end_s=1.25)
        self.assertEqual(len(cropped['pcm']),8000)
        self.assertTrue(np.all(cropped['pcm']>.3))

    def test_digital_silence_does_not_spawn_or_require_asr_models(self):
        import subprocess
        spawn=subprocess.Popen
        def only_media(args,**kwargs):
            self.assertIn('media_worker.py',str(args))
            return spawn(args,**kwargs)
        with tempfile.TemporaryDirectory() as root,mock.patch('subprocess.Popen',side_effect=only_media):
            result=AsrManager(root).transcribe(self.wav(silence=True))
            self.assertEqual(result['status'],'silence')
            self.assertEqual(result['cues'],[])

    def test_cancel_and_invalid_track_fail_before_native_inference(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError): media.extract(self.wav(),cancel=cancel)
        with self.assertRaisesRegex(ValueError,'track'): media.extract(self.wav(),audio_track=1)


if __name__=='__main__':
    unittest.main()

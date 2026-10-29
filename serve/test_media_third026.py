"""Subtitle import and media clock boundaries from the third local audit."""
import unittest
from unittest import mock

from serve.asr import contract
from serve import test_asr as speech_fixture


class SubtitleNewlines(unittest.TestCase):
    def test_srt_cues_are_identical_across_standard_line_endings(self):
        source = '1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n2\n00:00:02,000 --> 00:00:03,000\n第二句\n'
        expected = contract.subtitle_text(source)
        for separator in ('\n', '\r\n', '\r'):
            with self.subTest(separator=repr(separator)):
                self.assertEqual(contract.subtitle_text(source.replace('\n', separator)), expected)

    def test_vtt_headers_comments_and_cues_keep_boundaries_with_cr_newlines(self):
        source = 'WEBVTT\n\nNOTE imported subtitles\nThis is not dialogue.\n\n00:00.000 --> 00:01.000\n第一句\n\n00:02.000 --> 00:03.000\n第二句\n'
        expected = contract.subtitle_text(source)
        actual = contract.subtitle_text(source.replace('\n', '\r'))
        self.assertEqual(actual, expected)
        self.assertEqual([cue['text'] for cue in actual['cues']], ['第一句', '第二句'])

    def test_mixed_newlines_preserve_clipped_offset_and_selected_window_clock(self):
        source = '1\r00:00:00,000 --> 00:00:01,000\rfirst\r\r2\r\n00:00:02,000 --> 00:00:04,000\nsecond\n'
        result = contract.subtitle_text(source, offset_s=-.5, timeline='selected_window')
        self.assertEqual(result['timeline'], 'selected_window')
        self.assertEqual([(cue['text'],cue['start_s'],cue['end_s']) for cue in result['cues']],
                         [('first',0,.5),('second',1.5,3.5)])
        evidence = contract.relevant(result, 11, 13, selected_start_s=10)
        self.assertEqual([cue['text'] for cue in evidence['cues']], ['second'])
        self.assertEqual(evidence['cues'][0]['overlap_start_s'], 11.5)


class SubtitleHTTP(unittest.TestCase):
    setUp = speech_fixture.SpeechHTTP.setUp
    tearDown = speech_fixture.SpeechHTTP.tearDown
    request = speech_fixture.SpeechHTTP.request

    def test_http_import_preserves_cr_subtitle_cues_without_asr(self):
        sources = ('1\r00:00:01,000 --> 00:00:02,000\r你好\r\r2\r00:00:03,000 --> 00:00:04,000\r世界',
                   'WEBVTT\r\r00:01.000 --> 00:02.000\r你好\r\r00:03.000 --> 00:04.000\r世界')
        with mock.patch('subprocess.Popen', side_effect=AssertionError('subtitle import must not start ASR')):
            for source in sources:
                with self.subTest(vtt=source.startswith('WEBVTT')):
                    result = self.request('/v1/asr/transcript/parse', {'text':source,'origin_s':10})
                    self.assertEqual([(cue['text'],cue['start_s'],cue['end_s']) for cue in result['cues']],
                                     [('你好',11,12),('世界',13,14)])
        self.assertFalse(hasattr(self.service, 'asr_manager'))


if __name__ == '__main__':
    unittest.main()

"""Second media audit: stale device decisions must not start automatic ASR."""
import base64
import io
import tempfile
import unittest
from unittest import mock

from serve.asr import config
from serve import test_asr_lifecycle as fixtures, test_asr as http_fixtures


class SpeechDeviceGuard(unittest.TestCase):
    setUp = fixtures.OwnedWorker.setUp
    write_worker = fixtures.OwnedWorker.write_worker
    complete_worker = fixtures.OwnedWorker.complete_worker

    def test_changed_device_is_rejected_before_decoding_or_starting_any_worker(self):
        self.complete_worker()
        for expected, actual in (('cpu', 'cuda'), ('cuda', 'cpu')):
            with self.subTest(expected=expected, actual=actual), \
                    mock.patch.object(config, 'load', return_value={**config.DEFAULTS, 'device':actual}), \
                    mock.patch('serve.media_jobs.extract_audio', side_effect=AssertionError('decode began after a stale device decision')) as decode, \
                    mock.patch('subprocess.Popen', side_effect=AssertionError('worker began after a stale device decision')) as spawn:
                with self.assertRaisesRegex(ValueError, 'device.*changed'):
                    self.manager.transcribe(io.BytesIO(b'fixture'), {'expected_device':expected})
            decode.assert_not_called()
            spawn.assert_not_called()

    def test_upload_accepts_a_matching_guard_without_overriding_settings(self):
        self.complete_worker()
        result = self.manager.upload({'data':base64.b64encode(b'fixture').decode(), 'expected_device':'cpu'})
        self.assertEqual(result['cues'][0]['text'], 'hello')
        self.assertEqual(result['device'], 'cpu')

    def test_invalid_guard_is_rejected_before_decoding(self):
        for value in (None, '', 'gpu', True, 0, ['cpu']):
            with self.subTest(value=value), \
                    mock.patch('serve.media_jobs.extract_audio', side_effect=AssertionError('invalid device guard reached decoder')):
                with self.assertRaisesRegex(ValueError, 'expected.*device'):
                    self.manager.transcribe(io.BytesIO(b'fixture'), {'expected_device':value})

    def test_unguarded_legacy_upload_keeps_existing_behavior(self):
        self.complete_worker()
        result = self.manager.upload({'data':base64.b64encode(b'fixture').decode()})
        self.assertEqual(result['device'], 'cpu')


class SpeechGuardCapabilities(unittest.TestCase):
    def test_device_guard_is_advertised_even_when_optional_models_are_missing(self):
        with tempfile.TemporaryDirectory() as root:
            capability = config.availability(root)
        self.assertTrue(capability['expected_device_guard'])
        self.assertFalse(capability['supported'])


class SpeechGuardHTTP(unittest.TestCase):
    setUp = http_fixtures.SpeechHTTP.setUp
    tearDown = http_fixtures.SpeechHTTP.tearDown
    request = http_fixtures.SpeechHTTP.request

    def test_settings_change_between_read_and_post_rejects_upload_and_owned_source(self):
        import urllib.error
        old = self.request('/v1/asr/settings')
        self.assertEqual(old['settings']['device'], 'cpu')
        self.assertTrue(old['capabilities']['expected_device_guard'])
        source = self.request('/v1/video/sources', {'owner':'a'*64, 'data':base64.b64encode(b'fixture').decode()})
        config.save(self.root, {**old['settings'], 'device':'cuda'})
        with mock.patch('serve.media_jobs.extract_audio', side_effect=AssertionError('stale device reached decoding')) as decode, \
                mock.patch('subprocess.Popen', side_effect=AssertionError('stale device started a child')) as spawn:
            for path, payload in (('/v1/audio/transcribe', {'data':base64.b64encode(b'fixture').decode()}),
                                  ('/v1/video/transcribe', {'source_id':source['id'], 'owner':'a'*64})):
                with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(path, {**payload, 'expected_device':old['settings']['device']})
                self.assertEqual(error.exception.code, 400)
                self.assertIn('device changed', error.exception.read().decode())
        decode.assert_not_called()
        spawn.assert_not_called()
        # The source lease must unwind even though transcribe failed early.
        self.assertTrue(self.request('/v1/video/sources/delete', {'owner':'a'*64,'source_id':source['id']})['deleted'])


if __name__ == '__main__':
    unittest.main()

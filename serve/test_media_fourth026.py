"""Video-task deletion must not consume a task on a dependency preflight failure."""
import unittest
import threading
import base64
from pathlib import Path
from unittest import mock

from serve import test_video_tasks as fixture
from serve import test_asr as http_fixture
from serve import video_tasks
from serve.asr.manager import AsrManager


class DeleteDependencyPreflight(unittest.TestCase):
    setUp = fixture.VideoTasks.setUp
    tearDown = fixture.VideoTasks.tearDown
    create = fixture.VideoTasks.create

    def test_corrupt_sibling_task_does_not_partially_delete_selected_task(self):
        source, task = self.create()
        selected = self.store.root/(task['id']+'.task.json')
        before = selected.read_bytes()
        broken = self.store.root/('f'*32+'.task.json')
        broken.write_bytes(b'{broken')
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.store.delete(fixture.OWNER, task['id'])
        self.assertEqual(selected.read_bytes(), before)
        self.assertTrue((self.store.root/(source['id']+'.media')).is_file())
        broken.unlink()
        self.assertEqual(self.store.delete(fixture.OWNER, task['id']), {'deleted':True})
        self.assertFalse(selected.exists())

    def test_sibling_io_failure_keeps_task_and_source_retryable(self):
        source, task = self.create()
        other_source, other = self.create()
        selected = self.store.root/(task['id']+'.task.json')
        target = self.store.root/(other['id']+'.task.json')
        read = video_tasks._read
        def unavailable(path):
            if path == target:
                raise PermissionError('fixture temporarily unreadable task')
            return read(path)
        with mock.patch.object(video_tasks, '_read', side_effect=unavailable):
            with self.assertRaisesRegex(PermissionError, 'unreadable'):
                self.store.delete(fixture.OWNER, task['id'])
        self.assertTrue(selected.is_file())
        self.assertTrue((self.store.root/(source['id']+'.media')).is_file())
        self.assertEqual(self.store.delete(fixture.OWNER, task['id']), {'deleted':True})
        self.assertTrue((self.store.root/(other_source['id']+'.media')).is_file())

    def test_other_task_sharing_source_retains_the_source(self):
        source, task = self.create()
        sibling = self.store.create(fixture.OWNER,source['id'],{'plan':fixture.PLAN,'question':'another'},'engine-a')
        self.assertEqual(self.store.delete(fixture.OWNER,task['id']), {'deleted':True})
        self.assertTrue((self.store.root/(sibling['id']+'.task.json')).is_file())
        self.assertTrue((self.store.root/(source['id']+'.media')).is_file())
        self.assertEqual(self.store.delete(fixture.OWNER,sibling['id']), {'deleted':True})
        self.assertFalse((self.store.root/(source['id']+'.media')).exists())


class UploadCancellation(unittest.TestCase):
    setUp = fixture.VideoTasks.setUp
    tearDown = fixture.VideoTasks.tearDown

    def test_already_cancelled_upload_never_allocates_decoded_payload(self):
        manager = AsrManager(self.root)
        cancelled = threading.Event(); cancelled.set()
        with mock.patch('serve.asr.manager.base64.b64decode', side_effect=AssertionError('cancelled payload decoded')), \
                mock.patch.object(manager, 'transcribe', side_effect=AssertionError('cancelled request transcribed')):
            with self.assertRaises(InterruptedError):
                manager.upload({'data':'eA=='}, cancelled)

    def test_cancel_during_upload_decode_prevents_transcription(self):
        manager = AsrManager(self.root)
        cancelled = threading.Event()
        def decoded(*args, **kwargs):
            cancelled.set()
            return b'fixture decoded audio'
        with mock.patch('serve.asr.manager.base64.b64decode', side_effect=decoded), \
                mock.patch.object(manager, 'transcribe', side_effect=AssertionError('cancelled request transcribed')):
            with self.assertRaises(InterruptedError):
                manager.upload({'data':'eA=='}, cancelled)


class SourceCleanupFailures(unittest.TestCase):
    setUp = fixture.VideoTasks.setUp
    tearDown = fixture.VideoTasks.tearDown
    create = fixture.VideoTasks.create

    def deny_unlink(self, target):
        unlink = Path.unlink
        def remove(path, *args, **kwargs):
            if path == target:
                raise PermissionError('fixture locked source component')
            return unlink(path, *args, **kwargs)
        return mock.patch.object(Path, 'unlink', new=remove)

    def test_committed_task_delete_retains_source_metadata_when_media_is_locked(self):
        source, task = self.create()
        media = self.store.root/(source['id']+'.media')
        metadata = self.store.root/(source['id']+'.source.json')
        result, failure = None, None
        with self.deny_unlink(media):
            try:result = self.store.delete(fixture.OWNER, task['id'])
            except OSError as error:failure = error
        self.assertFalse((self.store.root/(task['id']+'.task.json')).exists())
        self.assertTrue(metadata.is_file(), f'committed cleanup lost TTL metadata: {failure}')
        self.assertEqual(result, {'deleted':True,'cleanup_pending':True})
        self.time[0] += 86401
        self.store.cleanup()
        self.assertFalse(metadata.exists());self.assertFalse(media.exists())

    def test_committed_task_delete_reports_metadata_cleanup_pending(self):
        source, task = self.create()
        media = self.store.root/(source['id']+'.media')
        metadata = self.store.root/(source['id']+'.source.json')
        result = None
        with self.deny_unlink(metadata):
            try:result = self.store.delete(fixture.OWNER, task['id'])
            except OSError:pass
        self.assertEqual(result, {'deleted':True,'cleanup_pending':True})
        self.assertTrue(metadata.is_file())
        self.assertFalse(media.exists())
        self.time[0] += 86401
        self.store.cleanup()
        self.assertFalse(metadata.exists())

    def test_ttl_cleanup_retains_locked_source_for_a_later_pass(self):
        source = self.store.upload(fixture.OWNER,base64.b64encode(b'original').decode(),persistent=True)
        other = self.store.upload(fixture.OWNER,base64.b64encode(b'other source').decode(),persistent=True)
        media = self.store.root/(source['id']+'.media')
        metadata = self.store.root/(source['id']+'.source.json')
        self.time[0] += 86401
        with self.deny_unlink(media):
            self.store.cleanup()
        self.assertTrue(metadata.is_file(), 'TTL discarded source metadata before releasing locked media')
        self.assertTrue(media.is_file())
        self.assertFalse((self.store.root/(other['id']+'.media')).exists())
        self.assertFalse((self.store.root/(other['id']+'.source.json')).exists())
        self.store.cleanup()
        self.assertFalse(metadata.exists());self.assertFalse(media.exists())

    def test_direct_source_delete_can_retry_after_media_lock(self):
        source = self.store.upload(fixture.OWNER,base64.b64encode(b'original').decode(),persistent=True)
        media = self.store.root/(source['id']+'.media')
        metadata = self.store.root/(source['id']+'.source.json')
        with self.deny_unlink(media):
            with self.assertRaisesRegex(PermissionError, 'locked source'):
                self.store.delete_source(fixture.OWNER,source['id'])
        self.assertTrue(metadata.is_file())
        self.assertEqual(self.store.delete_source(fixture.OWNER,source['id']), {'deleted':True})
        self.assertFalse(metadata.exists());self.assertFalse(media.exists())

    def test_direct_source_delete_can_retry_after_metadata_lock(self):
        source = self.store.upload(fixture.OWNER,base64.b64encode(b'original').decode(),persistent=True)
        media = self.store.root/(source['id']+'.media')
        metadata = self.store.root/(source['id']+'.source.json')
        with self.deny_unlink(metadata):
            with self.assertRaisesRegex(PermissionError, 'locked source'):
                self.store.delete_source(fixture.OWNER,source['id'])
        self.assertTrue(metadata.is_file());self.assertFalse(media.exists())
        self.assertEqual(self.store.delete_source(fixture.OWNER,source['id']), {'deleted':True})
        self.assertFalse(metadata.exists())


class SourceCleanupHTTP(unittest.TestCase):
    setUp = http_fixture.SpeechHTTP.setUp
    tearDown = http_fixture.SpeechHTTP.tearDown
    request = http_fixture.SpeechHTTP.request
    deny_unlink = SourceCleanupFailures.deny_unlink

    def test_committed_delete_is_http_success_with_pending_cleanup(self):
        source = self.request('/v1/video/sources', {'owner':fixture.OWNER,'data':base64.b64encode(b'original').decode(),
                                                   'persistent':True})
        task = self.request('/v1/video/tasks', {'owner':fixture.OWNER,'source_id':source['id'],
                                               'config':{'plan':fixture.PLAN}})
        media = self.root/'.video-tasks'/(source['id']+'.media')
        metadata = self.root/'.video-tasks'/(source['id']+'.source.json')
        with self.deny_unlink(media):
            result = self.request('/v1/video/tasks/delete', {'owner':fixture.OWNER,'id':task['id']})
        self.assertEqual(result, {'deleted':True,'cleanup_pending':True})
        self.assertEqual(self.request('/v1/video/tasks/list', {'owner':fixture.OWNER}), {'tasks':[]})
        self.assertTrue(metadata.is_file());self.assertTrue(media.is_file())
        self.assertEqual(self.request('/v1/video/sources/delete', {'owner':fixture.OWNER,'source_id':source['id']}),
                         {'deleted':True})
        self.assertFalse(metadata.exists());self.assertFalse(media.exists())

if __name__ == '__main__':
    unittest.main()

"""Persisted video record damage must not hide unrelated tasks or consume data."""
import base64
import json
import unittest
import urllib.error

from serve import test_video_tasks as fixture
from serve import test_asr as http_fixture


class PersistedRecordShapes(unittest.TestCase):
    setUp = fixture.VideoTasks.setUp
    tearDown = fixture.VideoTasks.tearDown
    create = fixture.VideoTasks.create

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_invalid_task_source_handle_is_rejected_before_task_delete(self):
        source, task = self.create()
        selected = self.store.root/(task['id']+'.task.json')
        original = json.loads(selected.read_text('utf-8'))
        for handle in ('../outside', None, 123, []):
            with self.subTest(handle=handle):
                self.write(selected, {**original, 'source_id':handle})
                before = selected.read_bytes()
                with self.assertRaises(ValueError):
                    self.store.delete(fixture.OWNER, task['id'])
                self.assertTrue(selected.is_file(), 'invalid task was consumed before source validation')
                self.assertEqual(selected.read_bytes(), before)
                self.assertTrue((self.store.root/(source['id']+'.media')).is_file())

    def test_damaged_task_fields_do_not_hide_another_valid_task(self):
        _, bad = self.create()
        _, good = self.create()
        selected = self.store.root/(bad['id']+'.task.json')
        original = json.loads(selected.read_text('utf-8'))
        for field, value in (('state',None), ('revision',None), ('config',[]), ('id','f'*32), ('expires','later')):
            with self.subTest(field=field):
                self.write(selected, {**original, field:value})
                result = self.store.list(fixture.OWNER)
                self.assertEqual([task['id'] for task in result['tasks']], [good['id']])
                self.assertTrue(selected.is_file(), 'record damage must not authorize automatic deletion')

    def test_bad_expiry_is_preserved_while_other_expired_source_is_reclaimed(self):
        bad = self.store.upload(fixture.OWNER,base64.b64encode(b'bad expiry').decode(),persistent=True)
        marker = self.store.root/(bad['id']+'.source.json')
        value = json.loads(marker.read_text('utf-8'))
        good = self.store.upload(fixture.OWNER,base64.b64encode(b'normal expiry').decode(),persistent=True)
        self.write(marker, {**value, 'expires':'not-a-time'})
        self.time[0] += 86401
        self.store.cleanup()
        self.assertTrue(marker.is_file())
        self.assertTrue((self.store.root/(bad['id']+'.media')).is_file())
        self.assertFalse((self.store.root/(good['id']+'.source.json')).exists())
        self.assertFalse((self.store.root/(good['id']+'.media')).exists())

    def test_unknown_json_suffix_is_not_treated_as_an_owned_expired_record(self):
        self.store._directory()
        note = self.store.root/('e'*32+'.notes.json')
        note.write_text('{"expires":0,"text":"preserve user content"}', encoding='utf-8')
        self.store.cleanup()
        self.assertTrue(note.is_file())

    def test_source_record_identity_must_match_its_filename(self):
        source = self.store.upload(fixture.OWNER,base64.b64encode(b'original').decode(),persistent=True)
        marker = self.store.root/(source['id']+'.source.json')
        value = json.loads(marker.read_text('utf-8'))
        self.write(marker, {**value,'id':'f'*32})
        with self.assertRaises(ValueError):
            with self.store.source(source['id'],fixture.OWNER):
                self.fail('mismatched source record was exposed')
        self.assertTrue(marker.is_file())

    def test_missing_source_byte_count_is_a_record_error(self):
        source = self.store.upload(fixture.OWNER,base64.b64encode(b'original').decode(),persistent=True)
        marker = self.store.root/(source['id']+'.source.json')
        value = json.loads(marker.read_text('utf-8'));value.pop('bytes')
        self.write(marker,value)
        with self.assertRaises(ValueError):
            with self.store.source(source['id'],fixture.OWNER):
                self.fail('incomplete source record was exposed')
        self.assertTrue(marker.is_file())

    def test_valid_older_task_stays_listable_and_deletable_after_context_changes(self):
        source, task = self.create()
        selected = self.store.root/(task['id']+'.task.json')
        value = json.loads(selected.read_text('utf-8'))
        value.update(state={}, fingerprint='older-fingerprint-format')
        self.write(selected,value)
        self.assertEqual([item['id'] for item in self.store.list(fixture.OWNER)['tasks']], [task['id']])
        with self.assertRaisesRegex(ValueError, 'settings changed'):
            self.store.get(fixture.OWNER,task['id'],'new-engine-context')
        self.assertEqual(self.store.delete(fixture.OWNER,task['id']), {'deleted':True})
        self.assertFalse((self.store.root/(source['id']+'.media')).exists())

    def test_source_optional_labels_are_not_required_for_reuse(self):
        source = self.store.upload(fixture.OWNER,base64.b64encode(b'original').decode())
        marker = self.store.root/(source['id']+'.source.json')
        value = json.loads(marker.read_text('utf-8'))
        value.pop('name');value.pop('persistent')
        self.write(marker,value)
        with self.store.source(source['id'],fixture.OWNER,verify=True) as (path, _):
            self.assertEqual(path.read_bytes(),b'original')


class RecordShapeHTTP(unittest.TestCase):
    setUp = http_fixture.SpeechHTTP.setUp
    tearDown = http_fixture.SpeechHTTP.tearDown
    request = http_fixture.SpeechHTTP.request

    def test_bad_record_is_omitted_from_list_and_rejected_before_delete(self):
        source = self.request('/v1/video/sources', {'owner':fixture.OWNER,
            'data':base64.b64encode(b'original').decode(),'persistent':True})
        body = {'owner':fixture.OWNER,'source_id':source['id'],'config':{'plan':fixture.PLAN}}
        bad = self.request('/v1/video/tasks', body)
        good = self.request('/v1/video/tasks', body)
        selected = self.root/'.video-tasks'/(bad['id']+'.task.json')
        original = selected.read_bytes()
        record = json.loads(original)
        selected.write_text(json.dumps({**record,'state':None}),encoding='utf-8')
        result = self.request('/v1/video/tasks/list', {'owner':fixture.OWNER})
        self.assertEqual([item['id'] for item in result['tasks']],[good['id']])
        selected.write_text(json.dumps({**record,'source_id':'../not-a-handle'}),encoding='utf-8')
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/v1/video/tasks/delete', {'owner':fixture.OWNER,'id':bad['id']})
        self.assertEqual(failure.exception.code,400)
        self.assertTrue(selected.is_file())
        selected.write_bytes(original)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request('/v1/video/tasks/delete', {'owner':'b'*64,'id':good['id']})
        self.assertEqual(failure.exception.code,400)
        self.assertEqual(self.request('/v1/video/tasks/delete',{'owner':fixture.OWNER,'id':bad['id']}),{'deleted':True})
        self.assertTrue((self.root/'.video-tasks'/(source['id']+'.media')).is_file())


class DeleteSourceBindings(unittest.TestCase):
    setUp = fixture.VideoTasks.setUp
    tearDown = fixture.VideoTasks.tearDown
    create = fixture.VideoTasks.create

    def test_mismatched_source_owner_or_digest_is_rejected_before_task_commit(self):
        for foreign in (False,True):
            with self.subTest(foreign_owner=foreign):
                source, task = self.create()
                other = self.store.upload('b'*64 if foreign else fixture.OWNER,
                    base64.b64encode(b'original' if foreign else b'other source').decode(),persistent=True)
                selected = self.store.root/(task['id']+'.task.json')
                record = json.loads(selected.read_text('utf-8'))
                selected.write_text(json.dumps({**record,'source_id':other['id']}),encoding='utf-8')
                before = selected.read_bytes()
                with self.assertRaises(ValueError):
                    self.store.delete(fixture.OWNER,task['id'])
                self.assertEqual(selected.read_bytes(),before)
                for identity in (source['id'],other['id']):
                    self.assertTrue((self.store.root/(identity+'.source.json')).is_file())
                    self.assertTrue((self.store.root/(identity+'.media')).is_file())

    def test_missing_source_marker_never_authorizes_deleting_an_orphan_payload(self):
        source, task = self.create()
        (self.store.root/(source['id']+'.source.json')).unlink()
        self.assertEqual(self.store.delete(fixture.OWNER,task['id']), {'deleted':True,'cleanup_pending':True})
        self.assertFalse((self.store.root/(task['id']+'.task.json')).exists())
        self.assertTrue((self.store.root/(source['id']+'.media')).is_file())

    def test_already_missing_source_needs_no_payload_cleanup(self):
        source, task = self.create()
        for suffix in ('.source.json','.media'):
            (self.store.root/(source['id']+suffix)).unlink()
        self.assertEqual(self.store.delete(fixture.OWNER,task['id']), {'deleted':True})

    def test_owned_source_may_be_deleted_after_its_expiry_when_task_is_still_valid(self):
        source, task = self.create()
        marker = self.store.root/(source['id']+'.source.json')
        record = json.loads(marker.read_text('utf-8'))
        marker.write_text(json.dumps({**record,'expires':self.time[0]-1}),encoding='utf-8')
        self.assertEqual(self.store.delete(fixture.OWNER,task['id']), {'deleted':True})
        self.assertFalse((self.store.root/(source['id']+'.media')).exists())


if __name__ == '__main__':
    unittest.main()

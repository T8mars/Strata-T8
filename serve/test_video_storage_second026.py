"""The source and checkpoint quota applies to final on-disk bytes, atomically."""
import base64
import json
import unittest

from serve import test_video_tasks as fixture
from serve.video_tasks import _json_bytes


class RecordQuota(unittest.TestCase):
    setUp = fixture.VideoTasks.setUp
    tearDown = fixture.VideoTasks.tearDown
    create = fixture.VideoTasks.create
    state = fixture.VideoTasks.state

    def occupied(self):
        return sum(p.stat().st_size for p in self.store.root.iterdir())

    def test_upload_accounts_for_metadata_and_cleans_rejected_source(self):
        self.store.quota = 3
        with self.assertRaisesRegex(ValueError, 'storage is full'):
            self.store.upload(fixture.OWNER, base64.b64encode(b'abc').decode())
        self.assertEqual(list(self.store.root.iterdir()), [])

    def test_new_task_cannot_exceed_quota_or_extend_source_on_rejection(self):
        source = self.store.upload(fixture.OWNER, base64.b64encode(b'original').decode())
        record = self.store.root/(source['id']+'.source.json')
        before = record.read_bytes()
        self.store.quota = self.occupied()
        with self.assertRaisesRegex(ValueError, 'storage is full'):
            self.store.create(fixture.OWNER, source['id'], {'plan':fixture.PLAN}, 'engine-a')
        self.assertEqual(record.read_bytes(), before)
        self.assertEqual(list(self.store.root.glob('*.task.json')), [])

    def test_checkpoint_rejection_preserves_old_revision_and_source(self):
        source, task = self.create()
        before = {p:p.read_bytes() for p in self.store.root.glob('*.json')}
        self.store.quota = self.occupied()
        with self.assertRaisesRegex(ValueError, 'storage is full'):
            self.store.checkpoint(fixture.OWNER, task['id'], 0, self.state(), 'engine-a')
        self.assertEqual({p:p.read_bytes() for p in before}, before)
        self.assertEqual(self.store.get(fixture.OWNER, task['id'], 'engine-a')['revision'], 0)

    def test_replacement_uses_final_size_without_counting_old_record_twice(self):
        _, task = self.create()
        path = self.store.root/(task['id']+'.task.json')
        replacement = json.loads(path.read_bytes())
        replacement.update(state=self.state(), revision=1)
        self.store.quota = self.occupied()-path.stat().st_size+len(_json_bytes(replacement))
        saved = self.store.checkpoint(fixture.OWNER, task['id'], 0, self.state(), 'engine-a')
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(self.occupied(), self.store.quota)


if __name__ == '__main__':
    unittest.main()

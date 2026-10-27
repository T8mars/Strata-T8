"""A failed dependent write must not consume the caller's checkpoint revision."""
import json
import unittest
from unittest import mock

from serve import test_video_tasks as fixture, video_tasks


class CheckpointCommit(unittest.TestCase):
    setUp = fixture.VideoTasks.setUp
    tearDown = fixture.VideoTasks.tearDown
    create = fixture.VideoTasks.create
    state = fixture.VideoTasks.state

    def test_source_renewal_failure_keeps_revision_retryable(self):
        source, task = self.create()
        self.time[0] += 10
        source_path = self.store.root/(source['id']+'.source.json')
        original = video_tasks._atomic
        def save(path, value):
            if path == source_path:
                raise OSError('fixture source renewal failure')
            original(path, value)
        with mock.patch.object(video_tasks, '_atomic', side_effect=save):
            with self.assertRaisesRegex(OSError, 'source renewal failure'):
                self.store.checkpoint(fixture.OWNER, task['id'], 0, self.state(), 'engine-a')
        self.assertEqual(self.store.get(fixture.OWNER, task['id'], 'engine-a')['revision'], 0)
        saved = self.store.checkpoint(fixture.OWNER, task['id'], 0, self.state(), 'engine-a')
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(json.loads(source_path.read_bytes())['expires'], saved['expires'])

    def test_task_replace_failure_preserves_old_revision_and_safe_source(self):
        source, task = self.create()
        self.time[0] += 10
        task_path = self.store.root/(task['id']+'.task.json')
        source_path = self.store.root/(source['id']+'.source.json')
        original = video_tasks._atomic
        def save(path, value):
            if path == task_path:
                raise OSError('fixture task replace failure')
            original(path, value)
        with mock.patch.object(video_tasks, '_atomic', side_effect=save):
            with self.assertRaisesRegex(OSError, 'task replace failure'):
                self.store.checkpoint(fixture.OWNER, task['id'], 0, self.state(), 'engine-a')
        loaded = self.store.get(fixture.OWNER, task['id'], 'engine-a')
        self.assertEqual(loaded['revision'], 0)
        self.assertGreaterEqual(json.loads(source_path.read_bytes())['expires'], loaded['expires'])
        saved = self.store.checkpoint(fixture.OWNER, task['id'], 0, self.state(), 'engine-a')
        self.assertEqual(saved['revision'], 1)


if __name__ == '__main__':
    unittest.main()

"""The v0.1.45 prompt cache must coexist with T8's ordered temporal embeddings."""
from pathlib import Path
import tempfile, unittest
from unittest import mock
from serve import server, video
from serve.frontend import ChatTemplate, openai_to_messages
from serve.test_video import FakeVision, part


class TemporalPromptCache(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tok = server.ByteTokenizer()
        self.engine = server.MockEngine(tok, 'ok', max_context=4096)
        self.engine.native_video = self.engine.native_video_build = True
        self.vision = FakeVision(directory.name)
        self.service = server.Service(self.engine, tok,
            ChatTemplate(Path(__file__).with_name('chat_template.jinja')), vision=self.vision)
        self.addCleanup(self.service.discard_embeddings)
        self.messages, self.tools, self.kwargs = openai_to_messages(
            {'messages': [{'role': 'user', 'content': [part()]}]})
        self.kwargs['enable_thinking'] = False

    def test_video_preflight_does_not_replace_real_prompt_cache(self):
        self.service.encode_prompt([{'role': 'user', 'content': 'An existing conversation.'}], None, {})
        before = list(self.service.prompts.entries)
        self.service.video_precheck(self.messages, self.tools, self.kwargs, 16)
        self.assertEqual(self.service.prompts.entries, before)
        self.assertEqual(self.vision.calls, [])

    def test_impossible_native_prompt_is_not_retained_in_incremental_cache(self):
        self.engine.max_context = 32
        with self.assertRaises(ValueError):
            self.service.prepare(self.messages, self.tools, self.kwargs, 16)
        self.assertEqual(self.service.prompts.entries, [])
        self.assertEqual(self.vision.calls, [])

    def test_expanded_video_context_refusal_forgets_unexpanded_prompt_and_snapshot(self):
        expanded = video.expand_videos(self.messages, server.Vision.load, lambda: None)
        ids = self.service.encode_prompt(expanded, self.tools, self.kwargs, store=False)
        self.engine.max_context = len(ids) + server.CTX_SLACK + 1
        with mock.patch.object(self.service, 'ensure_loaded'):
            with self.assertRaisesRegex(ValueError, 'exceeds the context'):
                self.service.prepare(self.messages, self.tools, self.kwargs)
        self.assertTrue(self.vision.calls)
        self.assertEqual(self.service.prompts.entries, [])
        self.assertIsNone(self.service.embeddings.path)
        self.assertIsNone(self.service.embeddings.spans)
        self.assertFalse(list(self.vision.dir.glob('req-*.sve')))


if __name__ == '__main__':
    unittest.main()

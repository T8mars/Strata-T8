"""A failed mirror must not consume every queued range before failover."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from tools import portable_download as downloader

REAL_EVENT = threading.Event


def quick_event():
    event = REAL_EVENT()
    wait = event.wait
    # Preserve thread startup synchronization; make downloader retry delays
    # immediate in this isolated process without waiting real seconds.
    event.wait = lambda timeout=None: wait(None) if timeout is None else wait(0)
    return event


class BrokenMirror(unittest.TestCase):
    def test_failed_mirror_stops_queued_ranges_and_can_retry_another_source(self):
        data = b'fixture payload' * 150000
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'model.gguf'
            target.write_bytes(b'previous target must survive failed download')
            ranges = []
            def broken(request, **kwargs):
                ranges.append(request.get_header('Range'))
                raise OSError('fixture mirror unavailable')
            with mock.patch.object(downloader.urllib.request, 'urlopen', side_effect=broken), \
                    mock.patch.object(downloader.time, 'sleep'), \
                    mock.patch.object(downloader.threading, 'Event', side_effect=quick_event):
                with self.assertRaisesRegex(OSError, 'mirror unavailable'):
                    downloader.download('https://fixture.invalid/first',target,len(data),digest,workers=1,chunk_mib=1)
            self.assertEqual(target.read_bytes(), b'previous target must survive failed download')
            self.assertEqual(len(ranges), 6, 'queued ranges continued after the first terminal mirror failure')
            self.assertEqual(set(ranges), {f'bytes=0-{1024**2-1}'})
            state = json.loads(target.with_name(target.name+'.ranges.json').read_text('utf-8'))
            self.assertEqual(state['complete'], [])

            def valid(request, **kwargs):
                first,last = map(int,request.get_header('Range').removeprefix('bytes=').split('-'))
                result = io.BytesIO(data[first:last+1])
                result.status = 206
                result.headers = {'Content-Range':f'bytes {first}-{last}/{len(data)}'}
                return result
            with mock.patch.object(downloader.urllib.request, 'urlopen', side_effect=valid):
                downloader.download('https://fixture.invalid/second',target,len(data),digest,workers=2,chunk_mib=1)
            self.assertEqual(target.read_bytes(), data)
            self.assertTrue(target.with_name(target.name+'.verified.json').is_file())
            self.assertFalse(target.with_name(target.name+'.ranges.json').exists())
            self.assertFalse(target.with_name(target.name+'.part').exists())

    def interrupted_download(self, *, during_submit=False):
        data = b'c' * (2*1024**2+17)
        entered, release = REAL_EVENT(), REAL_EVENT()
        ranges = []
        class Response(io.BytesIO):
            def read(self, *args):
                entered.set()
                if not release.wait(3):
                    raise AssertionError('download fixture was not released')
                return super().read(*args)
        def fetch(request, **kwargs):
            ranges.append(request.get_header('Range'))
            first,last = map(int,ranges[-1].removeprefix('bytes=').split('-'))
            response = Response(data[first:last+1])
            response.status = 206
            response.headers = {'Content-Range':f'bytes {first}-{last}/{len(data)}'}
            return response
        def interrupt(*args, **kwargs):
            self.assertTrue(entered.wait(3))
            release.set()
            raise KeyboardInterrupt('fixture user cancelled')
        submit = downloader.concurrent.futures.ThreadPoolExecutor.submit
        submitted = []
        def interrupted_submit(pool, *args, **kwargs):
            if len(submitted) == 2:
                interrupt()
            submitted.append(1)
            return submit(pool, *args, **kwargs)
        boundary = (mock.patch.object(downloader.concurrent.futures.ThreadPoolExecutor, 'submit', new=interrupted_submit)
                    if during_submit else mock.patch.object(downloader.concurrent.futures, 'wait', side_effect=interrupt))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'model.gguf'
            target.write_bytes(b'previous target')
            try:
                with mock.patch.object(downloader.urllib.request, 'urlopen', side_effect=fetch), \
                        boundary:
                    with self.assertRaisesRegex(KeyboardInterrupt, 'user cancelled'):
                        downloader.download('https://fixture.invalid', target, len(data),
                                            hashlib.sha256(data).hexdigest(), workers=1, chunk_mib=1)
            finally:
                release.set()
            self.assertEqual(target.read_bytes(), b'previous target')
            self.assertEqual(len(ranges), 1, 'cancelled download drained the queued HTTP requests')
            state = json.loads(target.with_name(target.name+'.ranges.json').read_text('utf-8'))
            self.assertEqual(state['complete'], [])

    def test_user_interrupt_stops_pending_ranges_without_publishing_partial_content(self):
        self.interrupted_download()

    def test_interrupt_while_submitting_ranges_also_stops_the_queue(self):
        self.interrupted_download(during_submit=True)


if __name__ == '__main__':
    unittest.main()

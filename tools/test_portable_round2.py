"""Second audit: interrupted persistence, invalid deliveries and updater transactions."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import portable
from tools import bootstrap_portable, portable_download, portable_update as update
from serve import runconfig
from tools import test_portable_update as update_tests


class ConfigurationPersistence(unittest.TestCase):
    def test_settings_save_cleans_failed_temporary_file_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'config.json'
            path.write_text('{"old": true}')
            with mock.patch('os.replace', side_effect=OSError('simulated locked destination')):
                with self.assertRaises(OSError): runconfig.save(path, {'api_key': 'fixture-private-key'})
            self.assertEqual(path.read_text(), '{"old": true}')
            self.assertEqual((root/'config.json.bak').read_text(), '{"old": true}')
            self.assertEqual({p.name for p in root.iterdir()}, {'config.json', 'config.json.bak'})

    def test_replace_failure_preserves_original_and_removes_secret_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'config.json'
            path.write_text('{"old": true}')
            with mock.patch('os.replace', side_effect=OSError('simulated locked destination')):
                with self.assertRaises(OSError):
                    portable.save_json(path, {'api_key': 'fixture-private-key'})
            self.assertEqual(path.read_text(), '{"old": true}')
            self.assertEqual(list(root.iterdir()), [path])

    def test_partial_disk_write_cannot_truncate_the_previous_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'
            path.write_text('{"old": true}')
            original_open = io.open
            class FullDisk:
                def __init__(self, file): self.file = file
                def __enter__(self): return self
                def __exit__(self, *args): self.file.close()
                def write(self, data):
                    self.file.write(data[:5])
                    self.file.flush()
                    raise OSError('simulated disk full')
            def failing_open(file, mode='r', *args, **kwargs):
                opened = original_open(file, mode, *args, **kwargs)
                return FullDisk(opened) if 'w' in mode else opened
            with mock.patch('io.open', side_effect=failing_open):
                with self.assertRaises(OSError): portable.save_json(path, {'api_key': 'fixture-private-key'})
            self.assertEqual(path.read_text(), '{"old": true}')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_nonstandard_json_never_replaces_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'
            path.write_text('{"old": true}')
            with self.assertRaises(ValueError): portable.save_json(path, {'temperature': float('nan')})
            self.assertEqual(path.read_text(), '{"old": true}')


class DeliveryValidation(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        (root/'models/IQ3_S').mkdir(parents=True)
        for name in ('models/IQ3_S/main.gguf', 'mtp/rt/experts.bin', 'mtp/rt/dense.bin', 'mtp/rt/dense.txt'):
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture component')
        descriptor = {'family': 'qwen', 'model': 'IQ3_S', 'gguf_dir': 'models/IQ3_S', 'required_files': ['models/IQ3_S/main.gguf']}
        return root, descriptor

    def test_malformed_delivery_is_reported_without_raw_type_or_key_errors(self):
        for change in (lambda d: [], lambda d: dict(d, family=[]), lambda d: dict(d, gguf_dir=None),
                       lambda d: {k: v for k, v in d.items() if k != 'gguf_dir'},
                       lambda d: dict(d, required_files='models/IQ3_S/main.gguf'), lambda d: dict(d, required_files=[None])):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root, descriptor = self.fixture(directory)
                (root/'portable-model.json').write_text(json.dumps(change(descriptor)))
                with self.assertRaises((ValueError, RuntimeError)): portable.model_delivery(root)

    def test_zero_byte_component_is_refused_before_native_preparation(self):
        for name in ('models/IQ3_S/main.gguf', 'mtp/rt/experts.bin', 'mtp/rt/dense.bin', 'mtp/rt/dense.txt'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root, descriptor = self.fixture(directory)
                (root/'portable-model.json').write_text(json.dumps(descriptor))
                (root/name).write_bytes(b'')
                with self.assertRaisesRegex(RuntimeError, 'incomplete|empty'): portable.model_delivery(root)


class FailedUpdateStaging(unittest.TestCase):
    setUp = update_tests.ReleaseValidation.setUp
    tearDown = update_tests.ReleaseValidation.tearDown
    fixture = update_tests.ReleaseValidation.fixture

    def test_failed_download_cleans_its_owned_stage_and_cannot_leave_plan(self):
        root, release, fetch = self.fixture()
        stage = self.base/'stage'
        stage.mkdir()
        def interrupted(url, destination=None, limit=None):
            if destination:
                destination.write_bytes(b'partial large archive')
                raise OSError('connection lost')
            return fetch(url, destination, limit)
        with mock.patch.object(update, 'fetch', side_effect=interrupted), mock.patch.object(update, 'running_processes', return_value=[]), mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)):
            with self.assertRaises(OSError): update.prepare(root, release)
        self.assertFalse(stage.exists())
        self.assertFalse((root/'.portable-update/plan.json').exists())
        self.assertEqual((root/'app.py').read_text(), 'old')

    def test_apply_script_copy_failure_cannot_publish_a_plan(self):
        root, release, fetch = self.fixture()
        stage = self.base/'stage'
        stage.mkdir()
        with mock.patch.object(update, 'fetch', side_effect=fetch), mock.patch.object(update, 'running_processes', return_value=[]), mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)), mock.patch.object(update.shutil, 'copy2', side_effect=OSError('copy denied')):
            with self.assertRaises(OSError): update.prepare(root, release)
        self.assertFalse(stage.exists())
        self.assertFalse((root/'.portable-update/plan.json').exists())

    def test_success_keeps_verified_incoming_but_drops_duplicate_archive(self):
        root, release, fetch = self.fixture()
        stage = self.base/'stage'
        stage.mkdir()
        with mock.patch.object(update, 'fetch', side_effect=fetch), mock.patch.object(update, 'running_processes', return_value=[]), mock.patch.object(update.tempfile, 'mkdtemp', return_value=str(stage)):
            plan = update.prepare(root, release)
        self.assertTrue(Path(plan['stage']).is_dir())
        self.assertEqual(list(stage.glob('*.zip')), [])


class BootstrapCache(unittest.TestCase):
    def test_invalid_cached_download_is_replaced_by_verified_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'python.zip'
            target.write_bytes(b'interrupted cache')
            payload = b'complete pinned fixture'
            with mock.patch('urllib.request.urlopen', return_value=io.BytesIO(payload)):
                bootstrap_portable.download('https://fixture.invalid/python.zip', target, hashlib.sha256(payload).hexdigest())
            self.assertEqual(target.read_bytes(), payload)

    def test_interrupted_download_never_becomes_a_cached_final_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'python.zip'
            class Interrupted(io.BytesIO):
                def read(self, size=-1):
                    if self.tell(): raise OSError('interrupted')
                    return super().read(4)
            with mock.patch('urllib.request.urlopen', return_value=Interrupted(b'partial bytes')):
                with self.assertRaises(OSError): bootstrap_portable.download('https://fixture.invalid/python.zip', target)
            self.assertEqual(list(target.parent.iterdir()), [])


@unittest.skipUnless(os.name == 'nt', 'actual update applier uses Windows PowerShell')
class ApplyManifestBinding(unittest.TestCase):
    setUp = update_tests.ReleaseValidation.setUp
    tearDown = update_tests.ReleaseValidation.tearDown
    plan = update_tests.WindowsApply.plan
    apply = update_tests.WindowsApply.apply

    def test_altered_old_plan_cannot_delete_an_unmanaged_user_file(self):
        root, _, path = self.plan()
        (root/'user.txt').write_text('preserve me')
        plan = json.loads(path.read_text())
        plan['old'][0]['path'] = 'user.txt'
        path.write_text(json.dumps(plan))
        result = self.apply(path)
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'user.txt').read_text(), 'preserve me')
        self.assertEqual((root/'app.py').read_text(), 'old')

    def test_changed_plan_hash_cannot_authorize_corrupted_staged_file(self):
        root, incoming, path = self.plan()
        (incoming/'app.py').write_text('bad')
        plan = json.loads(path.read_text())
        for entry in plan['new']:
            if entry['path'] == 'app.py': entry['sha256'] = hashlib.sha256(b'bad').hexdigest()
        path.write_text(json.dumps(plan))
        result = self.apply(path)
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'old')

    def test_omitted_plan_entry_cannot_delete_a_file_still_in_the_manifest(self):
        root, _, path = self.plan()
        plan = json.loads(path.read_text())
        plan['new'] = [e for e in plan['new'] if e['path'] != 'app.py']
        path.write_text(json.dumps(plan))
        result = self.apply(path)
        self.assertNotEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((root/'app.py').read_text(), 'old')


class BinaryWeightPolicy(unittest.TestCase):
    def test_small_named_upstream_metadata_is_allowed_but_arbitrary_binary_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = update_tests.package(root, {'data/expert-profile.bin': 'routing statistics'})
            update.validate_manifest(root, manifest)
            manifest = update_tests.package(root, {'data/accidental.bin': 'weight'})
            with self.assertRaisesRegex(ValueError, 'Model'): update.validate_manifest(root, manifest)

    def test_binary_mtp_weights_are_not_plain_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = update_tests.package(root, {'engine/experts.bin': 'accidental model'})
            with self.assertRaisesRegex(ValueError, 'Model'): update.validate_manifest(root, manifest)


class ResumedModelRanges(unittest.TestCase):
    def response(self, data, first, last, total):
        response = io.BytesIO(data)
        response.status = 206
        response.headers = {'Content-Range': f'bytes {first}-{last}/{total}'}
        return response

    def test_resume_exact_tail_range_and_retry_short_read(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'fixture.gguf'
            payload = b'a'*(1024*1024)+b'seventeen-tail-xyz'
            digest = hashlib.sha256(payload).hexdigest()
            partial = target.with_name(target.name+'.part')
            partial.write_bytes(payload[:1024*1024]+bytes(len(payload)-1024*1024))
            state = target.with_name(target.name+'.ranges.json')
            state.write_text(json.dumps({'url': 'https://previous.invalid', 'size': len(payload), 'sha256': digest, 'chunk': 1024*1024, 'complete': [0]}))
            requests = []
            def fetch(request, timeout=None):
                requests.append(request.headers['Range'])
                first, last = map(int, request.headers['Range'][6:].split('-'))
                data = payload[first:last+1]
                return self.response(data[:2] if len(requests) == 1 else data, first, last, len(payload))
            with mock.patch('urllib.request.urlopen', side_effect=fetch), mock.patch.object(portable_download.time, 'sleep'):
                portable_download.download('https://current.invalid', target, len(payload), digest, workers=2, chunk_mib=1)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(requests, [f'bytes=1048576-{len(payload)-1}']*2)
            self.assertFalse(state.exists())
            self.assertFalse(partial.exists())

    def test_failed_final_hash_resets_ranges_for_a_clean_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'fixture.gguf'
            payload = b'correct fixture'
            digest = hashlib.sha256(payload).hexdigest()
            def fetch(request, timeout=None): return self.response(b'x'*len(payload), 0, len(payload)-1, len(payload))
            with mock.patch('urllib.request.urlopen', side_effect=fetch):
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    portable_download.download('https://fixture.invalid', target, len(payload), digest, workers=1, chunk_mib=1)
            state = target.with_name(target.name+'.ranges.json')
            self.assertEqual(json.loads(state.read_text())['complete'], [])
            self.assertFalse(target.exists())
            with mock.patch('urllib.request.urlopen', return_value=self.response(payload, 0, len(payload)-1, len(payload))):
                portable_download.download('https://fixture.invalid', target, len(payload), digest, workers=1, chunk_mib=1)
            self.assertEqual(target.read_bytes(), payload)


if __name__ == '__main__': unittest.main()

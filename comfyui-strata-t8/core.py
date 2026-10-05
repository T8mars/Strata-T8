"""HTTP and owned-service supervision. Importing this module performs no GPU or network work."""
from __future__ import annotations
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import uuid

HOME = Path(os.environ.get('STRATA_COMFY_HOME') or Path(os.environ.get('LOCALAPPDATA', Path.home()/'.config'))/'Strata-T8-ComfyUI')


class StrataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Connection:
    profile: str


def profile_path(name):
    if not re.fullmatch(r'[\w-]{1,64}', name):
        raise StrataError('Profile name: 1–64 letters, digits, underscores or hyphens')
    return HOME/'profiles'/f'{name}.json'


def profiles():
    return sorted(p.stem for p in (HOME/'profiles').glob('*.json')) or ['default']


def read_profile(name):
    path = profile_path(name)
    if not path.is_file():
        raise StrataError('Create this local profile in the Strata panel first; workflows contain only its name.')
    return normalize_profile(json.loads(path.read_text(encoding='utf-8')))


def normalize_profile(profile):
    profile = dict(profile)
    if profile.get('mode') not in ('managed', 'external'):
        raise StrataError('Profile mode must be managed or external')
    if profile['mode'] == 'managed':
        if not isinstance(profile.get('runtime'), str) or not profile['runtime'].strip() or not isinstance(profile.get('data_dir'), str) or not profile['data_dir'].strip():
            raise StrataError('Managed profiles require runtime and data_dir paths')
        if not 1 <= int(profile.get('port', 8082)) <= 65535:
            raise StrataError('Managed port must be between 1 and 65535')
        profile['url'] = f"http://127.0.0.1:{int(profile.get('port', 8082))}"
        profile['allow_lifecycle'] = True
        profile.setdefault('same_gpu', True)
    url = urllib.parse.urlsplit(profile['url'])
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise StrataError('Use an HTTP(S) service URL without embedded credentials, query or fragment')
    if url.path.rstrip('/') not in ('', '/v1'):
        raise StrataError('Service URL path must be empty or /v1')
    if profile.get('same_gpu') and url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise StrataError('GPU handoff requires a local service')
    if not isinstance(profile.get('api_key', ''), str) or any(c in profile.get('api_key', '') for c in '\r\n'):
        raise StrataError('API key must be a string without line breaks')
    if profile.get('same_gpu') and not profile.get('allow_lifecycle'):
        raise StrataError('Same-GPU mode requires explicit lifecycle control')
    if not 1024 <= int(profile.get('context', 32768)) <= 131072:
        raise StrataError('Context must be between 1024 and 131072')
    return profile


def save_profile(name, profile):
    with profile_lock(name):
        _save_profile(name, profile)


def _save_profile(name, profile):
    # Credentials live here, never in node inputs, PNG metadata or status responses.
    path = profile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(profile)
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if profile.get('api_key') == '__KEEP__':
        profile['api_key'] = old.get('api_key', '')
    if profile.get('mode') == 'managed' and not profile.get('api_key'):
        profile['api_key'] = uuid.uuid4().hex + uuid.uuid4().hex
    profile = normalize_profile(profile)
    temp = path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)
    os.chmod(path, 0o600)


def interrupted():
    try:
        import comfy.model_management as mm
        mm.throw_exception_if_processing_interrupted()
    except ImportError:
        pass


def encode_images(images, max_images=8, max_pixels=1048576):
    import numpy as np
    from PIL import Image
    if len(images.shape) != 4 or images.shape[-1] not in (3, 4):
        raise StrataError('IMAGE must have shape [batch, height, width, 3 or 4]')
    if not 1 <= images.shape[0] <= max_images:
        raise StrataError(f'Use at most {max_images} images per request')
    if images.numel() > max_images*16*1048576*4:
        raise StrataError('Image input exceeds the CPU conversion limit; resize it upstream')
    result = []
    for tensor in images:
        interrupted()
        array = (tensor.detach().to('cpu').clamp(0, 1).numpy()*255).round().astype(np.uint8)
        image = Image.fromarray(array).convert('RGB')
        scale = min(1, (max_pixels/(image.width*image.height))**.5)
        if scale < 1:
            image = image.resize((max(1, int(image.width*scale)), max(1, int(image.height*scale))), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format='PNG')
        result.append('data:image/png;base64,' + base64.b64encode(out.getvalue()).decode())
    return result


class Client:
    def __init__(self, profile):
        self.profile = profile

    def request(self, path, body=None, check=interrupted, timeout=None):
        endpoint = urllib.parse.urlsplit(self.profile['url'])
        cls = http.client.HTTPSConnection if endpoint.scheme == 'https' else http.client.HTTPConnection
        if check:
            check()
        conn = cls(endpoint.hostname, endpoint.port, timeout=min(5, timeout or self.profile.get('timeout_s', 1800)))
        conn.auto_open = 0  # A cancelled/closed socket must never reconnect and send a late request.
        result, finished, cancelled = {}, threading.Event(), threading.Event()
        def work():
            try:
                headers = {'Content-Type': 'application/json'}
                if self.profile.get('api_key'):
                    headers['Authorization'] = 'Bearer '+self.profile['api_key']
                conn.connect()
                if cancelled.is_set():
                    return
                conn.sock.settimeout(timeout or self.profile.get('timeout_s', 1800))
                conn.request('POST' if body is not None else 'GET', path, json.dumps(body).encode() if body is not None else None, headers)
                response = conn.getresponse()
                raw = response.read(16*1024**2+1)
                if len(raw) > 16*1024**2:
                    raise StrataError('Service response exceeds 16 MiB')
                data = json.loads(raw)
                if response.status >= 400:
                    error = data.get('error') or {}
                    raise StrataError(f"HTTP {response.status} {error.get('code') or error.get('type') or ''}: {error.get('message', 'service rejected request')}")
                result['data'] = data
            except Exception as error:
                result['error'] = error
            finally:
                conn.close()
                finished.set()
        worker = threading.Thread(target=work, daemon=True, name='strata-http')
        worker.start()
        try:
            while not finished.wait(.1):
                if check:
                    check()
            if 'error' in result:
                error = result['error']
                if isinstance(error, StrataError):
                    message = str(error)
                    key = self.profile.get('api_key')
                    raise StrataError(message.replace(key, '[redacted]') if key else message) from None
                raise StrataError(f'Strata connection failed: {type(error).__name__}') from None
            return result['data']
        except BaseException:
            cancelled.set()
            if conn.sock is not None:
                try:
                    conn.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            conn.close()
            worker.join(timeout=5)
            raise


@contextmanager
def file_lock(key, check=interrupted):
    path = HOME/'locks'/(hashlib.sha256(key.encode()).hexdigest()+'.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        while True:
            lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                check()
                time.sleep(.1)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def profile_lock(name, check=interrupted):
    profile_path(name)  # Validate the shared name before constructing its lock key.
    return file_lock('profile:'+name, check)


def service_lock(profile, check=interrupted):
    # All ComfyUI instances under this account share the same GPU and endpoint locks.
    key = 'same-gpu-0' if profile.get('same_gpu') else (str(Path(profile['runtime']).resolve()).casefold() if profile['mode'] == 'managed' else profile['url'].removesuffix('/v1').rstrip('/'))
    return file_lock(key, check)


class Managed:
    def __init__(self, name, profile):
        self.name, self.profile = name, profile
        self.root = Path(profile['runtime']).expanduser().resolve()
        self.dir = HOME/'instances'/name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir/'owner.json'
        self.python = self.root/'runtime/python/python.exe'

    def owned(self):
        import psutil
        if not self.state_path.is_file():
            return None
        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        try:
            proc = psutil.Process(state['pid'])
            recorded_python = Path(state.get('python') or self.python).resolve()
            if (abs(proc.create_time()-state['created']) > .01 or Path(proc.exe()).resolve() != recorded_python
                    or str(self.dir/'service.json') not in proc.cmdline()):
                return None
            return proc
        except (psutil.Error, OSError):
            return None

    def stop(self):
        import psutil
        proc = self.owned()
        if proc is None:
            return
        children = proc.children(recursive=True)
        targets = [proc, *children]
        for target in targets:
            try:
                target.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(targets, timeout=15)
        for target in alive:
            try:
                target.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=15)
        if alive:
            raise StrataError('Owned service processes are still exiting; downstream GPU work is blocked')
        self.state_path.unlink(missing_ok=True)

    def ensure(self, client, check=interrupted):
        import psutil
        config_keys = ('runtime', 'data_dir', 'port', 'context', 'vision', 'api_key')
        startup = {key: self.profile.get(key) for key in config_keys}
        startup['runtime_version'] = json.loads((self.root/'meta.json').read_text(encoding='utf-8')).get('version') if (self.root/'meta.json').is_file() else None
        fingerprint = hashlib.sha256(json.dumps(startup, sort_keys=True).encode()).hexdigest()
        proc = self.owned()
        if proc is not None and json.loads(self.state_path.read_text(encoding='utf-8')).get('fingerprint') != fingerprint:
            self.stop()
            proc = None
        if proc is None:
            if not self.python.is_file() or not (self.root/'tools/managed_config.py').is_file():
                raise StrataError('Select a compatible Strata-T8 portable runtime')
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', int(self.profile.get('port', 8082))))
                except OSError:
                    raise StrataError('Managed port is occupied; select another port or use an external profile') from None
            log = open(self.dir/'service.log', 'ab', buffering=0)
            config_cmd = [str(self.python), '-X', 'utf8', str(self.root/'tools/managed_config.py'),
                          '--data-dir', self.profile['data_dir'], '--output', str(self.dir/'service.json'),
                          '--vision', self.profile.get('vision', 'gpu'), '--context', str(self.profile.get('context', 32768))]
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            config = subprocess.Popen(config_cmd, cwd=self.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
            try:
                deadline = time.monotonic()+900
                while config.poll() is None:
                    check()
                    if time.monotonic() > deadline:
                        raise StrataError('Offline model preparation timed out; see the local service log')
                    time.sleep(.1)
                if config.returncode:
                    raise StrataError(f'Offline profile preparation failed; see {self.dir / "service.log"}')
            except BaseException:
                if config.poll() is None:
                    task = psutil.Process(config.pid)
                    children = task.children(recursive=True)
                    for child in children:
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    config.kill()
                    config.wait(timeout=20)
                    _, alive = psutil.wait_procs(children, timeout=20)
                    if alive:
                        raise StrataError('Model preparation children have not exited')
                raise
            finally:
                log.close()
            instance = uuid.uuid4().hex
            env = dict(os.environ, STRATA_API_KEY=self.profile['api_key'], STRATA_INSTANCE_ID=instance)
            with open(self.dir/'service.log', 'ab', buffering=0) as log:
                child = subprocess.Popen([str(self.python), '-X', 'utf8', '-u', str(self.root/'serve/server.py'),
                                         '--engine', 'strata', '--config', str(self.dir/'service.json'),
                                         '--port', str(self.profile.get('port', 8082))], cwd=self.root, env=env,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
            proc = psutil.Process(child.pid)
            try:
                temp = self.state_path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
                temp.write_text(json.dumps({'pid': child.pid, 'created': proc.create_time(), 'instance': instance,
                                           'python': str(self.python), 'fingerprint': fingerprint}), encoding='utf-8')
                os.replace(temp, self.state_path)
            except BaseException:
                child.kill()
                child.wait(timeout=20)
                raise
        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        try:
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                check()
                if not proc.is_running():
                    raise StrataError(f'Managed server exited; see {self.dir / "service.log"}')
                try:
                    status = client.request('/v1/status', timeout=2, check=check)
                except StrataError:
                    time.sleep(.2)
                    continue
                if status.get('instance_id') != state['instance']:
                    raise StrataError('Port belongs to a different server instance')
                return status
            raise StrataError('Managed HTTP server did not become ready within 90 seconds')
        except BaseException:
            self.stop()
            raise


def gpu_handoff(profile):
    import psutil
    import comfy.model_management as mm
    mm.unload_all_models()
    mm.soft_empty_cache()
    import torch
    device = mm.get_torch_device()
    if device.type != 'cuda':
        raise StrataError('Same-GPU mode currently requires the verified NVIDIA path')
    free, total = torch.cuda.mem_get_info(device)
    if free < int(profile.get('min_free_vram_mib', 12288))*1024**2:
        raise StrataError(f'GPU conflict: only {free//1024**2} MiB free after ComfyUI unload')
    if psutil.virtual_memory().available < float(profile.get('min_free_ram_gib', 60))*1024**3:
        raise StrataError('Insufficient available RAM after ComfyUI offload; close other models or lower the profile threshold only after measurement')
    return device, free, torch.cuda.memory_allocated(device)


def cleanup(client, profile, manager, baseline):
    deadline = time.monotonic()+float(profile.get('cleanup_timeout_s', 90))
    last = None
    while time.monotonic() < deadline:
        try:
            status = client.request('/v1/status', check=None, timeout=3)
            if not status['activity']['in_flight']:
                client.request('/v1/unload', {}, check=None, timeout=5)
                status = client.request('/v1/status', check=None, timeout=3)
                if not status['loaded'] and not any(p['running'] or p['loaded'] or p['starting'] for p in status['processes'].values()):
                    break
        except (StrataError, KeyError) as error:
            last = error
        time.sleep(.2)
    else:
        if manager:
            manager.stop()
        else:
            raise StrataError(f'External service release could not be confirmed: {last}')
    if baseline:
        import torch
        end = time.monotonic()+15
        while torch.cuda.mem_get_info(baseline[0])[0] < baseline[1]-256*1024**2:
            if time.monotonic() > end:
                raise StrataError('GPU memory has not returned after Strata unload; downstream work is blocked')
            time.sleep(.2)


def generate(connection, requests):
    with profile_lock(connection.profile):
        return _generate(connection, requests)


def _generate(connection, requests):
    profile = read_profile(connection.profile)
    client = Client(profile)
    manager = Managed(connection.profile, profile) if profile['mode'] == 'managed' else None
    with service_lock(profile):
        baseline = None
        cleanup_required = False
        def check():
            interrupted()
            if baseline:
                import torch
                if torch.cuda.memory_allocated(baseline[0]) > baseline[2]+128*1024**2:
                    raise StrataError('ComfyUI background GPU work appeared during the Strata request')
        try:
            status = manager.ensure(client) if manager else client.request('/v1/status', check=check, timeout=5)
            if status.get('service') != 'strata' or status.get('protocol_version') != 1:
                raise StrataError('This node requires Strata-T8 protocol version 1; update the runtime')
            if profile.get('same_gpu') and status['concurrency']['serving'] != 1:
                raise StrataError('Same-GPU mode requires parallel=1')
            if profile.get('same_gpu') and status['activity']['in_flight']:
                raise StrataError('Strata is busy with another request; retry after it finishes')
            cleanup_required = bool(profile.get('allow_lifecycle'))
            if profile.get('same_gpu') and (status['loaded'] or any(p['running'] or p['starting'] for p in status['processes'].values())):
                cleanup(client, profile, manager, None)
            baseline = gpu_handoff(profile) if profile.get('same_gpu') else None
            answers = []
            for request in requests:
                check()
                request = dict(request, model=status['model'], stream=False)
                if any(isinstance(m.get('content'), list) for m in request['messages']) and not status['vision']['enabled']:
                    raise StrataError('Enable the bundled vision encoder in this profile')
                result = client.request('/v1/chat/completions', request, check=check)
                message = result['choices'][0]['message']
                text = message.get('content') or ''
                if not text:
                    raise StrataError('Model returned no final answer; increase max_tokens or reduce reasoning effort')
                answers.append((text, message.get('reasoning_content') or '', json.dumps(result.get('usage') or {}, ensure_ascii=False)))
            return answers
        finally:
            if cleanup_required:
                cleanup(client, profile, manager, baseline)


def control(connection, action, check=interrupted):
    if action == 'status':
        return _control(connection, action, check)
    with profile_lock(connection.profile, check):
        return _control(connection, action, check)


def _control(connection, action, check=interrupted):
    if action not in ('status', 'start', 'load', 'unload', 'stop'):
        raise StrataError('Unknown control action')
    profile = read_profile(connection.profile)
    client = Client(profile)
    if action == 'status':
        return client.request('/v1/status', timeout=5, check=check)
    with service_lock(profile, check=check):
        manager = Managed(connection.profile, profile) if profile['mode'] == 'managed' else None
        if action == 'stop':
            if not manager:
                raise StrataError('Only a managed profile can stop its owned server')
            manager.stop()
            return {'service': 'strata', 'stopped': True}
        if action != 'status' and not profile.get('allow_lifecycle'):
            raise StrataError('Enable lifecycle control explicitly for this external profile')
        if manager and action in ('start', 'load'):
            manager.ensure(client, check=check)
        if action == 'load' and profile.get('same_gpu'):
            status = client.request('/v1/status', timeout=5, check=check)
            if status.get('protocol_version') != 1 or status['activity']['in_flight']:
                raise StrataError('Load requires an idle compatible Strata-T8 service')
            cleanup(client, profile, manager, None)
        baseline = gpu_handoff(profile) if action == 'load' and profile.get('same_gpu') else None
        status = None
        try:
            if action == 'load':
                client.request('/v1/load', {}, check=check)
            elif action == 'unload':
                cleanup(client, profile, manager, None)
            status = client.request('/v1/status', timeout=5)
        finally:
            if action == 'load' and (profile.get('same_gpu') or status is None) and profile.get('allow_lifecycle'):
                cleanup(client, profile, manager, baseline)
                if status is not None:
                    status['after_release'] = client.request('/v1/status', timeout=5, check=None)
        return status

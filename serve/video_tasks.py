"""Owned local video sources and atomic, opt-in analysis checkpoints.

The client drives inference. Closing its connection never leaves a GPU job
running in this store. Source bytes, prompts and notes stay on this machine.
"""
from __future__ import annotations
import base64
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

MAX_SOURCE = 128 * 1024**2
MAX_STATE = 8 * 1024**2
MAX_TOTAL = 2 * 1024**3
TTL = 24 * 3600
_ID = re.compile(r'[0-9a-f]{32}\Z')
_OWNER = re.compile(r'[0-9a-f]{64}\Z')


def runtime_context(svc, root):
    """Fingerprint immutable software/configuration, excluding load/busy state."""
    root = Path(root).resolve()
    files = {}
    names = {'meta.json', 'serve/video.py', 'serve/video_tasks.py', 'serve/server.py',
             'serve/media_jobs.py', 'serve/media_worker.py', 'serve/web/app.js',
             'serve/web/video-analysis.js', 'serve/web/speech-analysis.js',
             'tools/strata_video.py', 'asr-settings.json'}
    names.update(path.relative_to(root).as_posix() for path in (root / 'serve/asr').rglob('*.py'))
    for name in sorted(names):
        path = root / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    config = getattr(svc, 'config_path', None)
    if config and Path(config).is_file():
        files['configuration'] = hashlib.sha256(Path(config).read_bytes()).hexdigest()
    engine = svc.engine
    spawn = getattr(engine, 'spawn', ())
    signatures = {}
    # A projector/reader can be replaced without changing its configured path.
    # Resume must not mix visual evidence made by two different readers. Keep
    # loading/capability state out of the identity and never hash large weights.
    vision = getattr(svc, 'vision', None)
    vision_spawn = getattr(vision, 'spawn', ())
    readers = getattr(vision, 'recovery_readers', (vision_spawn[0],) if vision_spawn else ())
    if readers:
        signatures['vision_args'] = [list(args) for args in readers]
        candidates = []
        for args in readers:
            if args:
                candidates.append(args[0])
                candidates.extend(args[index+1] for index, arg in enumerate(args[:-1])
                                  if arg in ('--mmproj', '--model'))
        signatures['vision_assets'] = []
        for value in sorted(set(candidates)):
            path = Path(value)
            if not path.is_absolute():
                path = Path(getattr(vision, 'dir', root)) / path
            try:
                if path.is_file():
                    stat = path.stat()
                    signatures['vision_assets'].append([str(path.resolve()), stat.st_size, stat.st_mtime_ns])
            except OSError as error:
                signatures['vision_assets'].append([str(path), 'unavailable', type(error).__name__])
    # A restored task already contains speech, so it bypasses AsrManager's
    # normal cache validation. Include the selected external assets and native
    # component here as cheap file signatures, without reading large weights.
    from serve.asr import config as asr_config
    try:
        settings = asr_config.load(root)
    except (ValueError, TypeError, RecursionError):
        # An invalid optional ASR profile must not disable visual-only tasks.
        # Its bytes are already fingerprinted above, so fixing it invalidates
        # any checkpoint made while it was unusable.
        settings = dict(asr_config.DEFAULTS)
    settings = asr_config.resolve_paths(root, settings)
    python, build = asr_config.runtime_paths(root, settings['device'])
    assets = [python]
    assets.extend(path for path in build.rglob('*') if path.suffix.lower() in ('.dll', '.pyd'))
    if settings['model_dir']:
        assets.extend(Path(settings['model_dir']) / item[0]
                      for item in (asr_config.MODELS[settings['quantization']], asr_config.PROJECTOR))
    assets.extend(asr_config.vad_path(root, settings) / name
                  for name in ('fireredvad_stream_vad_with_cache.onnx', 'cmvn.ark'))
    signatures['asr_assets'] = []
    for path in sorted(set(assets)):
        if not path.is_absolute():
            path = root / path
        try:
            if path.is_file():
                stat = path.stat()
                signatures['asr_assets'].append([str(path.resolve()), stat.st_size, stat.st_mtime_ns])
        except OSError as error:
            # ASR is optional: an unavailable speech drive must not prevent
            # video-only capability checks. Its return still changes identity.
            signatures['asr_assets'].append([str(path), 'unavailable', type(error).__name__])
    for name, value in (('engine', spawn[0] if spawn else None), ('model', getattr(engine, 'model_path', None))):
        if value:
            path = Path(value)
            if not path.is_absolute() and spawn and spawn[2]:
                path = Path(spawn[2]) / path
            if path.exists():
                stat = path.stat()
                signatures[name] = [str(path.resolve()), stat.st_size, stat.st_mtime_ns]
                if path.is_dir():
                    signatures[name+'_files'] = [[p.relative_to(path).as_posix(),p.stat().st_size,p.stat().st_mtime_ns]
                                                  for p in sorted(path.rglob('*')) if p.is_file()][:4096]
    return digest({'model':svc.model, 'files':files, 'inputs':signatures,
                   'engine_args':spawn[1] if spawn else [], 'sampling':getattr(svc, 'sampling_defaults', {})})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _json_bytes(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > MAX_STATE:
        raise ValueError('Video checkpoint exceeds 8 MiB')
    return raw


def _atomic(path, value):
    raw = _json_bytes(value)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE:
        raise ValueError('Video record is unavailable')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise ValueError('Video record is unavailable') from None


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_plan(plan):
    if not isinstance(plan, dict) or not isinstance(plan.get('segments'), list) or not 1 <= len(plan['segments']) <= 512:
        raise ValueError('Invalid video task plan')
    if any(not _finite_number(plan.get(k)) for k in ('start_s','end_s')):
        raise ValueError('Invalid task window bounds')
    end = plan.get('start_s')
    for index, segment in enumerate(plan['segments']):
        if not isinstance(segment, dict) or type(segment.get('index')) is not int or segment['index'] != index or segment.get('start_s') != end:
            raise ValueError('Video task windows must be consecutive')
        start, end = segment.get('start_s'), segment.get('end_s')
        if any(not _finite_number(n) for n in (start, end)) or start < 0 or end <= start:
            raise ValueError('Invalid video task time range')
        if type(segment.get('max_frames')) is not int or not 2 <= segment['max_frames'] <= 64:
            raise ValueError('Invalid video task frame budget')
        rate=segment.get('sampling_fps')
        if not _finite_number(rate) or not 0<rate<=120:
            raise ValueError('Invalid video task sampling rate')
    if end != plan.get('end_s') or end - plan['start_s'] > 3600:
        raise ValueError('Invalid video task duration')


def validate_state(state, plan):
    if not isinstance(state, dict) or set(state) - {'notes', 'times', 'empty_segments', 'speech', 'evidence', 'fusion', 'phase', 'memory', 'merge_state'}:
        raise ValueError('Invalid video checkpoint fields')
    _json_bytes(state)
    notes, times = state.get('notes', []), state.get('times', [])
    if not isinstance(notes, list) or len(notes) > len(plan['segments']) or not isinstance(times, list) or len(times) > 32768:
        raise ValueError('Invalid video checkpoint progress')
    empty = 0
    for note, segment in zip(notes, plan['segments']):
        if (not isinstance(note, dict) or note.get('start_s') != segment['start_s'] or note.get('end_s') != segment['end_s']
                or type(note.get('frames')) is not int or not 0 <= note['frames'] <= segment['max_frames']
                or not isinstance(note.get('text'), str) or not note['text'].strip() or len(note['text']) > 6000):
            raise ValueError('Invalid committed video window')
        empty += note['frames'] == 0
    if state.get('empty_segments', 0) != empty:
        raise ValueError('Invalid empty video window count')
    last = plan['segments'][len(notes)-1]['end_s'] if notes else plan['start_s']
    if any(not _finite_number(t) or not plan['start_s'] <= t < last + 1e-6 for t in times):
        raise ValueError('Invalid checkpoint frame times')
    if len(times) != sum(n['frames'] for n in notes):
        raise ValueError('Checkpoint frame count differs from committed windows')
    if state.get('phase', 'observe') not in ('observe', 'merge', 'fusion', 'complete'):
        raise ValueError('Invalid video checkpoint phase')
    if state.get('phase') in ('merge', 'fusion', 'complete') and len(notes) != len(plan['segments']):
        raise ValueError('An incomplete video cannot be marked complete')
    if state.get('phase')=='complete' and not isinstance(state.get('evidence'),str):
        raise ValueError('Completed video task lacks its evidence text')
    for field in ('evidence', 'memory'):
        if field in state and (not isinstance(state[field], str) or len(state[field]) > 16000):
            raise ValueError('Invalid checkpoint text budget')
    if 'speech' in state:
        from serve.asr.contract import transcript
        transcript(state['speech'])
    if 'fusion' in state and (not isinstance(state['fusion'], list) or len(state['fusion']) > 1024
                             or any(not isinstance(s, str) or len(s) > 3000 for s in state['fusion'])):
        raise ValueError('Invalid audio fusion checkpoint')
    if 'merge_state' in state:
        value=state['merge_state']
        if (not isinstance(value,dict) or set(value)!={'round','records','next'} or type(value['round']) is not int
                or not 0<=value['round']<=10 or any(not isinstance(value[k],list) or len(value[k])>512
                    or any(not isinstance(text,str) or not text.strip() or len(text)>6200 for text in value[k]) for k in ('records','next'))):
            raise ValueError('Invalid hierarchical merge progress')


class VideoStore:
    def __init__(self, root, *, clock=time.time, quota=MAX_TOTAL):
        self.root = Path(root) / '.video-tasks'
        self.clock, self.quota = clock, quota
        self.lock = threading.RLock()
        self.active = {}

    def _directory(self):
        if self.root.is_symlink() or getattr(self.root,'is_junction',lambda:False)():
            raise ValueError('Video task directory must be a local directory')
        self.root.mkdir(exist_ok=True)
        return self.root

    def _owner(self, owner):
        if not isinstance(owner, str) or not _OWNER.fullmatch(owner):
            raise ValueError('A private video owner token is required')
        return hashlib.sha256(owner.encode()).hexdigest()

    def _path(self, identity, suffix):
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            raise ValueError('Invalid video handle')
        return self._directory() / (identity + suffix)

    def _stored(self, path, suffix):
        """Validate the envelope before a persisted record can drive an operation.

        Progress/context compatibility remains the responsibility of get(); a
        valid older task must still be listable and deletable after an update.
        """
        try:
            record = _read(path)
            identity = path.name.removesuffix(suffix)
            valid = (suffix in ('.source.json', '.task.json') and _ID.fullmatch(identity)
                     and record.get('id') == identity
                     and isinstance(record.get('owner'), str) and _OWNER.fullmatch(record['owner'])
                     and type(record.get('expires')) in (int, float) and math.isfinite(record['expires']))
            if suffix == '.source.json':
                valid = (valid and type(record.get('bytes')) is int and 0 < record['bytes'] <= MAX_SOURCE
                         and isinstance(record.get('sha256'), str) and _OWNER.fullmatch(record['sha256']))
            else:
                valid = (valid and isinstance(record.get('source_id'), str) and _ID.fullmatch(record['source_id'])
                         and isinstance(record.get('source_sha256'), str) and _OWNER.fullmatch(record['source_sha256'])
                         and type(record.get('revision')) is int and record['revision'] >= 0
                         and isinstance(record.get('config'), dict) and isinstance(record.get('state'), dict)
                         and isinstance(record['state'].get('notes', []), list) and 'context' in record)
                if valid:
                    validate_plan(record['config'].get('plan'))
            if not valid:
                raise ValueError('Video record is unavailable')
        except (KeyError, TypeError, OverflowError, RecursionError):
            raise ValueError('Video record is unavailable') from None
        return record

    def _record(self, identity, owner, suffix):
        record = self._stored(self._path(identity, suffix), suffix)
        if record.get('owner') != self._owner(owner) or record.get('expires', 0) <= self.clock():
            raise ValueError('Video handle has expired or belongs to another browser')
        return record

    def _record_space(self, records):
        """Called under the store lock before replacing any of these records."""
        total = sum(p.stat().st_size for p in self.root.iterdir()
                    if p not in records and p.is_file() and not p.is_symlink())
        total += sum(len(_json_bytes(value)) for value in records.values())
        if total > self.quota:
            raise ValueError('Local video storage is full; clear saved tasks first')

    def _remove_source_files(self, identity):
        # Keep retention metadata until the potentially locked payload is gone.
        # A later retry/TTL pass can then finish either deletion safely.
        self._path(identity, '.media').unlink(missing_ok=True)
        self._path(identity, '.source.json').unlink(missing_ok=True)

    def cleanup(self):
        with self.lock:
            for path in self._directory().glob('*.json'):
                suffix = next((s for s in ('.source.json', '.task.json') if path.name.endswith(s)), None)
                if suffix is None or path.is_symlink():
                    continue
                try:
                    record = self._stored(path, suffix)
                except (ValueError, OSError):
                    continue
                if record.get('expires', 0) <= self.clock() and path.name.split('.')[0] not in self.active:
                    try:
                        if path.name.endswith('.source.json'):
                            self._remove_source_files(path.name.split('.')[0])
                        else:
                            path.unlink(missing_ok=True)
                    except OSError:
                        continue

    def delete_source(self,owner,identity):
        with self.lock:
            self._record(identity,owner,'.source.json')
            if identity in self.active:raise ValueError('Video source is in use')
            for path in self.root.glob('*.task.json'):
                if self._stored(path, '.task.json')['source_id']==identity:raise ValueError('Clear the saved task before deleting its source')
            self._remove_source_files(identity)
            return {'deleted':True}

    def upload(self, owner, encoded, *, persistent=False, name='video', cancel=None):
        owner_hash = self._owner(owner)
        if type(persistent) is not bool or not isinstance(name, str) or len(name) > 255:
            raise ValueError('Invalid video retention settings')
        if not isinstance(encoded, str) or len(encoded) > (MAX_SOURCE+2)//3*4:
            raise ValueError('Video source exceeds 128 MiB')
        with self.lock:
            self.cleanup()
            occupied = sum(p.stat().st_size for p in self.root.iterdir() if p.is_file() and not p.is_symlink())
            if occupied + len(encoded)*3//4 > self.quota or len(list(self.root.glob('*.source.json'))) >= 128:
                raise ValueError('Local video storage is full; clear saved tasks first')
            identity, hasher, size = uuid.uuid4().hex, hashlib.sha256(), 0
            path = self._path(identity, '.media')
            try:
                with path.open('xb') as stream:
                    # Four-aligned blocks decode independently; cancellation is bounded.
                    for offset in range(0, len(encoded), 4*1024**2):
                        if cancel is not None and cancel.is_set():
                            raise InterruptedError('Video upload cancelled')
                        block = base64.b64decode(encoded[offset:offset+4*1024**2], validate=True)
                        size += len(block)
                        if size > MAX_SOURCE:
                            raise ValueError('Video source exceeds 128 MiB')
                        hasher.update(block)
                        stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not size:
                    raise ValueError('Video source is empty')
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('Video upload cancelled')
                expires = self.clock() + (TTL if persistent else 3600)
                record = {'id': identity, 'owner': owner_hash, 'bytes': size, 'sha256': hasher.hexdigest(),
                          'expires': expires, 'name': name, 'persistent': persistent}
                record_path = self._path(identity, '.source.json')
                self._record_space({record_path:record})
                _atomic(record_path, record)
                return {k:v for k,v in record.items() if k != 'owner'}
            except BaseException:
                path.unlink(missing_ok=True)
                raise

    @contextmanager
    def source(self, identity, owner, *, verify=False):
        with self.lock:
            record = self._record(identity, owner, '.source.json')
            path = self._path(identity, '.media')
            if path.is_symlink() or not path.is_file() or path.stat().st_size != record['bytes']:
                raise ValueError('Saved video source has changed')
            if verify:
                hasher = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(4*1024**2), b''):
                        hasher.update(block)
                if hasher.hexdigest() != record['sha256']:
                    raise ValueError('Saved video source has changed')
            self.active[identity] = self.active.get(identity,0)+1
        try:
            yield path, record
        finally:
            with self.lock:
                self.active[identity] -= 1
                if not self.active[identity]:self.active.pop(identity)

    def create(self, owner, source, config, context):
        if not isinstance(config, dict) or set(config)-{'plan','question','mode','sampling','name','asr','continuity'}:
            raise ValueError('Invalid video task configuration')
        validate_plan(config.get('plan'))
        if not isinstance(config.get('question', ''), str) or len(config.get('question', '')) > 16384:
            raise ValueError('Video task question exceeds its budget')
        with self.lock, self.source(source, owner, verify=True) as (_, record):
            if len(list(self.root.glob('*.task.json'))) >= 128:
                raise ValueError('Too many saved video tasks; clear an older task')
            identity = uuid.uuid4().hex
            fingerprint = digest({'source':record['sha256'], 'config':config, 'context':context})
            task = {'id':identity, 'owner':self._owner(owner), 'source_id':source, 'source_sha256':record['sha256'],
                    'config':config, 'context':context, 'fingerprint':fingerprint, 'revision':0,
                    'state':{'notes':[], 'times':[], 'empty_segments':0, 'phase':'observe'},
                    'expires':self.clock()+TTL}
            record.update(persistent=True, expires=task['expires'])
            self._record_space({self._path(source, '.source.json'):record,
                                self._path(identity, '.task.json'):task})
            _atomic(self._path(source, '.source.json'), record)
            _atomic(self._path(identity, '.task.json'), task)
            return self._public(task)

    @staticmethod
    def _public(task):
        return {k:v for k,v in task.items() if k not in ('owner', 'context')}

    def get(self, owner, identity, context):
        with self.lock:
            task = self._record(identity, owner, '.task.json')
            if task['context'] != context:
                raise ValueError('Model, engine or runtime settings changed; start a new video analysis')
            with self.source(task['source_id'], owner, verify=True) as (_, record):
                if record['sha256'] != task['source_sha256']:
                    raise ValueError('Saved video source has changed')
            validate_state(task['state'], task['config']['plan'])
            return self._public(task)

    def checkpoint(self, owner, identity, revision, state, context):
        with self.lock:
            task = self.get(owner, identity, context)
            if type(revision) is not int or revision != task['revision']:
                raise ValueError('Video progress changed in another page; reload saved tasks')
            validate_state(state, task['config']['plan'])
            old = task['state'].get('notes', [])
            if len(state.get('notes', [])) < len(old) or state.get('notes', [])[:len(old)] != old:
                raise ValueError('Committed video windows cannot be rewritten')
            task.update(state=state, revision=revision+1, expires=self.clock()+TTL, owner=self._owner(owner), context=context)
            source = self._record(task['source_id'], owner, '.source.json')
            source['expires'] = task['expires']
            self._record_space({self._path(identity, '.task.json'):task,
                                self._path(task['source_id'], '.source.json'):source})
            _atomic(self._path(task['source_id'], '.source.json'), source)
            # Renew the dependency before publishing its checkpoint revision.
            # If renewal fails the caller can retry the same revision; if the
            # task write fails only the source's harmless retention is extended.
            _atomic(self._path(identity, '.task.json'), task)
            return self._public(task)

    def list(self, owner):
        hashed = self._owner(owner)
        with self.lock:
            self.cleanup()
            result = []
            for path in self.root.glob('*.task.json'):
                try:
                    task = self._stored(path, '.task.json')
                except (ValueError, OSError):
                    continue
                if task.get('owner') == hashed:
                    result.append({'id':task['id'], 'config':task['config'], 'revision':task['revision'],
                                   'completed':len(task['state'].get('notes', [])), 'phase':task['state'].get('phase'),
                                   'expires':task['expires']})
            return {'tasks':result}

    def delete(self, owner, identity):
        with self.lock:
            task = self._record(identity, owner, '.task.json')
            source = task['source_id']
            if source in self.active:
                raise ValueError('Video source is in use; stop the current request first')
            marker = self._path(source, '.source.json')
            source_record = self._stored(marker, '.source.json') if marker.exists() or marker.is_symlink() else None
            if source_record is not None and (source_record['owner'] != task['owner']
                                               or source_record['sha256'] != task['source_sha256']):
                raise ValueError('Saved video source has changed')
            selected = self._path(identity, '.task.json')
            # Check dependencies before committing deletion. An unreadable
            # sibling must not consume the caller's still-retryable task.
            siblings = [self._stored(p, '.task.json') for p in self.root.glob('*.task.json') if p != selected and not p.is_symlink()]
            selected.unlink()
            result = {'deleted':True}
            if not any(s.get('source_id') == source for s in siblings):
                if source_record is None:
                    # The task is authorized, but its reference alone cannot
                    # authorize deleting a payload with missing ownership data.
                    media = self._path(source, '.media')
                    if media.exists() or media.is_symlink():
                        result['cleanup_pending'] = True
                else:
                    try:
                        self._remove_source_files(source)
                    except OSError:
                        # Task deletion is committed. Payload cleanup can be
                        # retried using its retained metadata after a lock clears.
                        result['cleanup_pending'] = True
            return result

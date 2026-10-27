"""Optional owned ASR jobs with bounded cancellation, caching and process cleanup."""
from __future__ import annotations
import base64
from contextlib import contextmanager, nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from tools.portable_io import atomic_json
from . import config
from .contract import from_confucius, transcript
from .media import check


@contextmanager
def resource_lease(identity='cpu'):
    """An OS-released lock shared by GUI and node clients; no stale lockfile ownership."""
    base = Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir()))/'Strata/asr-leases'
    base.mkdir(parents=True,exist_ok=True)
    with (base/(hashlib.sha256(identity.encode()).hexdigest()+'.lock')).open('a+b') as stream:
        stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Another ASR task owns this device; wait for it to finish') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name=='nt':
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(stream,fcntl.LOCK_UN)


class AsrManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.state = {'phase':'idle'}

    def progress(self):
        with self.state_lock:
            return dict(self.state)

    def update(self, **values):
        with self.state_lock:
            self.state = {**self.state, **values}

    def upload(self, request, cancel=None, *, gpu_scope=None, max_bytes=128*1024**2):
        if not isinstance(request,dict) or set(request)-{'data','start_s','end_s','audio_track','channel','language','context','offset_ms'}:
            raise ValueError('Invalid ASR video upload settings')
        encoded = request.get('data')
        if not isinstance(encoded,str) or len(encoded) > (max_bytes+2)//3*4:
            raise ValueError(f'ASR upload exceeds {max_bytes//1024**2} MiB')
        data = base64.b64decode(encoded,validate=True)
        if len(data)>max_bytes:
            raise ValueError(f'ASR upload exceeds {max_bytes//1024**2} MiB')
        return self.transcribe(io.BytesIO(data),request,cancel,gpu_scope=gpu_scope)

    def transcribe(self, source, options=None, cancel=None, *, gpu_scope=None):
        from . import media
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('An ASR task is already running')
        try:
            settings = config.load(self.root)
            options = dict(options or {})
            settings = config.validate({**settings, **{key:options[key] for key in ('audio_track','channel','language','context','offset_ms') if key in options}})
            settings = config.resolve_paths(self.root, settings)
            self.update(phase='extracting',error=None,status=None,cached=False,completed_samples=0,total_samples=0,segments=0)
            from serve.media_jobs import extract_audio
            extracted = extract_audio(source,start_s=options.get('start_s',0),end_s=options.get('end_s'),
                                      audio_track=settings['audio_track'],channel=settings['channel'],cancel=cancel)
            origin = extracted['window_start_s']+settings['offset_ms']/1000
            details = {key:value for key,value in extracted.items() if key!='pcm'}
            if extracted['status'] in ('no_track','silence'):
                result = transcript({'contract_version':1,'timeline':'input','status':extracted['status'],
                                     'source':'confucius','cues':[],'timing_precision':'segment','audio':details})
                self.update(phase='completed',status=result['status'])
                return result
            available = config.availability(self.root,settings)
            if not available['supported']:
                raise RuntimeError('语音组件或模型未配置，请到模型设置检查：'+', '.join(available['missing']))
            check(cancel)
            signature = self._signature(settings, cancel)
            key = hashlib.sha256(json.dumps({'media':media.source_identity(source,cancel),'settings':settings,
                'start':extracted['window_start_s'],'end':extracted['window_end_s'],
                'models':signature,'schema':1,'worker_source':self._source_signature(),
                'runtime':self._runtime_signature(settings,cancel)},sort_keys=True).encode()).hexdigest()
            cache_dir = self.root/'.asr-cache'
            cache_dir.mkdir(exist_ok=True)
            cache = cache_dir/(key+'.json')
            if cache.is_file() and cache.stat().st_size <= 2*1024**2:
                try:
                    result = transcript(json.loads(cache.read_text(encoding='utf-8')))
                    if result['status'] in ('complete','no_speech') and result.get('cache_key') == key:
                        self.update(phase='completed',cached=True,status=result['status'])
                        return {**result,'cache_hit':True}
                except (ValueError,OSError):
                    pass
            python, build = config.runtime_paths(self.root,settings['device'])
            vad = config.vad_path(self.root, settings)
            self.update(phase='loading',cached=False)
            scope = gpu_scope() if settings['device']=='cuda' and gpu_scope is not None else nullcontext()
            if settings['device']=='cuda' and gpu_scope is None:
                raise RuntimeError('CUDA ASR requires an owned Strata GPU handoff; use CPU or the managed API')
            with resource_lease('asr-'+settings['device']), scope, tempfile.TemporaryDirectory(prefix='strata-asr-') as temp:
                check(cancel)
                import numpy as np
                directory = Path(temp)
                np.save(directory/'audio.npy',extracted['pcm'],allow_pickle=False)
                job = {**settings,'build_dir':str(build),'vad_dir':str(vad),'pcm':str(directory/'audio.npy'),
                       'result':str(directory/'result.json'),'progress':str(directory/'progress.json')}
                atomic_json(directory/'job.json',job)
                with (directory/'worker.log').open('wb') as log:
                    process = subprocess.Popen([str(python),'-I','-X','utf8',str(self.root/'serve/asr/worker.py'),str(directory/'job.json')],
                        cwd=self.root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                        env={key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME')},
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    from serve.winjob import contain
                    contain(process)
                    try:
                        deadline = time.monotonic()+max(180, (extracted['window_end_s']-extracted['window_start_s'])*60)
                        while process.poll() is None:
                            check(cancel)
                            if time.monotonic() > deadline:
                                raise TimeoutError('ASR worker exceeded its time budget')
                            progress = directory/'progress.json'
                            if progress.is_file() and progress.stat().st_size<8192:
                                try: self.update(**json.loads(progress.read_text(encoding='utf-8')))
                                except (ValueError,OSError): pass
                            time.sleep(.15)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try: process.wait(timeout=10)
                            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
                    if process.returncode:
                        log.flush()
                        diagnostic = (directory/'worker.log').read_bytes()[-8192:].decode('utf-8','replace')
                        raise RuntimeError('ASR worker failed: '+diagnostic)
                result_path = directory/'result.json'
                if not result_path.is_file() or result_path.stat().st_size > 2*1024**2:
                    raise RuntimeError('ASR worker returned a missing or oversized transcript')
                raw = json.loads(result_path.read_text(encoding='utf-8'))
                result = from_confucius(raw,origin_s=max(0,origin))
                # A negative manual offset clips cues explicitly instead of moving the entire transcript origin.
                if origin < 0:
                    result['cues'] = [{**cue,'start_s':max(0,cue['start_s']+origin),'end_s':cue['end_s']+origin}
                                      for cue in result['cues'] if cue['end_s']+origin>0]
                    result = transcript(result)
                result.update({'audio':details,'model':settings['quantization'],'device':settings['device'],'model_digests':signature,
                               'cache_key':key,'cache_hit':False,'requires_review':bool(raw.get('forced_boundaries') or
                                   any(cue.get('requires_review') for cue in result['cues']))})
            check(cancel)
            if result['status'] in ('complete','no_speech'):
                atomic_json(cache,result)
            self.update(phase='completed',status=result['status'])
            return result
        except BaseException as error:
            self.update(phase='cancelled' if isinstance(error,InterruptedError) else 'failed',error=str(error))
            raise
        finally:
            self.lock.release()

    def _source_signature(self):
        digest = hashlib.sha256()
        sources=set((self.root/'serve/asr').rglob('*.py'))
        sources.update(path for path in (self.root/'serve/media_jobs.py',self.root/'serve/media_worker.py',
                                        self.root/'serve/video.py',self.root/'tools/strata_video.py') if path.is_file())
        for path in sorted(sources):
            digest.update(path.relative_to(self.root).as_posix().encode()); digest.update(path.read_bytes())
        return digest.hexdigest()

    def _runtime_signature(self, settings, cancel=None):
        """Invalidate cached text when the installed native component changes."""
        _, build = config.runtime_paths(self.root, settings['device'])
        digest = hashlib.sha256()
        for path in sorted(build.rglob('*')):
            if path.is_file() and path.suffix.lower() in ('.dll','.pyd'):
                digest.update(path.relative_to(build).as_posix().encode())
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(4*1024**2),b''):
                        check(cancel)
                        digest.update(block)
        return digest.hexdigest()

    def _signature(self, settings, cancel=None):
        settings = config.resolve_paths(self.root, settings)
        signatures = {}
        for name,size,digest in (config.MODELS[settings['quantization']],config.PROJECTOR):
            path = (Path(settings['model_dir'])/name).resolve(strict=True)
            if path.parent != Path(settings['model_dir']).resolve() or path.stat().st_size!=size:
                raise ValueError('Invalid or linked ASR model file')
            # Model hashing occurs only on an enabled ASR task, never during normal startup.
            with path.open('rb') as stream:
                actual = self._hash(stream,cancel)
            if actual!=digest:
                raise ValueError('ASR model SHA256 mismatch: '+name)
            signatures[name] = digest
        vad = config.vad_path(self.root, settings)
        from tools.prepare_asr_models import VAD
        for name,size,digest in VAD:
            path = vad/name
            if not path.is_file() or path.stat().st_size!=size:
                raise ValueError('ASR VAD asset is missing or incomplete: '+name)
            with path.open('rb') as stream:
                actual = self._hash(stream,cancel)
            if actual!=digest:
                raise ValueError('ASR VAD SHA256 mismatch: '+name)
            signatures['vad/'+name] = digest
        return signatures

    @staticmethod
    def _hash(stream, cancel):
        digest = hashlib.sha256()
        for block in iter(lambda:stream.read(4*1024**2),b''):
            check(cancel)
            digest.update(block)
        return digest.hexdigest()

"""Disposable native media jobs; a decoder fault cannot terminate the HTTP server."""
from __future__ import annotations
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

_MEDIA_LOCK = threading.Lock()
_WORKER_SCRIPT = Path(__file__).with_name('media_worker.py')
MAX_VIDEO_BYTES = 128 * 1024**2
MAX_AUDIO_BYTES = 256 * 1024**2
MAX_PCM_BYTES = 3600 * 16000 * 4
MAX_RESULT_BYTES = 192 * 1024**2


class MediaWorkerError(RuntimeError):
    pass


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Media decoding cancelled')


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    else:
        process.wait()
    if process.poll() is None:
        raise MediaWorkerError('The owned media worker did not exit')


def _write_source(source, target, limit, cancel):
    _check(cancel)
    if isinstance(source, (bytes, bytearray, io.BytesIO)):
        data = source.getbuffer() if isinstance(source, io.BytesIO) else memoryview(source)
        try:
            if len(data) > limit:
                raise ValueError('Media source exceeds its input byte limit')
            with target.open('wb') as output:
                for offset in range(0, len(data), 4*1024**2):
                    _check(cancel)
                    output.write(data[offset:offset+4*1024**2])
        finally:
            data.release()
        return
    if not isinstance(source, (str, Path)) or '://' in str(source):
        raise ValueError('Media decoding requires a local regular file or BytesIO')
    try:
        path = Path(source)
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError('Media source is missing or exceeds its input byte limit')
        size = 0
        with path.open('rb') as stream, target.open('wb') as output:
            while block := stream.read(4*1024**2):
                _check(cancel)
                size += len(block)
                if size > limit:
                    raise ValueError('Media source exceeds its input byte limit')
                output.write(block)
    except OSError:
        raise ValueError('The local media source could not be read') from None


def _diagnostic(directory):
    path = directory/'worker.log'
    if not path.is_file():
        return ''
    with path.open('rb') as stream:
        stream.seek(max(0,path.stat().st_size-2048))
        value = stream.read().decode('utf-8','replace')
    # IPC inputs use fixed names. Never include the user's source filename or
    # input bytes in a crash message, and keep traceback/log text bounded.
    return _safe_message(value,directory)[-1200:]


def _safe_message(value,directory):
    path=str(directory)
    # FFmpeg errors may nest repr(filename), producing multiple levels of
    # escaped Windows separators before JSON transports the message.
    for count in (8,4,2,1):
        value=value.replace(path.replace('\\','\\'*count),'<media-job>')
    return value.replace(directory.as_posix(),'<media-job>').replace('input.media','<uploaded media>')


def _run(operation, source, options, cancel=None, *, timeout_s=150):
    if operation not in ('video','audio'):
        raise ValueError('Unsupported media operation')
    if isinstance(timeout_s,bool) or not isinstance(timeout_s,(int,float)) or not math.isfinite(timeout_s) or timeout_s<=0:
        raise ValueError('Invalid media time budget')
    encoded = json.dumps({'operation':operation,'options':options},ensure_ascii=False,allow_nan=False)
    if len(encoded.encode('utf-8')) > 32768:
        raise ValueError('Media settings exceed their IPC budget')
    deadline=time.monotonic()+timeout_s
    acquired=False
    try:
        while not acquired:
            _check(cancel)
            if time.monotonic()>=deadline:
                raise TimeoutError('Media worker queue exceeded its time budget')
            acquired=_MEDIA_LOCK.acquire(timeout=min(.05,max(.001,deadline-time.monotonic())))
        with tempfile.TemporaryDirectory(prefix='strata-media-') as temporary:
            # Windows may give TEMP an 8.3 username path while the worker's
            # resolve() expands it; use one canonical IPC path on both sides.
            directory=Path(temporary).resolve()
            _write_source(source,directory/'input.media',MAX_VIDEO_BYTES if operation=='video' else MAX_AUDIO_BYTES,cancel)
            (directory/'job.json').write_text(encoded,encoding='utf-8')
            _check(cancel)
            if time.monotonic()>=deadline:
                raise TimeoutError('Media input preparation exceeded its time budget')
            retries=0
            while True:
                _check(cancel)
                if time.monotonic()>=deadline:
                    raise TimeoutError('Media decoding exceeded its time budget')
                # The preceding process was waited to exit before this retry.
                # Its incomplete JSON/PCM must never become the next result.
                for name in ('result.json','audio.f32'):
                    (directory/name).unlink(missing_ok=True)
                with (directory/'worker.log').open('ab' if retries else 'wb') as log:
                    process=subprocess.Popen([sys.executable,'-I','-X','utf8','-X','faulthandler',str(_WORKER_SCRIPT),str(directory/'job.json')],
                        cwd=_WORKER_SCRIPT.resolve().parents[1],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                        env={key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME')},
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    try:
                        from serve.winjob import contain
                        contain(process)
                        while process.poll() is None:
                            _check(cancel)
                            if time.monotonic()>=deadline:
                                raise TimeoutError('Media decoding exceeded its time budget')
                            time.sleep(.03)
                        _check(cancel)
                    finally:
                        _stop(process)
                    if process.returncode:
                        _check(cancel)
                        if retries==0:
                            retries=1
                            continue
                        log.flush()
                        detail=_diagnostic(directory)
                        raise MediaWorkerError(f'Media decoder process failed (exit {process.returncode}); the service remains running.'+
                                               (' Diagnostic: '+detail if detail else ''))
                break
            result_path=directory/'result.json'
            budget=MAX_RESULT_BYTES if operation=='video' else 1024*1024
            if not result_path.is_file() or result_path.stat().st_size>budget:
                raise MediaWorkerError('Media worker returned a missing or oversized result')
            try:
                envelope=json.loads(result_path.read_text(encoding='utf-8'))
            except (ValueError,UnicodeError):
                raise MediaWorkerError('Media worker returned invalid result JSON') from None
            if not isinstance(envelope,dict):
                raise MediaWorkerError('Media worker returned an invalid result envelope')
            if 'error' in envelope:
                error=envelope['error']
                if not isinstance(error,dict) or error.get('type') not in ('ValueError','TimeoutError','InterruptedError') or not isinstance(error.get('message'),str):
                    raise MediaWorkerError('Media worker returned an invalid error envelope')
                message=_safe_message(error['message'],directory)[:1200]
                raise {'ValueError':ValueError,'TimeoutError':TimeoutError,'InterruptedError':InterruptedError}[error['type']](message)
            result=envelope.get('result')
            if not isinstance(result,dict):
                raise MediaWorkerError('Media worker returned no completed result')
            if retries:
                result={**result,'media_worker_retries':retries}
            if operation=='audio':
                result=_audio_result(result,directory)
                _check(cancel)
                return result
            from serve import video
            if result.get('empty_segment') is True:
                _validate_empty_video(result,options)
            else:
                try:
                    video.video_part(result['part'])
                    from tools.strata_video import validate_manifest
                    validate_manifest(result['manifest'],len(result['part']['frames']))
                except (ValueError,TypeError,KeyError):
                    raise MediaWorkerError('Media worker returned an invalid video contract') from None
            _check(cancel)
            return result
    finally:
        if acquired:
            _MEDIA_LOCK.release()


def _validate_empty_video(result,options):
    try:
        manifest=result['manifest'];source=manifest['source'];sampling=manifest['sampling'];coverage=manifest['coverage']
        if (not options['allow_empty_segment'] or result.get('part') is not None or manifest.get('frames')!=[]
                or type(manifest.get('contract_version')) is not int or manifest['contract_version']!=1
                or type(coverage.get('sample_count')) is not int or coverage['sample_count']!=0):
            raise ValueError('Invalid empty media envelope')
        from tools.strata_video import finite
        begin=finite(sampling['start_s'],'empty start')
        end=finite(sampling['end_s'],'empty end')
        rate=finite(sampling['sampling_fps'],'empty rate',positive=True)
        finite(source.get('time_origin_s',0),'empty origin',minimum=-1e12)
        duration=source.get('duration_s')
        if duration is not None:duration=finite(duration,'empty source duration',positive=True)
        requested_start=finite(options['start_s'],'requested start')
        requested_end=options['end_s']
        if requested_end is not None:requested_end=finite(requested_end,'requested end')
        expected_end=(min(requested_end,duration) if duration is not None and requested_end is not None
                      else requested_end or duration or requested_start+min(3600,options['max_frames']/rate))
        if (end<=begin or end-begin>3600 or not math.isclose(begin,requested_start,abs_tol=1e-6)
                or not math.isclose(end,expected_end,abs_tol=1e-6)
                or rate!=options['sampling_fps'] or sampling['max_frames']!=options['max_frames']
                or sampling['max_pixels']!=589824
                or coverage.get('first_sample_s') is not None or coverage.get('last_sample_s') is not None
                or coverage.get('max_sample_gap_s')!=0
                or not math.isclose(coverage['window_start_s'],begin,abs_tol=1e-6)
                or not math.isclose(coverage['window_end_s'],end,abs_tol=1e-6)):
            raise ValueError('Invalid empty media metadata')
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
        raise MediaWorkerError('Media worker returned an invalid empty video window') from None


def _audio_result(result,directory):
    from serve.asr.contract import number
    if result.get('status') not in ('ready','silence','no_track'):
        raise MediaWorkerError('Media worker returned an invalid audio status')
    if result['status']=='no_track':
        return {**result,'pcm':None}
    try:
        start=number(result.get('window_start_s'),'audio start')
        end=number(result.get('window_end_s'),'audio end')
    except ValueError:
        raise MediaWorkerError('Media worker returned invalid audio bounds') from None
    count=math.ceil((end-start)*16000-1e-8)
    pcm=directory/'audio.f32'
    if start<0 or end<=start or end-start>3600 or not pcm.is_file() or pcm.stat().st_size!=count*4 or count*4>MAX_PCM_BYTES:
        raise MediaWorkerError('Media worker returned invalid audio byte length')
    import numpy as np
    array=np.fromfile(pcm,dtype='<f4')
    if not np.isfinite(array).all():
        raise MediaWorkerError('Media worker returned nonfinite audio samples')
    return {**result,'pcm':array}


def sample_video(data,options,cancel=None,*,timeout_s=150):
    return _run('video',data,options,cancel,timeout_s=timeout_s)


def extract_audio(source,*,start_s=0,end_s=None,audio_track=0,channel='mean',cancel=None,timeout_s=150):
    return _run('audio',source,{'start_s':start_s,'end_s':end_s,'audio_track':audio_track,'channel':channel},
                cancel,timeout_s=timeout_s)

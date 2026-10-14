"""Decode only a bounded local audio track, preserving the video presentation clock."""
from __future__ import annotations
import io
import math
import time
from pathlib import Path
from .contract import number

RATE = 16000
MAX_SECONDS = 3600
MAX_SOURCE_BYTES = 256 * 1024**2


def check(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Speech transcription cancelled')


def source_identity(source, cancel=None):
    import hashlib
    digest = hashlib.sha256()
    if isinstance(source, io.BytesIO):
        check(cancel)
        digest.update(source.getbuffer())
    else:
        with Path(source).open('rb') as stream:
            for block in iter(lambda: stream.read(4*1024**2), b''):
                check(cancel)
                digest.update(block)
    return digest.hexdigest()


def extract(source, *, start_s=0, end_s=None, audio_track=0, channel='mean', cancel=None, timeout_s=120):
    import av
    import numpy as np
    from tools.strata_video import video_duration
    start_s = number(start_s, 'audio window start')
    if start_s < 0 or start_s >= 86400 or type(audio_track) is not int or not 0 <= audio_track < 32:
        raise ValueError('Invalid audio window or track')
    if channel not in ('mean','left','right'):
        raise ValueError('Invalid audio channel')
    if isinstance(source, io.BytesIO):
        size = source.getbuffer().nbytes
        source.seek(0)
    elif isinstance(source,(str,Path)):
        source = Path(source)
        if '://' in str(source) or not source.is_file():
            raise ValueError('Audio extraction requires a local file')
        size = source.stat().st_size
    else:
        raise ValueError('Unsupported audio input')
    if size > MAX_SOURCE_BYTES:
        raise ValueError('Audio source exceeds 256 MiB')
    deadline = time.monotonic()+timeout_s
    check(cancel)
    with av.open(source,mode='r',timeout=(10,10), options={'protocol_whitelist':'file,pipe',
            'format_whitelist':'mov,matroska,webm,avi,flv,mpeg,mpegts,ogg,wav,mp3,flac'}) as container:
        video = next(iter(container.streams.video), None)
        origin = float(video.start_time * video.time_base) if video is not None and video.start_time is not None else None
        if video is not None and origin is None:
            # One frame establishes the clock. Never decode all video just to extract speech.
            first = next(container.decode(video), None)
            if first is None or first.pts is None or first.time_base is None:
                raise ValueError('Video presentation origin is unavailable for speech alignment')
            origin = float(first.pts*first.time_base)
            container.seek(0, backward=True)
        if origin is None:
            origin = float(container.start_time or 0)/av.time_base
        tracks = list(container.streams.audio)
        if not tracks:
            return {'status':'no_track','pcm':None,'source_origin_s':origin,'window_start_s':start_s,'window_end_s':end_s,'track':audio_track}
        if audio_track >= len(tracks):
            raise ValueError('Selected audio track does not exist')
        stream = tracks[audio_track]
        if len(stream.layout.channels) > 8:
            raise ValueError('Audio exceeds eight channels')
        duration = video_duration(container,video) if video is not None else (
            float(stream.duration*stream.time_base) if stream.duration is not None else None)
        stop = min(number(end_s,'audio window end'),duration) if end_s is not None and duration is not None else (
            number(end_s,'audio window end') if end_s is not None else duration)
        if stop is None or stop <= start_s or stop-start_s > MAX_SECONDS:
            raise ValueError('Select a nonempty audio window of at most 3600 seconds')
        first_pts, last_pts = origin+start_s, origin+stop
        count = math.ceil((stop-start_s)*RATE-1e-8)
        pcm = np.zeros(count,dtype=np.float32)
        written = np.zeros(count,dtype=np.bool_)
        resampler = av.AudioResampler(format='fltp', layout=stream.layout, rate=RATE)
        if start_s > 1:
            container.seek(round((first_pts-1)*av.time_base),backward=True)
        decoded, seen_pts = 0, False
        def place(frame):
            nonlocal seen_pts
            if frame.pts is None or frame.time_base is None:
                raise ValueError('Audio PTS is missing; cannot align speech with video')
            pts = float(frame.pts*frame.time_base)
            seen_pts = True
            array = frame.to_ndarray()
            if channel == 'right' and array.shape[0] < 2:
                raise ValueError('Selected right audio channel does not exist')
            mono = array.mean(axis=0) if channel == 'mean' else array[1 if channel=='right' else 0]
            if not np.isfinite(mono).all():
                raise ValueError('Audio contains NaN or infinity')
            destination = round((pts-first_pts)*RATE)
            left, right = max(0,destination), min(count,destination+len(mono))
            if right > left:
                existing = written[left:right]
                values = mono[left-destination:right-destination]
                if np.any(existing):
                    # Duplicate PTS often comes from an overlapping decode range. Keep the first samples.
                    np.copyto(pcm[left:right],values,where=~existing)
                else:
                    pcm[left:right] = values
                written[left:right] = True
        for frame in container.decode(stream):
            check(cancel)
            decoded += 1
            if decoded > 1000000 or time.monotonic() > deadline:
                raise TimeoutError('Audio decoding exceeded its budget')
            pts = float(frame.pts*frame.time_base) if frame.pts is not None and frame.time_base is not None else None
            if pts is None:
                raise ValueError('Audio PTS is missing; cannot align speech with video')
            if pts >= last_pts+.5:
                break
            for converted in resampler.resample(frame):
                place(converted)
        for converted in resampler.resample(None):
            place(converted)
        check(cancel)
        if not seen_pts:
            raise ValueError('Audio track yielded no timestamped samples')
        return {'status':'silence' if not np.any(pcm) else 'ready','pcm':pcm,
                'source_origin_s':origin,'window_start_s':start_s,'window_end_s':stop,'track':audio_track,
                'sample_rate':RATE,'decoded_audio_frames':decoded,'audio_covered_samples':int(written.sum()),
                'gap_samples':int(count-written.sum()),'clock':'original_pts_with_silence_padding'}

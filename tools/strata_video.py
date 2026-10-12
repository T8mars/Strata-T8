"""Bounded CPU video sampling shared by the portable app and ComfyUI nodes.

No server, torch, subprocess, network, or model dependencies. PyAV is imported
only for file decoding. The manifest keeps source timestamps separate from the
clip clock and records the sampling coverage instead of inventing event times.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import math
from pathlib import Path
import time
from typing import Iterable

CONTRACT_VERSION = 1
MAX_FRAMES = 256
MAX_PIXELS = 16_777_216
MAX_RETAINED_BYTES = 256 * 1024**2


class VideoCancelled(RuntimeError):
    pass


@dataclass
class VideoFrame:
    index: int
    timestamp_s: float
    source_timestamp_s: float
    image: object
    timestamp_kind: str = 'pts'
    source_index: int | None = None


@dataclass
class SampledVideo:
    frames: list[VideoFrame]
    manifest: dict


def check_cancel(cancel):
    if cancel is not None:
        stopped = cancel() if callable(cancel) else cancel.is_set()
        if stopped:
            raise VideoCancelled('Video sampling cancelled')


def finite(value, name, *, minimum=0.0, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    value = float(value)
    if not math.isfinite(value) or value < minimum or (positive and value <= minimum):
        raise ValueError(f'{name} is outside its allowed range')
    return value


def _integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name} must be an integer from {low} to {high}')
    return value


def sampling_schedule(start_s, end_s, sampling_fps=1.0, max_frames=64):
    """Media-relative targets; a hard frame cap spreads samples over the window."""
    start_s = finite(start_s, 'start_s')
    end_s = finite(end_s, 'end_s')
    sampling_fps = finite(sampling_fps, 'sampling_fps', positive=True)
    _integer(max_frames, 'max_frames', 1, MAX_FRAMES)
    if end_s <= start_s or sampling_fps > 120:
        raise ValueError('Sampling requires a nonempty window and fps <= 120')
    wanted = max(1, math.ceil((end_s - start_s) * sampling_fps))
    count = min(wanted, max_frames)
    step = max(1 / sampling_fps, (end_s - start_s) / count)
    return [start_s + i * step for i in range(count)]


def _limits(start_s, end_s, sampling_fps, max_frames, max_pixels, max_duration_s):
    start_s = finite(start_s, 'start_s')
    if end_s is not None:
        end_s = finite(end_s, 'end_s')
        if end_s <= start_s:
            raise ValueError('end_s must be after start_s')
    finite(sampling_fps, 'sampling_fps', positive=True)
    if sampling_fps > 120:
        raise ValueError('sampling_fps must not exceed 120')
    _integer(max_frames, 'max_frames', 1, MAX_FRAMES)
    _integer(max_pixels, 'max_pixels', 1024, MAX_PIXELS)
    finite(max_duration_s, 'max_duration_s', positive=True)
    if max_frames * max_pixels * 3 > MAX_RETAINED_BYTES:
        raise ValueError('Frame/pixel budget exceeds the retained CPU memory limit')
    return start_s, end_s


def bounded_image(image, max_pixels=589824):
    from PIL import Image
    _integer(max_pixels, 'max_pixels', 1024, MAX_PIXELS)
    if not isinstance(image, Image.Image) or image.width < 1 or image.height < 1:
        raise ValueError('Expected a nonempty PIL image')
    if image.width * image.height > MAX_PIXELS:
        raise ValueError('Decoded frame exceeds the source pixel limit')
    if image.width * image.height > max_pixels:
        ratio = math.sqrt(max_pixels / (image.width * image.height))
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                             Image.Resampling.LANCZOS)
    return image.convert('RGB').copy()


def _manifest(frames, *, duration, origin, start, end, sampling_fps, max_frames,
              max_pixels, source_kind, decoded=0, targets=None, ended=True):
    media_times = [f.source_timestamp_s - origin for f in frames]
    requested = list(targets or [])
    return {
        'contract_version': CONTRACT_VERSION,
        'source': {'kind': source_kind, 'duration_s': duration, 'time_origin_s': origin},
        'frames': [{'index': f.index, 'timestamp_s': f.timestamp_s,
                    'source_timestamp_s': f.source_timestamp_s, 'timestamp_kind': f.timestamp_kind,
                    'source_index': f.source_index, 'width': f.image.width, 'height': f.image.height}
                   for f in frames],
        'sampling': {'start_s': start, 'end_s': end, 'sampling_fps': sampling_fps,
                     'max_frames': max_frames, 'max_pixels': max_pixels,
                     'requested_targets_s': requested, 'decoded_frames': decoded},
        'coverage': {'sample_count': len(frames), 'window_start_s': start, 'window_end_s': end,
                     'first_sample_s': media_times[0] if media_times else None,
                     'last_sample_s': media_times[-1] if media_times else None,
                     'max_sample_gap_s': max((b-a for a, b in zip(media_times, media_times[1:])), default=0.0),
                     'frame_cap_applied': bool(end is not None and
                                              math.ceil((end-start)*sampling_fps) > max_frames),
                     'timestamp_kind': 'pts' if all(f.timestamp_kind == 'pts' for f in frames) else 'estimated',
                     'sampling_completed': bool(media_times and requested and media_times[-1] + 1e-9 >= requested[-1]),
                     'decode_completed': ended,
                     'continuous_observation': False},
    }


def sample_video(source, *, start_s=0.0, end_s=None, sampling_fps=1.0,
                 max_frames=64, max_pixels=589824, max_duration_s=3600,
                 max_input_bytes=268435456, max_decoded_frames=180000,
                 timeout_s=120.0, crop=None, cancel=None):
    """Decode a local file/seekable stream on CPU, retaining only bounded samples.

    Network playlists/protocols are deliberately not inputs. Callers download or
    upload to their own bounded private stream before calling this function.
    """
    try:
        import av
    except ImportError as error:
        raise ValueError('Video decoding requires PyAV; install av==15.0.0 in this Python environment') from error
    start_s, end_s = _limits(start_s, end_s, sampling_fps, max_frames, max_pixels, max_duration_s)
    _integer(max_input_bytes, 'max_input_bytes', 1, 2**31)
    _integer(max_decoded_frames, 'max_decoded_frames', 1, 1_000_000)
    finite(timeout_s, 'timeout_s', positive=True)
    if crop is not None:
        if not isinstance(crop, (tuple, list)) or len(crop) != 4:
            raise ValueError('Video crop requires x, y, width and height')
        crop = tuple(_integer(value, 'crop coordinate', 0 if i < 2 else 1, MAX_PIXELS)
                     for i, value in enumerate(crop))
    check_cancel(cancel)
    if isinstance(source, (str, Path)):
        path = Path(source)
        if '://' in str(source) or not path.is_file():
            raise ValueError('Video source must be a local regular file')
        if path.stat().st_size > max_input_bytes:
            raise ValueError('Video exceeds the input byte limit')
        source = str(path)
    else:
        try:
            old = source.tell()
            source.seek(0, 2)
            size = source.tell()
            source.seek(old)
        except (AttributeError, OSError) as error:
            raise ValueError('Video input must be a bounded seekable stream') from error
        if size > max_input_bytes:
            raise ValueError('Video exceeds the input byte limit')
    deadline = time.monotonic() + timeout_s
    frames, decoded, target_index, last_pts = [], 0, 0, None
    try:
        with av.open(source, mode='r', timeout=(min(timeout_s, 10), min(timeout_s, 10)),
                     options={'protocol_whitelist': 'file,pipe',
                              'format_whitelist': 'mov,matroska,webm,avi,flv,mpeg,mpegts,ogg,gif'}) as container:
            streams = list(container.streams.video)
            if not streams:
                raise ValueError('Input contains no video stream')
            stream = streams[0]
            if stream.width * stream.height > MAX_PIXELS:
                raise ValueError('Video dimensions exceed the source pixel limit')
            stream.thread_type = 'NONE'
            origin = float(stream.start_time * stream.time_base) if stream.start_time is not None else 0.0
            duration = (float(stream.duration * stream.time_base) if stream.duration is not None
                        else float(container.duration / av.time_base) if container.duration is not None else None)
            if duration is not None and (not math.isfinite(duration) or duration <= 0):
                duration = None
            stop = min(end_s, duration) if end_s is not None and duration is not None else end_s or duration
            if stop is None:
                stop = start_s + min(max_duration_s, max_frames / sampling_fps)
            if stop <= start_s:
                raise ValueError('Requested window is outside the video')
            if stop - start_s > max_duration_s:
                raise ValueError('Requested video window exceeds the duration limit')
            targets = sampling_schedule(start_s, stop, sampling_fps, max_frames)
            rate = float(stream.average_rate) if stream.average_rate else None
            # With no declared origin, decode the first PTS before locating the
            # clip. Resetting origin after a seek would count start_s twice.
            if start_s > 0 and stream.start_time is not None:
                container.seek(int((origin + start_s) * av.time_base), backward=True, any_frame=False)
            ended = False
            for frame in container.decode(stream):
                check_cancel(cancel)
                if time.monotonic() >= deadline:
                    raise TimeoutError('Video decoding exceeded its time limit')
                decoded += 1
                if decoded > max_decoded_frames:
                    raise ValueError('Video exceeds the decoded frame limit')
                if frame.width * frame.height > MAX_PIXELS:
                    raise ValueError('Decoded frame exceeds the source pixel limit')
                if frame.pts is not None and frame.time_base is not None:
                    pts, kind = float(frame.pts * frame.time_base), 'pts'
                elif rate and start_s == 0:
                    pts, kind = origin + (decoded - 1) / rate, 'estimated'
                else:
                    raise ValueError('Video frame has no usable timestamp')
                if not math.isfinite(pts):
                    raise ValueError('Video contains a nonfinite timestamp')
                if stream.start_time is None and decoded == 1:
                    origin = pts
                media_time = pts - origin
                if media_time >= stop:
                    ended = True
                    break
                if last_pts is not None and pts <= last_pts:
                    continue
                last_pts = pts
                if media_time + 1e-9 < targets[target_index]:
                    continue
                image = frame.to_image()
                rotation = float(getattr(frame, 'rotation', 0) or stream.metadata.get('rotate', 0) or 0)
                if not math.isfinite(rotation):
                    raise ValueError('Video rotation must be finite')
                if crop is not None and abs(rotation / 90 - round(rotation / 90)) > 1e-6:
                    raise ValueError('Cropped video requires a right-angle display rotation')
                if rotation:
                    image = image.rotate(rotation, expand=True)
                if crop is not None:
                    x, y, width, height = crop
                    if x + width > image.width or y + height > image.height:
                        raise ValueError('Video crop exceeds its displayed frame dimensions')
                    image = image.crop((x, y, x + width, y + height))
                image = bounded_image(image, max_pixels)
                frames.append(VideoFrame(len(frames), media_time - start_s, pts, image, kind))
                while target_index < len(targets) and targets[target_index] <= media_time + 1e-9:
                    target_index += 1
                if target_index == len(targets):
                    # All requested samples exist, but we did not decode the
                    # unsampled tail. Do not label that as a complete decode.
                    ended = False
                    break
            else:
                ended = True
            if not frames:
                raise ValueError('Requested window contains no decodable video frames')
            return SampledVideo(frames, _manifest(frames, duration=duration, origin=origin, start=start_s,
                end=stop, sampling_fps=sampling_fps, max_frames=max_frames, max_pixels=max_pixels,
                source_kind='video', decoded=decoded, targets=targets, ended=ended))
    except (VideoCancelled, ValueError, TimeoutError):
        raise
    except (av.FFmpegError, OSError) as error:
        raise ValueError(f'Video could not be decoded: {error}') from error


def sample_images(images: Iterable, *, fps=1.0, timestamps=None, source_duration_s=None,
                  time_origin_s=0.0, source_indices=None, preselected=False, start_s=0.0,
                  end_s=None, sampling_fps=1.0, max_frames=64, max_pixels=589824,
                  max_duration_s=3600, cancel=None):
    """Adapt CPU frames; preselected callers supply the full source duration.

    timestamps are media-relative source seconds; time_origin_s maps those
    onto the source container clock. A caller may select tensor indices before
    converting to PIL, preventing conversion of an entire IMAGE batch.
    """
    start_s, end_s = _limits(start_s, end_s, sampling_fps, max_frames, max_pixels, max_duration_s)
    fps = finite(fps, 'fps', positive=True)
    origin = finite(time_origin_s, 'time_origin_s', minimum=-1e12)
    if source_duration_s is not None:
        source_duration_s = finite(source_duration_s, 'source_duration_s', positive=True)
    if timestamps is not None:
        timestamps = list(timestamps)
        if len(timestamps) > 1_000_000:
            raise ValueError('Too many source timestamps')
        timestamps = [finite(t, 'timestamp') for t in timestamps]
        if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
            raise ValueError('Source timestamps must be strictly increasing')
    if source_indices is not None:
        source_indices = list(source_indices)
        if len(source_indices) > 1_000_000:
            raise ValueError('Too many source indices')
        for index in source_indices:
            _integer(index, 'source index', 0, 1_000_000)
        if any(b <= a for a, b in zip(source_indices, source_indices[1:])):
            raise ValueError('Source indices must be strictly increasing')
        if timestamps is not None and len(source_indices) != len(timestamps):
            raise ValueError('Source index/timestamp counts do not match')
    if preselected and (timestamps is None or source_duration_s is None):
        raise ValueError('Preselected frames require timestamps and full source duration')
    stop = min(end_s, source_duration_s) if end_s is not None and source_duration_s is not None else end_s or source_duration_s
    if stop is None and hasattr(images, '__len__'):
        stop = len(images) / fps if timestamps is None else (timestamps[-1] + 1/fps if timestamps else 0)
    if stop is None:
        raise ValueError('Streaming images require an explicit source duration or end_s')
    if stop <= start_s or stop - start_s > max_duration_s:
        raise ValueError('Requested image window is empty or exceeds duration limit')
    targets = sampling_schedule(start_s, stop, sampling_fps, max_frames)
    result, target_index, read = [], 0, 0
    for i, image in enumerate(images):
        check_cancel(cancel)
        read += 1
        if read > (max_frames if preselected else 180000):
            raise ValueError('Image input exceeds its frame limit')
        if timestamps is not None and i >= len(timestamps):
            raise ValueError('Image/timestamp counts do not match')
        if source_indices is not None and i >= len(source_indices):
            raise ValueError('Image/source index counts do not match')
        media_time = timestamps[i] if timestamps is not None else i/fps
        if media_time < start_s or media_time >= stop:
            continue
        if not preselected and media_time + 1e-9 < targets[target_index]:
            continue
        source_index = source_indices[i] if source_indices is not None else i
        result.append(VideoFrame(len(result), media_time-start_s, origin+media_time,
                                 bounded_image(image, max_pixels), 'pts' if timestamps is not None else 'estimated', source_index))
        if preselected:
            continue
        while target_index < len(targets) and targets[target_index] <= media_time + 1e-9:
            target_index += 1
        if target_index == len(targets):
            break
    if not result:
        raise ValueError('Image window contains no sampled frames')
    if preselected and timestamps is not None and read != len(timestamps):
        raise ValueError('Image/timestamp counts do not match')
    if preselected and source_indices is not None and read != len(source_indices):
        raise ValueError('Image/source index counts do not match')
    return SampledVideo(result, _manifest(result, duration=source_duration_s or stop, origin=origin,
        start=start_s, end=stop, sampling_fps=sampling_fps, max_frames=max_frames,
        max_pixels=max_pixels, source_kind='images', decoded=read, targets=targets))


def validate_manifest(manifest, frame_count=None):
    if (not isinstance(manifest, dict) or type(manifest.get('contract_version')) is not int
            or manifest['contract_version'] != CONTRACT_VERSION):
        raise ValueError('Unsupported video manifest contract')
    result = copy.deepcopy(manifest)
    source, frames = result.get('source'), result.get('frames')
    if not isinstance(source, dict) or not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError('Video manifest needs a source and bounded frame list')
    if frame_count is not None and len(frames) != frame_count:
        raise ValueError('Manifest/image frame counts do not match')
    origin = finite(source.get('time_origin_s', 0), 'source origin', minimum=-1e12)
    duration = source.get('duration_s')
    if duration is not None:
        finite(duration, 'source duration', positive=True)
    sampling = result.get('sampling')
    if not isinstance(sampling, dict):
        raise ValueError('Video manifest needs sampling metadata')
    start = finite(sampling.get('start_s', 0), 'start_s')
    end = finite(sampling.get('end_s'), 'end_s')
    rate = finite(sampling.get('sampling_fps'), 'sampling_fps', positive=True)
    cap = _integer(sampling.get('max_frames'), 'max_frames', 1, MAX_FRAMES)
    pixels = _integer(sampling.get('max_pixels'), 'max_pixels', 1024, MAX_PIXELS)
    if rate > 120 or len(frames) > cap or cap * pixels * 3 > MAX_RETAINED_BYTES:
        raise ValueError('Video manifest exceeds its sampling budget')
    if end <= start or end-start > 3600 or (duration is not None and end > duration + 1e-6):
        raise ValueError('Video sampling window exceeds its source duration')
    previous, indices = -math.inf, set()
    for item in frames:
        if not isinstance(item, dict):
            raise ValueError('Video frame metadata must be an object')
        idx = _integer(item.get('index'), 'frame index', 0, MAX_FRAMES-1)
        width = _integer(item.get('width'), 'frame width', 1, MAX_PIXELS)
        height = _integer(item.get('height'), 'frame height', 1, MAX_PIXELS)
        if width * height > pixels:
            raise ValueError('Video manifest frame exceeds its pixel budget')
        relative = finite(item.get('timestamp_s'), 'clip timestamp')
        pts = finite(item.get('source_timestamp_s'), 'source timestamp', minimum=-1e12)
        if idx in indices or pts <= previous or abs((pts-origin-start)-relative) > 1e-5:
            raise ValueError('Video frame indices/times are inconsistent')
        if relative >= end-start + 1e-6:
            raise ValueError('Video frame lies outside its sampling window')
        indices.add(idx)
        previous = pts
    return result

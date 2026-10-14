"""Explicit video request contract; no network, model loading or GPU calls."""
from __future__ import annotations

import io
import math
import struct

from PIL import Image
from tools.strata_video import CONTRACT_VERSION, VideoCancelled, bounded_size, check_cancel, finite, rgb_image

MAX_REQUEST_FRAMES = 64
MAX_FRAME_PIXELS = 16_777_216
MAX_VIDEO_BYTES = 128 * 1024**2


def upload_input(request):
    """Validate the upload envelope without loading a native decoder."""
    import base64
    if not isinstance(request, dict) or not isinstance(request.get('data'), str):
        raise ValueError('Video upload requires base64 data')
    encoded = request['data']
    if len(encoded) > (MAX_VIDEO_BYTES + 2)//3*4:
        raise ValueError('Video upload exceeds 128 MiB')
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError('Video upload data is not valid base64') from error
    if len(data) > MAX_VIDEO_BYTES:
        raise ValueError('Video upload exceeds 128 MiB')
    mode = request.get('mode', 'frames')
    if mode not in ('frames', 'native'):
        raise ValueError('Video mode must be frames or native')
    count = request.get('max_frames', 8)
    if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 64:
        raise ValueError('Video upload max_frames must be 2–64')
    allow_empty = request.get('allow_empty_segment', False)
    if type(allow_empty) is not bool:
        raise ValueError('allow_empty_segment must be a boolean')
    options = {'start_s':request.get('start_s',0), 'end_s':request.get('end_s'),
               'sampling_fps':request.get('sampling_fps',1), 'max_frames':count,
               'mode':mode, 'allow_empty_segment':allow_empty}
    return data, options


def sample_upload(request, cancel=None):
    """Pure in-process adapter retained for local tools and contract tests."""
    data, options = upload_input(request)
    return sample_source(io.BytesIO(data), options, cancel=cancel)


def sample_upload_isolated(request, cancel=None):
    """The HTTP service owns a disposable native-media worker."""
    from serve import media_jobs
    data, options = upload_input(request)
    try:
        return media_jobs.sample_video(data, options, cancel=cancel)
    except InterruptedError as error:
        raise VideoCancelled(str(error)) from error


def sample_source(source, options, cancel=None):
    """Decode a validated local upload inside its owning process."""
    import base64
    from tools.strata_video import sample_video, VideoWindowEmpty
    mode, count = options['mode'], options['max_frames']
    allow_empty = options['allow_empty_segment']
    try:
        sampled = sample_video(source, start_s=options['start_s'],
                               end_s=options['end_s'], sampling_fps=options['sampling_fps'],
                               max_frames=count, max_pixels=589824, max_input_bytes=MAX_VIDEO_BYTES,
                               cancel=cancel)
    except VideoWindowEmpty as error:
        if not allow_empty:
            raise
        check_cancel(cancel)
        return {'part': None, 'manifest': error.manifest, 'empty_segment': True}
    frames = []
    for frame in sampled.frames:
        check_cancel(cancel)
        output = io.BytesIO()
        frame.image.save(output, format='PNG')
        check_cancel(cancel)
        frames.append({'source': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode(),
                       'timestamp_s': frame.timestamp_s, 'source_timestamp_s': frame.source_timestamp_s,
                       'index': frame.index})
    manifest = sampled.manifest
    metadata = {'duration_s': manifest['sampling']['end_s'] - manifest['sampling']['start_s'],
                'time_origin_s': manifest['source']['time_origin_s'] + manifest['sampling']['start_s'],
                'sampling_fps': manifest['sampling']['sampling_fps']}
    return {'part': video_part({'type': 'strata_video', 'contract_version': 1, 'mode': mode,
                               'frames': frames, 'metadata': metadata}), 'manifest': manifest}


def video_part(part):
    if type(part.get('contract_version')) is not int or part['contract_version'] != CONTRACT_VERSION:
        raise ValueError('strata_video requires contract_version=1')
    mode = part.get('mode', 'frames')
    if mode not in ('frames', 'native'):
        raise ValueError('strata_video mode must be frames or native')
    frames = part.get('frames')
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_REQUEST_FRAMES:
        raise ValueError('strata_video requires 1–64 sampled frames')
    metadata = part.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('strata_video metadata must be an object')
    duration = metadata.get('duration_s')
    if duration is not None:
        duration = finite(duration, 'video duration_s', positive=True)
        if duration > 3600:
            raise ValueError('Video clip exceeds the 3600-second window limit')
    if 'sampling_fps' in metadata and finite(metadata['sampling_fps'], 'video sampling_fps', positive=True) > 120:
        raise ValueError('Video sampling_fps must not exceed 120')
    origin = finite(metadata.get('time_origin_s', 0), 'video time_origin_s', minimum=-1e12)
    last, source_last, seen, total, out = -math.inf, -math.inf, set(), 0, []
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise ValueError('Video frame must be an object')
        source = frame.get('source')
        if not isinstance(source, str) or not source.startswith('data:image/') or ';base64,' not in source:
            raise ValueError('Video frames require inline base64 images')
        total += len(source)
        if total > MAX_VIDEO_BYTES:
            raise ValueError('Video frame data exceeds its byte limit')
        timestamp = finite(frame.get('timestamp_s'), 'video timestamp_s')
        pts = finite(frame.get('source_timestamp_s', origin + timestamp), 'video source_timestamp_s', minimum=-1e12)
        if abs(pts - origin - timestamp) > 1e-5:
            raise ValueError('Video source and clip timestamps do not match time_origin_s')
        number = frame.get('index', index)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0 or number in seen:
            raise ValueError('Video frame indices must be unique nonnegative integers')
        if (timestamp >= 3600 or timestamp <= last or pts <= source_last
                or (duration is not None and timestamp >= duration + 1e-6)):
            raise ValueError('Video timestamps must increase within the clip')
        seen.add(number)
        last, source_last = timestamp, pts
        out.append({'source': source, 'timestamp_s': timestamp, 'source_timestamp_s': pts, 'index': number})
    return {'type': 'strata_video', 'mode': mode, 'contract_version': CONTRACT_VERSION,
            'frames': out, 'metadata': dict(metadata)}


def has_video(messages):
    return any(isinstance(m.get('content'), list) and
               any(p.get('type') == 'strata_video' for p in m['content'] if isinstance(p, dict)) for m in messages)


def expand_videos(messages, load_image, cancel_check):
    """Plan whole clips first, producing ordered image items or temporal pairs.

    A native pair is represented by one template video placeholder. The time
    prefix precedes the pair, never separating its two source frames.
    """
    result, total_frames = [], 0
    for message in messages:
        content = message.get('content')
        if not isinstance(content, list):
            result.append(message)
            continue
        items = []
        for item in content:
            cancel_check()
            if item.get('type') != 'strata_video':
                items.append(item)
                continue
            part = video_part(item)
            total_frames += len(part['frames'])
            if total_frames > MAX_REQUEST_FRAMES:
                raise ValueError('Request exceeds 64 total video frames')
            if part['mode'] == 'frames':
                for frame in part['frames']:
                    items.extend([{'type': 'text', 'text': f"<{frame['timestamp_s']:.1f} seconds>"},
                                  {'type': 'image', 'source': frame['source']}])
                continue
            # Keep all groups on the same bounded grid. The helper applies its
            # fixed max-token smart resize; equal inputs produce equal grids.
            decoded, size = [], None
            for frame in part['frames']:
                cancel_check()
                data = load_image(frame['source'])
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        if image.width * image.height > MAX_FRAME_PIXELS:
                            raise ValueError('Video frame exceeds source pixel limit')
                        image.load()
                        if size is None:
                            size = bounded_size(image.width, image.height, 589824)
                        image = rgb_image(image).resize(size, Image.Resampling.LANCZOS)
                        stream = io.BytesIO()
                        image.save(stream, format='PNG')
                        decoded.append(stream.getvalue())
                except (OSError, Image.DecompressionBombError) as error:
                    raise ValueError(f'Video frame could not be decoded: {error}') from error
                cancel_check()
            if len(decoded) % 2:
                decoded.append(decoded[-1])
            frames = part['frames']
            pairs = []
            for i in range(0, len(decoded), 2):
                timestamp = (frames[i]['timestamp_s'] + frames[min(i+1, len(frames)-1)]['timestamp_s']) / 2
                pairs.append({'timestamp_s': timestamp, 'pair': (decoded[i], decoded[i+1])})
            # Existing pack templates already render one outer video wrapper.
            # Native preparation expands its pad into all timestamped pairs.
            items.append({'type': 'video', 'pairs': pairs})
        result.append({**message, 'content': items})
    cancel_check()
    return result


def embedding_rows(path, expected_rows):
    """Small header and exact byte length checks before building a GENIV file."""
    with path.open('rb') as stream:
        header = stream.read(20)
    if len(header) != 20:
        raise ValueError('Vision embedding header is incomplete')
    magic, rows, nx, ny, width = struct.unpack('<4sIIII', header)
    if magic != b'SVE1' or rows != expected_rows or width != 2560 or rows < 1 or nx < 1 or ny < 1:
        raise ValueError('Vision embedding header is inconsistent')
    if rows != nx * ny or path.stat().st_size != 20 + rows * width * 4:
        raise ValueError('Vision embedding grid/byte length is inconsistent')
    return rows

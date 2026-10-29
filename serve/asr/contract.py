"""Bounded, dependency-free speech evidence and subtitle contracts."""
from __future__ import annotations

import json
import math
import re

VERSION = 1
MAX_BYTES = 2 * 1024 * 1024
MAX_CUES = 10000


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    try:
        value = float(value)
    except OverflowError:
        raise ValueError(f'{name} must be a finite number') from None
    if not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    return value


def transcript(value):
    if isinstance(value, str):
        if len(value.encode('utf-8')) > MAX_BYTES:
            raise ValueError('Transcript exceeds 2 MiB')
        value = json.loads(value.lstrip('\ufeff \t\r\n'))
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
        raise ValueError('Expected a bounded timed transcript object')
    if type(value.get('contract_version')) is not int or value.get('contract_version') != VERSION or value.get('timeline') not in ('input', 'selected_window'):
        raise ValueError('Timed transcript requires contract_version=1 and an explicit timeline')
    status = value.get('status')
    if status not in ('complete', 'partial', 'no_track', 'silence', 'no_speech'):
        raise ValueError('Transcript is not a completed or explicitly partial result')
    cues = value.get('cues')
    if not isinstance(cues, list) or len(cues) > MAX_CUES:
        raise ValueError('Transcript exceeds the cue limit')
    seen, previous, clean = set(), -math.inf, []
    for cue in cues:
        if not isinstance(cue, dict):
            raise ValueError('Invalid transcript cue')
        identity, text = cue.get('id'), cue.get('text')
        if not isinstance(identity, str) or not identity or len(identity) > 100 or identity in seen:
            raise ValueError('Cue IDs must be unique nonempty strings')
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 32768:
            raise ValueError('Cue text is empty or too large')
        start, end = number(cue.get('start_s'), 'cue start'), number(cue.get('end_s'), 'cue end')
        if start < 0 or end <= start or start < previous or end > 86400:
            raise ValueError('Cue intervals must be ordered, nonempty and within one day')
        method = cue.get('timing_method', 'provided_subtitle')
        if method not in ('vad_segment', 'segment_estimate', 'provided_subtitle', 'forced_alignment'):
            raise ValueError('Unknown transcript timing method')
        seen.add(identity); previous = start
        clean.append({**cue, 'id':identity, 'text':text, 'start_s':start, 'end_s':end,
                      'timing_method':method})
    if status in ('no_track', 'silence', 'no_speech') and clean:
        raise ValueError('An empty-audio status cannot contain speech cues')
    return {**value, 'cues':clean}


def relevant(value, start_s, end_s, *, selected_start_s=0):
    """Keep complete utterances and actual overlap; never invent word timings."""
    value = transcript(value)
    start_s, end_s = number(start_s, 'window start'), number(end_s, 'window end')
    if end_s <= start_s:
        raise ValueError('Expected a nonempty speech evidence window')
    origin = number(selected_start_s, 'selected start') if value['timeline'] == 'selected_window' else 0
    found = []
    for cue in value['cues']:
        start, end = cue['start_s'] + origin, cue['end_s'] + origin
        if start < end_s and end > start_s:
            found.append({**cue, 'start_s':start, 'end_s':end,
                          'overlap_start_s':max(start, start_s), 'overlap_end_s':min(end, end_s)})
    return {'status':value['status'], 'timing_precision':value.get('timing_precision', 'segment'),
            'source':value.get('source', 'provided_transcript'), 'cues':found}


def from_confucius(value, *, origin_s=0, timeline='input'):
    origin_s = number(origin_s,'transcript origin')
    if not isinstance(value, dict) or value.get('status') not in ('completed', 'finalized', 'complete', 'truncated','requires_review'):
        raise ValueError('Confucius transcript must be completed/finalized')
    if not isinstance(value.get('text'),str) or not isinstance(value.get('segments',[]),list) or len(value.get('segments',[]))>MAX_CUES:
        raise ValueError('Invalid Confucius text or segment list')
    duration = value.get('audio_samples_16k', value.get('total_samples'))
    if duration is None:
        samples = number(value.get('audio_seconds', value.get('duration_s')), 'audio duration') * 16000
        if not math.isfinite(samples):
            raise ValueError('Invalid Confucius audio duration')
        duration = round(samples)
    if type(duration) is not int or duration < 0 or duration > 3600 * 16000:
        raise ValueError('Invalid Confucius audio duration')
    cues, full, seen = [], [], set()
    for i, segment in enumerate(value.get('segments', [])):
        if not isinstance(segment, dict) or 'text' not in segment:
            raise ValueError('Confucius result lacks timed segment text; enable subtitle_timings')
        text = segment['text']
        if not isinstance(text, str):
            raise ValueError('Invalid segment text')
        full.append(text)
        start = segment.get('speech_start_sample', segment.get('start_sample'))
        end = segment.get('speech_end_sample', segment.get('end_sample'))
        owner_start,owner_end = segment.get('start_sample'),segment.get('end_sample')
        if (any(type(sample) is not int for sample in (start,end,owner_start,owner_end)) or
                not 0 <= owner_start <= start < end <= owner_end <= duration):
            raise ValueError('Invalid Confucius sample interval')
        identity = str(segment.get('segment_id', i))
        if identity in seen:
            raise ValueError('Duplicate Confucius segment ID')
        seen.add(identity)
        if text.strip():
            cues.append({'id':'speech-'+identity, 'start_s':origin_s+start/16000,
                         'end_s':origin_s+end/16000, 'text':text.strip(), 'speaker':None,
                         'language':segment.get('language', value.get('language', '')),
                         'timing_method':segment.get('timing_method', 'segment_estimate'),
                         'requires_review':bool(value.get('status') == 'requires_review' or segment.get('requires_review') or segment.get('end_reason') == 'duration_limit'
                                                or segment.get('timing_method', 'segment_estimate') == 'segment_estimate')})
    normalized = lambda text: re.sub(r'\s', '', text)
    if normalized(''.join(full)) != normalized(value.get('text', '')):
        raise ValueError('Timed segments do not cover the transcript text; re-run with subtitle_timings')
    partial = value.get('truncated') is True or value.get('status') == 'truncated'
    return transcript({'contract_version':VERSION, 'timeline':timeline, 'status':'partial' if partial else 'complete' if cues else 'no_speech',
                       'timing_precision':'segment', 'source':'confucius', 'cues':cues})


def subtitle_text(value, *, offset_s=0, timeline='input'):
    """Read provided SRT/VTT timestamps; no whole-audio/averaged-time fallback."""
    if not isinstance(value, str) or len(value.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Subtitle exceeds 2 MiB')
    stamp = r'(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}'
    def seconds(text):
        parts = text.replace(',', '.').split(':')
        if float(parts[-1]) >= 60 or int(parts[-2]) >= 60:
            raise ValueError('Subtitle minutes and seconds must be below 60')
        return sum(float(part) * 60**i for i, part in enumerate(reversed(parts)))
    cues = []
    for block in re.split(r'\n\s*\n', value.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff').strip()):
        lines = block.splitlines()
        timing = next((i for i,line in enumerate(lines) if '-->' in line), None)
        if timing is None:
            if block.startswith(('WEBVTT', 'NOTE', 'STYLE', 'REGION')):
                continue
            if block.strip():
                raise ValueError('Subtitle block lacks an explicit time range')
            continue
        match = re.fullmatch(rf'\s*({stamp})\s*-->\s*({stamp})(?:\s+[^\n]*)?', lines[timing])
        if not match:
            raise ValueError('Invalid SRT/VTT timestamp')
        text = '\n'.join(lines[timing+1:]).strip()
        if text:
            start, end = seconds(match[1])+offset_s, seconds(match[3])+offset_s
            if end <= 0:
                continue
            cues.append({'id':f'subtitle-{len(cues)}', 'start_s':max(0,start), 'end_s':end,
                         'text':text, 'timing_method':'provided_subtitle', 'speaker':None})
    return transcript({'contract_version':VERSION, 'timeline':timeline, 'status':'complete' if cues else 'no_speech',
                       'source':'provided_subtitle', 'timing_precision':'provided', 'cues':cues})

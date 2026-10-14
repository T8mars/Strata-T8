"""Dependency-free, validated subtitle construction shared by nodes and CLI.

Segment timings are estimates, not word alignment. Never subdivide their
text across invented time spans. Wrapping only changes display, not timing.
"""

from __future__ import annotations

import html
import math
import re
import unicodedata

SAMPLE_RATE = 16000
FINAL_STATUSES = {"complete", "finalized", "requires_review", "truncated"}


def _integer(value, name, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _text(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in value):
        raise ValueError(f"{name} contains unsupported control characters")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError(f"{name} contains an unpaired Unicode surrogate")
    return " ".join(value.split())


def _duration_samples(result):
    if "audio_samples_16k" in result:
        return _integer(result["audio_samples_16k"], "audio_samples_16k")
    seconds = result.get("audio_seconds")
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0):
        raise ValueError("Transcript needs a finite audio duration or audio_samples_16k")
    if type(seconds) is int:
        return seconds * SAMPLE_RATE
    if not math.isfinite(seconds) or not math.isfinite(seconds * SAMPLE_RATE):
        raise ValueError("audio_seconds cannot be represented as a finite sample duration")
    return round(seconds * SAMPLE_RATE)


def sample_milliseconds(samples):
    # Preserve Python round's ties-to-even policy without a float conversion.
    quotient, remainder = divmod(samples * 1000, SAMPLE_RATE)
    return quotient + int(remainder * 2 > SAMPLE_RATE
                          or remainder * 2 == SAMPLE_RATE and quotient % 2 == 1)


def _clusters(text):
    """Keep common combining sequences and joined emoji intact when wrapping."""
    clusters = []
    for char in text:
        attached = (unicodedata.combining(char) or char in "\ufe0e\ufe0f\u200d"
                    or 0x1F3FB <= ord(char) <= 0x1F3FF
                    or clusters and clusters[-1].endswith("\u200d"))
        if attached and clusters:
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def _width(text):
    return sum(0 if unicodedata.combining(char) or char in "\ufe0e\ufe0f\u200d"
               else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
               for char in text)


def wrap_text(text, chinese_chars=16, english_chars=42):
    """Wrap CJK at display boundaries and keep alphabetic words intact."""
    limit = chinese_chars * 2 if any(unicodedata.east_asian_width(c) in ("W", "F")
                                    for c in text) else english_chars
    units = []
    word = ""
    for cluster in _clusters(text):
        wide = unicodedata.east_asian_width(cluster[0]) in ("W", "F")
        if not wide and not cluster.isspace():
            word += cluster
        else:
            if word:
                units.append(word)
                word = ""
            units.append(cluster)
    if word:
        units.append(word)
    lines, line = [], ""
    closers = "，。！？；：、,.!?;:)]}）》」』”’"
    for unit in units:
        if line and _width(line + unit) > limit and unit[0] not in closers:
            lines.append(line.rstrip())
            line = unit.lstrip()
        else:
            line += unit
    if line.strip():
        lines.append(line.strip())
    return lines


def build_subtitles(result, *, offset_ms=0, chinese_chars=16, english_chars=42,
                    max_lines=2, max_cue_seconds=6.0, allow_partial=False,
                    whole_audio_draft=False):
    if not isinstance(result, dict):
        raise ValueError("Transcript JSON must be an object")
    if not isinstance(result.get("status"), str) or result["status"] not in FINAL_STATUSES:
        raise ValueError("Only completed or finalized transcripts can become subtitles")
    for value, name in ((allow_partial, "allow_partial"), (whole_audio_draft, "whole_audio_draft")):
        if type(value) is not bool:
            raise ValueError(f"{name} must be boolean")
    _integer(offset_ms, "offset_ms", minimum=-86400000)
    if offset_ms > 86400000:
        raise ValueError("offset_ms exceeds one day")
    _integer(chinese_chars, "chinese_chars", minimum=1)
    _integer(english_chars, "english_chars", minimum=1)
    _integer(max_lines, "max_lines", minimum=1)
    if (isinstance(max_cue_seconds, bool) or not isinstance(max_cue_seconds, (int, float))
            or not 0.1 <= max_cue_seconds <= 120 or not math.isfinite(max_cue_seconds)):
        raise ValueError("max_cue_seconds must be finite and between 0.1 and 120")
    full_text = _text(result.get("text", ""), "text")
    segments = result.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("segments must be a list")
    if "truncated" in result and type(result["truncated"]) is not bool:
        raise ValueError("truncated must be boolean")
    for index, segment in enumerate(segments):
        if isinstance(segment, dict) and "truncated" in segment and type(segment["truncated"]) is not bool:
            raise ValueError(f"segments[{index}].truncated must be boolean")
    forced_boundaries = _integer(result.get("forced_boundaries", 0), "forced_boundaries")
    partial = (result.get("truncated") is True or result.get("status") == "truncated"
               or any(isinstance(s, dict) and s.get("truncated") is True for s in segments))
    if partial and not allow_partial:
        raise ValueError("Transcript is truncated; explicitly enable allow_partial to export it")
    total = _duration_samples(result)
    spans = []
    previous_end = 0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"segments[{index}] must be an object")
        if "text" not in segment:
            raise ValueError("Segment text is missing. Re-run transcription with subtitle_timings enabled")
        text = _text(segment["text"], f"segments[{index}].text")
        start = _integer(segment.get("start_sample"), f"segments[{index}].start_sample")
        end = _integer(segment.get("end_sample"), f"segments[{index}].end_sample")
        if start >= end or start < previous_end or end > total:
            raise ValueError(f"segments[{index}] has reversed, zero-length, overlapping or out-of-range timing")
        previous_end = end
        if not text:
            continue
        speech_start = _integer(segment.get("speech_start_sample", start), "speech_start_sample")
        speech_end = _integer(segment.get("speech_end_sample", end), "speech_end_sample")
        if not start <= speech_start < speech_end <= end:
            raise ValueError(f"segments[{index}] has invalid speech timing")
        spans.append((speech_start, speech_end, text, segment.get("segment_id", index),
                      segment.get("timing_method", "segment_estimate")))
    if segments:
        mapped_text = "".join(_text(s["text"], "segment text") for s in segments)
        if re.sub(r"\s", "", mapped_text) != re.sub(r"\s", "", full_text):
            raise ValueError("Segment text does not cover the transcript. Re-run transcription; refusing to lose words")
    elif full_text:
        if not whole_audio_draft:
            raise ValueError("Transcript has no segment timings. Enable subtitle_timings and re-run, "
                             "or explicitly select whole_audio_draft")
        if total <= 0:
            raise ValueError("Text has no positive audio duration")
        spans.append((0, total, full_text, 0, "whole_audio_estimate"))
    cues, warnings = [], set()
    clipped, dropped = 0, 0
    for start, end, text, segment_id, method in spans:
        first = sample_milliseconds(start)
        last = sample_milliseconds(end)
        if first >= last:
            raise ValueError("Subtitle interval collapses after millisecond rounding")
        first += offset_ms
        last += offset_ms
        if last <= 0:
            dropped += 1
            continue
        if first < 0:
            first = 0
            clipped += 1
        lines = wrap_text(text, chinese_chars, english_chars)
        cue_warnings = []
        if method != "forced_alignment" and method != "manual":
            cue_warnings.append("approximate_segment_timing")
        if last - first > max_cue_seconds * 1000:
            cue_warnings.append("long_cue_needs_alignment_or_editing")
        if len(lines) > max_lines or any(_width(line) > (chinese_chars * 2 if any(
                unicodedata.east_asian_width(c) in ("W", "F") for c in text) else english_chars) for line in lines):
            cue_warnings.append("layout_needs_review")
        if partial:
            cue_warnings.append("partial_transcript")
        if forced_boundaries:
            cue_warnings.append("forced_boundary_needs_review")
        if cues and first < cues[-1]["end_ms"]:
            raise ValueError("Subtitle intervals overlap after millisecond rounding")
        cues.append({"id": f"segment-{segment_id}", "start_ms": first, "end_ms": last,
                     "text": text, "lines": lines, "source_segment_ids": [segment_id],
                     "timing_method": method, "needs_review": bool(cue_warnings),
                     "warnings": cue_warnings})
        warnings.update(cue_warnings)
    if clipped or dropped:
        warnings.add("offset_clipped_or_dropped_cues")
    status = "empty" if not cues else "partial" if partial else "requires_review" if warnings else "complete"
    return {"subtitle_schema_version": 1, "subtitle_status": status,
            "asr_status": result["status"], "cue_count": len(cues), "cues": cues,
            "offset_applied_ms": offset_ms, "clipped_cues": clipped, "dropped_cues": dropped,
            "warnings": sorted(warnings), "partial": partial}


def cue_timestamp(milliseconds, kind):
    _integer(milliseconds, "cue milliseconds")
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{',' if kind == 'srt' else '.'}{millis:03d}"


def render_subtitle(document, kind="srt"):
    if kind not in ("srt", "vtt"):
        raise ValueError("Subtitle format must be srt or vtt")
    cues = document.get("cues", [])
    if not cues:
        return ""
    blocks = []
    for index, cue in enumerate(cues, 1):
        first, last = cue["start_ms"], cue["end_ms"]
        if first >= last:
            raise ValueError("Subtitle cue start must be before end")
        # WebVTT defines character references; SubRip readers such as FFmpeg
        # display those references literally, including ordinary ampersands.
        text = "\n".join(html.escape(line, quote=False) if kind == "vtt" else line
                         for line in cue["lines"])
        body = f"{cue_timestamp(first, kind)} --> {cue_timestamp(last, kind)}\n{text}"
        blocks.append(f"{index}\n{body}" if kind == "srt" else body)
    return ("WEBVTT\n\n" if kind == "vtt" else "") + "\n\n".join(blocks) + "\n\n"

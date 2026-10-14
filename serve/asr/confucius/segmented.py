"""Speech-boundary segmentation for offline and append-only Q8 decoding."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .subtitles import sample_milliseconds

from .native import NativeQ8Engine
from .streaming import StreamingSession
from .vad import FireRedOnnxVAD


_SPACE_SEPARATED_LANGUAGES = frozenset({
    "English", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian",
})
_CLOSING_PUNCTUATION = frozenset(".,!?;:%)]}，。！？；：、）》」』")
_OPENING_OR_JOINING = frozenset("([{“‘「『-/'")


def join_segment_text(prefix: str, suffix: str, language: str | None) -> str:
    """Join independently decoded speech segments without merging words."""
    if not prefix or not suffix:
        return prefix + suffix
    # Auto language can arrive after the first text event. A Latin-leading
    # segment needs the same provisional separator as an eventual English tag.
    spaced = language in _SPACE_SEPARATED_LANGUAGES or (
        not language and suffix[0].isascii() and suffix[0].isalnum())
    if (not spaced or
            prefix[-1].isspace() or suffix[0].isspace() or
            prefix[-1] in _OPENING_OR_JOINING or suffix[0] in _CLOSING_PUNCTUATION):
        return prefix + suffix
    return prefix + " " + suffix


def exact_zero_boundaries(pcm: np.ndarray, *, minimum_zero: int = 2400,
                          minimum_speech: int = 8000) -> list[int]:
    """Return safe cuts after interior digital-zero runs in 16 kHz PCM.

    This handles edited multi-utterance files; nonzero room noise is never
    treated as silence by this exact-value shortcut.
    """
    value = np.asarray(pcm)
    if value.ndim != 1:
        raise ValueError("Expected mono PCM")
    zeros = np.asarray(value == 0, dtype=np.int8)
    changes = np.diff(np.pad(zeros, (1, 1)))
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    boundaries = []
    segment_start = 0
    for start, end in zip(starts, ends):
        if (end - start >= minimum_zero and start - segment_start >= minimum_speech and
                len(value) - end >= minimum_speech):
            boundaries.append(int(end))
            segment_start = int(end)
    return boundaries


@dataclass
class OfflineSegment:
    """Collect one bounded segment and recognize it once at its final boundary."""

    engine: NativeQ8Engine
    language: str | None = None
    context: str = ""
    max_segment_seconds: float = 30.0
    _chunks: list[np.ndarray] = field(default_factory=list)
    _samples: int = 0
    _final_event: dict | None = None

    def feed(self, pcm: np.ndarray) -> list[dict]:
        if self._final_event is not None:
            raise RuntimeError("Offline segment already finalized")
        value = np.asarray(pcm, dtype=np.float32)
        if value.ndim != 1 or not np.isfinite(value).all():
            raise ValueError("Expected finite mono PCM")
        if self._samples + len(value) > self.max_segment_seconds * 16000:
            raise ValueError("CONTEXT_LIMIT: maximum offline segment duration reached")
        if value.size:
            self._chunks.append(value)
            self._samples += len(value)
        return []

    def finish(self) -> dict:
        if self._final_event is not None:
            return {**self._final_event, "delta": "", "idempotent": True}
        result = (self.engine.transcribe(np.concatenate(self._chunks),
                                        context=self.context, language=self.language)
                  if self._samples else
                  {"text": "", "language": "", "finish_reason": "stop", "truncated": False})
        text = result["text"].strip()
        self._final_event = {**result, "stable_text": text, "preview_text": text,
                             "delta": text, "audio_end_sample": self._samples, "final": True}
        self._chunks.clear()
        return dict(self._final_event)


@dataclass
class PresetBoundaryVAD:
    """Present precomputed digital-zero cuts to SegmentedStream."""

    boundaries: list[int]
    seen: int = 0
    next_index: int = 0

    def feed(self, pcm: np.ndarray) -> list[dict]:
        self.seen += len(pcm)
        events = []
        while self.next_index < len(self.boundaries) and self.boundaries[self.next_index] <= self.seen:
            events.append({"sample": self.boundaries[self.next_index], "reason": "silence"})
            self.next_index += 1
        return events


@dataclass
class SegmentedStream:
    engine: NativeQ8Engine
    language: str | None = None
    context: str = ""
    chunk_ms: int = 160
    segment_seconds: int = 20
    min_segment_seconds: int = 0
    offline: bool = False
    vad: FireRedOnnxVAD = field(default_factory=FireRedOnnxVAD)
    segment_id: int = 0
    segment_start: int = 0
    total_samples: int = 0
    completed_text: str = ""
    stable_text: str = ""
    preview_text: str = ""
    detected_language: str = ""
    finalized: bool = False
    truncated: bool = False
    forced_boundaries: int = 0
    segments: list[dict] = field(default_factory=list)
    _segment: StreamingSession | OfflineSegment = field(init=False)
    _leading_zero_samples: int = 0
    _has_nonzero: bool = False
    _pending_silence: bool = False
    _segment_joiner: str | None = None
    _first_nonzero_sample: int | None = None
    _last_nonzero_sample: int | None = None
    _segment_samples: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.min_segment_seconds <= self.segment_seconds:
            raise ValueError("min_segment_seconds must be within the segment duration")
        self._new_segment()

    def _new_segment(self) -> None:
        self._segment = (OfflineSegment(self.engine, language=self.language, context=self.context)
                         if self.offline else
                         StreamingSession(self.engine, language=self.language, context=self.context,
                                          chunk_ms=self.chunk_ms, max_segment_seconds=30))
        self._leading_zero_samples = 0
        self._has_nonzero = False
        self._segment_joiner = None
        self._first_nonzero_sample = None
        self._last_nonzero_sample = None
        self._segment_samples = 0

    def _decorate(self, local: dict, *, segment_final: bool = False, end_reason: str = "") -> dict:
        language = local.get("language") or self.detected_language or self.language
        local_stable = local.get("stable_text", "")
        if local_stable and self._segment_joiner is None:
            joined = join_segment_text(self.completed_text, local_stable, language)
            self._segment_joiner = joined[len(self.completed_text):len(joined) - len(local_stable)]
        global_stable = self.completed_text + (self._segment_joiner or "") + local_stable
        if not local_stable:
            global_stable = self.completed_text
        if not global_stable.startswith(self.stable_text):
            raise RuntimeError("STABILITY_VIOLATION across VAD segment boundary")
        delta = global_stable[len(self.stable_text):]
        self.stable_text = global_stable
        local_preview = local.get("preview_text", "")
        self.preview_text = (self.completed_text + self._segment_joiner + local_preview
                             if self._segment_joiner is not None and local_preview else
                             join_segment_text(self.completed_text, local_preview, language))
        self.detected_language = local.get("language") or self.detected_language
        return {**local, "segment_id": self.segment_id,
                "audio_end_sample": self.segment_start + self._leading_zero_samples + local.get("audio_end_sample", 0),
                "stable_text": global_stable, "preview_text": self.preview_text,
                "delta": delta, "language": self.detected_language,
                "segment_final": segment_final, "end_reason": end_reason,
                "final": False}

    def _feed_part(self, part: np.ndarray) -> list[dict]:
        if not part.size:
            return []
        nonzero = np.flatnonzero(part)
        if nonzero.size:
            if self._first_nonzero_sample is None:
                self._first_nonzero_sample = self.segment_start + self._segment_samples + int(nonzero[0])
            self._last_nonzero_sample = self.segment_start + self._segment_samples + int(nonzero[-1]) + 1
        self._segment_samples += len(part)
        # Exact digital zero contains no speech. Skipping only leading zero
        # samples avoids repeated full-audio ASR passes on long silent files.
        # Nonzero low-level audio is never discarded by this shortcut.
        if not self._has_nonzero and not np.any(part):
            self._leading_zero_samples += len(part)
            return []
        self._has_nonzero = True
        return [self._decorate(event) for event in self._segment.feed(part)]

    def _end_segment(self, boundary: int, reason: str) -> dict:
        if reason == "duration_limit" and not self._has_nonzero:
            reason = "silence_rollover"
        local = self._segment.finish()
        self.truncated = self.truncated or bool(local.get("truncated", False))
        event = self._decorate(local, segment_final=True, end_reason=reason)
        self.completed_text = self.stable_text
        if reason in ("forced", "duration_limit"):
            self.forced_boundaries += 1
        if reason == "duration_limit":
            reset_duration = getattr(self.vad, "reset_speech_duration", None)
            if reset_duration is not None:
                reset_duration()
        text = local.get("stable_text", "").strip()
        bounds = getattr(self.vad, "speech_bounds", lambda start, end: None)(self.segment_start, boundary)
        acoustic_bounds = bool(bounds)
        if not bounds and self._first_nonzero_sample is not None:
            first, last = self._first_nonzero_sample, self._last_nonzero_sample
            # A pulse can have a positive sample span but no representable
            # millisecond interval. Keep decoder ownership as the estimate.
            if sample_milliseconds(first) < sample_milliseconds(last):
                bounds = (first, last)
        timing = {"timing_method": "vad_segment" if acoustic_bounds else "segment_estimate"}
        if bounds and bounds[0] < bounds[1]:
            timing.update({"speech_start_sample": bounds[0], "speech_end_sample": bounds[1]})
        self.segments.append({"segment_id": self.segment_id, "start_sample": self.segment_start,
                              "end_sample": boundary, "end_reason": reason,
                              "skipped_zero_samples": self._leading_zero_samples,
                              "finish_reason": local.get("finish_reason"),
                              "truncated": local.get("truncated", False),
                              "text": text, "language": local.get("language") or self.language or "",
                              **timing})
        self.segment_id += 1
        self.segment_start = boundary
        self._pending_silence = False
        self._new_segment()
        return event

    def feed(self, pcm: np.ndarray) -> list[dict]:
        if self.finalized:
            raise RuntimeError("Segmented stream already finalized")
        chunk = np.asarray(pcm, dtype=np.float32)
        if chunk.ndim != 1 or not np.isfinite(chunk).all():
            raise ValueError("Expected finite mono PCM")
        if not chunk.size:
            return []
        start = self.total_samples
        end = start + len(chunk)
        boundaries = self.vad.feed(chunk)
        self.total_samples = end
        events: list[dict] = []
        cursor = start
        for boundary in boundaries:
            stop = min(end, max(cursor, int(boundary["sample"])))
            if (boundary["reason"] == "silence" and
                    stop - self.segment_start < self.min_segment_seconds * 16000):
                self._pending_silence = True
                continue
            while cursor < stop:
                limit = self.segment_start + self.segment_seconds * 16000
                portion_end = min(stop, limit)
                events.extend(self._feed_part(chunk[cursor - start:portion_end - start]))
                cursor = portion_end
                if cursor == limit:
                    events.append(self._end_segment(cursor, "duration_limit"))
            if cursor > self.segment_start:
                events.append(self._end_segment(cursor, boundary["reason"]))
        if self._pending_silence:
            if getattr(self.vad, "active", False):
                # New speech superseded the early silence. Its next acoustic
                # boundary will decide where the current segment ends.
                self._pending_silence = False
            elif (not getattr(self.vad, "speech_candidate", 0) and
                  end >= self.segment_start + self.min_segment_seconds * 16000):
                stop = max(cursor, self.segment_start + self.min_segment_seconds * 16000)
                while cursor < stop:
                    limit = self.segment_start + self.segment_seconds * 16000
                    portion_end = min(stop, limit)
                    events.extend(self._feed_part(chunk[cursor - start:portion_end - start]))
                    cursor = portion_end
                    if cursor == limit:
                        events.append(self._end_segment(cursor, "duration_limit"))
                if self._pending_silence and cursor > self.segment_start:
                    events.append(self._end_segment(cursor, "silence"))
        while cursor < end:
            limit = self.segment_start + self.segment_seconds * 16000
            portion_end = min(end, limit)
            events.extend(self._feed_part(chunk[cursor - start:portion_end - start]))
            cursor = portion_end
            if cursor == limit:
                events.append(self._end_segment(cursor, "duration_limit"))
        return events

    def finish(self) -> dict:
        if self.finalized:
            return {"final": True, "stable_text": self.stable_text, "preview_text": self.preview_text,
                    "audio_end_sample": self.total_samples, "idempotent": True,
                    "truncated": self.truncated,
                    "finish_reason": "length" if self.truncated else "stop",
                    "forced_boundaries": self.forced_boundaries, "segments": self.segments}
        if self.total_samples > self.segment_start:
            event = self._end_segment(self.total_samples, "input_end")
        else:
            event = {"audio_end_sample": self.total_samples, "stable_text": self.stable_text,
                     "preview_text": self.preview_text, "delta": "", "language": self.detected_language,
                     "segment_id": self.segment_id, "segment_final": False}
        self.finalized = True
        return {**event, "final": True, "truncated": self.truncated,
                "finish_reason": "length" if self.truncated else event.get("finish_reason"),
                "forced_boundaries": self.forced_boundaries,
                "segments": self.segments}

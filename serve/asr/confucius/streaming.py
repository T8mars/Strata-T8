"""Append-only streaming controller for the official Q8 native backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np

from .native import NativeQ8Engine, parse_raw


class StabilityViolation(RuntimeError):
    """Raised when a would-be committed prefix would revise published text."""


@dataclass
class StreamingSession:
    engine: NativeQ8Engine
    language: str | None = None
    context: str = ""
    chunk_ms: int = 160
    lookahead_ms: int = 160
    unfixed_tokens: int = 1
    max_segment_seconds: float = 30.0
    _buffer: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    _audio: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))
    _raw: str = ""
    stable_text: str = ""
    preview_text: str = ""
    detected_language: str = ""
    seq: int = 0
    finalized: bool = False
    _first: bool = True
    _final_event: dict | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not 80 <= self.chunk_ms <= 2000 or not 0 <= self.lookahead_ms <= 2000:
            raise ValueError("Invalid chunk/lookahead duration")
        if not 0 <= self.unfixed_tokens <= 16:
            raise ValueError("Invalid unfixed token count")

    def _rollback(self, raw: str) -> str:
        ids = self.engine.tokenize(raw)
        keep = max(0, len(ids) - self.unfixed_tokens)
        while keep >= 0:
            value = self.engine.detokenize(ids[:keep]) if keep else ""
            if "\ufffd" not in value:
                return value
            keep -= 1
        return ""

    def _decode(self, *, final: bool) -> dict:
        prefix = self._rollback(self._raw) if self._raw else ""
        # Never roll back through characters already published to clients.
        if self._raw and not parse_raw(prefix, self.language)[1].startswith(self.stable_text):
            prefix = self._raw
        if final:
            max_tokens = 512
        else:
            max_tokens = 4 if self._first else 2
        start = perf_counter()
        result = self.engine.generate(
            self._audio, context=self.context, language=self.language,
            prefix=prefix, max_tokens=max_tokens,
        )
        elapsed_ms = round((perf_counter() - start) * 1000, 1)
        candidate_raw = (prefix + result["text"]).split("|", 1)[0]
        detected_language, candidate = parse_raw(candidate_raw, self.language)
        provisional_none = (not final and self.language is None and not self.stable_text and
                            not self.detected_language and
                            candidate_raw.lower().startswith("language none<asr_text>"))
        if provisional_none:
            candidate = ""
            fixed = ""
        elif final:
            fixed = candidate
        else:
            _, fixed = parse_raw(self._rollback(candidate_raw), self.language)
        if not candidate.startswith(self.stable_text):
            raise StabilityViolation(
                f"Generated text revised committed prefix at seq={self.seq}; previous={self.stable_text!r}, candidate={candidate!r}"
            )
        if not final and len(fixed) < len(self.stable_text):
            fixed = self.stable_text
        if not fixed.startswith(self.stable_text):
            raise StabilityViolation(
                f"Committed prefix changed at seq={self.seq}; previous={self.stable_text!r}, candidate={fixed!r}"
            )
        delta = fixed[len(self.stable_text):]
        self.stable_text = fixed
        self.preview_text = candidate
        self.detected_language = detected_language or self.detected_language
        # Auto language can say `None` on the initial silent/lookahead chunk.
        # Treat an empty None result as provisional instead of cementing it
        # into every following assistant prefix.
        self._raw = "" if provisional_none else candidate_raw
        self.seq += 1
        self._first = provisional_none
        return {
            "seq": self.seq,
            "audio_end_sample": len(self._audio),
            "stable_text": self.stable_text,
            "delta": delta,
            "preview_text": self.preview_text,
            "language": self.detected_language,
            "finish_reason": result.get("finish_reason"),
            "infer_ms": elapsed_ms,
            "final": final,
            "truncated": final and result.get("finish_reason") == "length",
        }

    def feed(self, pcm16k: np.ndarray) -> list[dict]:
        if self.finalized:
            raise RuntimeError("Session already finalized")
        chunk = np.asarray(pcm16k, dtype=np.float32)
        if chunk.ndim != 1 or not np.isfinite(chunk).all():
            raise ValueError("Expected 1-D finite float32 PCM")
        if not chunk.size:
            return []
        if len(self._buffer) + len(self._audio) + len(chunk) > self.max_segment_seconds * 16_000:
            raise ValueError("CONTEXT_LIMIT: maximum continuous segment duration reached")
        self._buffer = np.concatenate((self._buffer, chunk))
        events = []
        while True:
            needed = (self.chunk_ms + self.lookahead_ms if self._first else self.chunk_ms) * 16
            if self._buffer.size < needed:
                break
            self._audio = np.concatenate((self._audio, self._buffer[:needed]))
            self._buffer = self._buffer[needed:]
            events.append(self._decode(final=False))
        return events

    def finish(self) -> dict:
        if self.finalized:
            if self._final_event is None:
                raise RuntimeError("Finalized session has no terminal event")
            return {**self._final_event, "delta": "", "idempotent": True}
        if self._buffer.size:
            self._audio = np.concatenate((self._audio, self._buffer))
            self._buffer = np.empty(0, dtype=np.float32)
        if not self._audio.size:
            self.finalized = True
            event = {"seq": self.seq, "stable_text": "", "delta": "", "preview_text": "",
                     "language": "", "audio_end_sample": 0, "final": True,
                     "finish_reason": "stop", "truncated": False}
            self._final_event = dict(event)
            return event
        event = self._decode(final=True)
        self.finalized = True
        self._final_event = dict(event)
        return event

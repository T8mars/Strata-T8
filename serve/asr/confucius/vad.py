"""FireRedVAD ONNX stream detector in the isolated worker, without Torch."""

from __future__ import annotations

from collections import deque
from pathlib import Path

import kaldi_native_fbank as knf
import kaldiio
import numpy as np
import onnxruntime as ort

from .native import ROOT

VAD_DIR = ROOT / "runtime/asr/vad"


class FireRedOnnxVAD:
    """Official cached Stream-VAD ONNX plus Kaldi fbank/CMVN preprocessing."""

    def __init__(self, directory: Path = VAD_DIR, *, threshold: float = 0.4,
                 min_speech_frames: int = 8, min_silence_frames: int = 20,
                 max_speech_frames: int = 2000) -> None:
        model_path = directory / "fireredvad_stream_vad_with_cache.onnx"
        cmvn_path = directory / "cmvn.ark"
        if not model_path.is_file() or not cmvn_path.is_file():
            raise FileNotFoundError(f"FireRedVAD ONNX assets missing in {directory}")
        self.model = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        stats = np.asarray(kaldiio.load_mat(str(cmvn_path)), dtype=np.float64)
        count = stats[0, -1]
        self.mean = np.asarray(stats[0, :-1] / count, dtype=np.float32)
        variance = stats[1, :-1] / count - np.square(self.mean.astype(np.float64))
        self.inv_std = np.asarray(1 / np.sqrt(np.maximum(variance, 1e-20)), dtype=np.float32)
        options = knf.FbankOptions()
        options.frame_opts.samp_freq = 16000
        options.frame_opts.frame_length_ms = 25
        options.frame_opts.frame_shift_ms = 10
        options.frame_opts.dither = 0
        options.frame_opts.snip_edges = True
        options.mel_opts.num_bins = 80
        options.mel_opts.debug_mel = False
        self.fbank = knf.OnlineFbank(options)
        self.cache = np.zeros((8, 1, 128, 19), dtype=np.float32)
        self.next_frame = 0
        self.samples_seen = 0
        self.smooth = deque(maxlen=5)
        self.threshold = threshold
        self.min_speech = min_speech_frames
        self.min_silence = min_silence_frames
        self.max_speech = max_speech_frames
        self.speech_candidate = 0
        self.silence_candidate = 0
        self.active = False
        self.speech_frames = 0
        self.last_probability = 0.0
        self.speech_start_sample = None
        self.speech_intervals = deque()

    def feed(self, pcm: np.ndarray) -> list[dict]:
        value = np.asarray(pcm, dtype=np.float32)
        if value.ndim != 1 or not np.isfinite(value).all():
            raise ValueError("VAD expects finite mono PCM")
        if not value.size:
            return []
        self.samples_seen += len(value)
        # FireRed's reference AudioFeat reads WAV samples as int16 before
        # Kaldi fbank; the ASR stream uses normalized float32 PCM.
        self.fbank.accept_waveform(16000, (value * 32768.0).tolist())
        ready = self.fbank.num_frames_ready
        if ready <= self.next_frame:
            return []
        feats = np.vstack([self.fbank.get_frame(i) for i in range(self.next_frame, ready)]).astype(np.float32)
        feats = np.ascontiguousarray((feats - self.mean) * self.inv_std, dtype=np.float32)[None]
        probs, self.cache = self.model.run(None, {"feat": feats, "caches_in": self.cache})
        events: list[dict] = []
        for offset, probability in enumerate(probs.reshape(-1)):
            frame = self.next_frame + offset + 1
            self.last_probability = float(probability)
            self.smooth.append(self.last_probability)
            speech = (sum(self.smooth) / len(self.smooth)) >= self.threshold
            if self.active:
                self.speech_frames += 1
                self.silence_candidate = 0 if speech else self.silence_candidate + 1
                if self.silence_candidate >= self.min_silence or self.speech_frames >= self.max_speech:
                    reason = "forced" if self.speech_frames >= self.max_speech else "silence"
                    speech_end = min(self.samples_seen, (frame - self.silence_candidate) * 160 + 240)
                    if self.speech_start_sample is not None and speech_end > self.speech_start_sample:
                        self.speech_intervals.append((self.speech_start_sample, speech_end))
                    self.speech_start_sample = None
                    events.append({"sample": min(self.samples_seen, frame * 160 + 240),
                                   "reason": reason, "frame": frame, "probability": self.last_probability})
                    self.active = False
                    self.speech_candidate = 0
                    self.silence_candidate = 0
                    self.speech_frames = 0
            else:
                self.speech_candidate = self.speech_candidate + 1 if speech else 0
                if self.speech_candidate >= self.min_speech:
                    self.active = True
                    self.speech_start_sample = max(0, (frame - self.speech_candidate) * 160)
                    self.speech_frames = self.speech_candidate
                    self.silence_candidate = 0
        self.next_frame = ready
        while self.speech_intervals and self.speech_intervals[0][1] < self.samples_seen - 60 * 16000:
            self.speech_intervals.popleft()
        return events

    def speech_bounds(self, start: int, end: int):
        """Approximate acoustic interval, separate from decoder ownership cuts."""
        intervals = list(self.speech_intervals)
        if self.speech_start_sample is not None:
            speech_end = min(self.samples_seen, (self.next_frame - self.silence_candidate) * 160 + 240)
            intervals.append((self.speech_start_sample, speech_end))
        overlaps = [(max(start, first), min(end, last)) for first, last in intervals
                    if first < end and last > start]
        return (min(first for first, _ in overlaps), max(last for _, last in overlaps)) if overlaps else None

    def reset_speech_duration(self) -> None:
        """Keep acoustic state but restart the hard speech-length counter.

        SegmentedStream can cut an ongoing utterance at its own duration limit.
        Without this reset, FireRedVAD's independent 20-second limit may emit
        a second forced boundary only a few frames into the next segment.
        """
        self.speech_frames = 0

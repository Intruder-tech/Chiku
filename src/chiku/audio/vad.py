"""Voice activity detection and utterance segmentation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

FRAME = 512
RATE = 16000
FRAME_MS = FRAME * 1000 // RATE  # 32


class SpeechDetector(Protocol):
    name: str

    def __call__(self, frame: np.ndarray) -> float: ...

    def reset(self) -> None: ...


class SileroVAD:
    """Silero VAD v5 via onnxruntime (no PyTorch needed)."""

    name = "silero"
    _CONTEXT = 64

    def __init__(self, model_path: Path):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self._CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._context, frame.astype(np.float32, copy=False)])[None, :]
        out, self._state = self._sess.run(
            None, {"input": x, "state": self._state, "sr": self._sr}
        )
        self._context = x[0, -self._CONTEXT:]
        return float(out[0][0])


class EnergyVAD:
    """Fallback detector when the Silero model is missing: adaptive noise floor."""

    name = "energy"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._floor_db = -60.0

    def __call__(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame * frame))) + 1e-9
        db = 20 * np.log10(rms)
        # floor drifts down fast, up slowly
        rate = 0.2 if db < self._floor_db else 0.005
        self._floor_db += rate * (db - self._floor_db)
        return float(np.clip((db - self._floor_db - 9.0) / 12.0, 0.0, 1.0))


@dataclass
class SpeechStarted:
    pass


@dataclass
class SpeechEnded:
    audio: np.ndarray

    @property
    def duration_s(self) -> float:
        return len(self.audio) / RATE


SegmentEvent = SpeechStarted | SpeechEnded


class UtteranceSegmenter:
    """Turns per-frame speech probabilities into utterances.

    Hysteresis: speech starts above `threshold`, ends after `min_silence_ms`
    below `threshold - 0.15`. A short pre-roll keeps the first syllable.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        min_silence_ms: int = 700,
        max_utterance_s: float = 30.0,
        preroll_ms: int = 320,
    ):
        self.threshold = threshold
        self.neg_threshold = max(0.05, threshold - 0.15)
        self.min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self.min_silence_frames = max(1, min_silence_ms // FRAME_MS)
        self.max_frames = int(max_utterance_s * 1000 / FRAME_MS)
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self.reset()

    def reset(self) -> None:
        self._in_speech = False
        self._candidate: list[np.ndarray] = []
        self._frames: list[np.ndarray] = []
        self._silence = 0
        self._preroll.clear()

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def process(self, frame: np.ndarray, prob: float) -> list[SegmentEvent]:
        events: list[SegmentEvent] = []
        if not self._in_speech:
            if prob >= self.threshold:
                self._candidate.append(frame)
                if len(self._candidate) >= self.min_speech_frames:
                    self._in_speech = True
                    self._frames = list(self._preroll) + self._candidate
                    self._candidate = []
                    self._silence = 0
                    events.append(SpeechStarted())
            else:
                if prob < self.neg_threshold and self._candidate:
                    # blip too short to be speech; fold it into the pre-roll
                    for f in self._candidate:
                        self._preroll.append(f)
                    self._candidate = []
                elif self._candidate:
                    self._candidate.append(frame)
                    return events
                self._preroll.append(frame)
            return events

        self._frames.append(frame)
        if prob < self.neg_threshold:
            self._silence += 1
        elif prob >= self.threshold:
            self._silence = 0
        if self._silence >= self.min_silence_frames or len(self._frames) >= self.max_frames:
            keep = len(self._frames) - max(0, self._silence - 3)  # keep ~100 ms of tail
            audio = np.concatenate(self._frames[:keep]).astype(np.float32)
            self.reset()
            events.append(SpeechEnded(audio))
        return events


class SustainedSpeech:
    """True once speech probability stays above `threshold` for `min_ms` (barge-in)."""

    def __init__(self, threshold: float, min_ms: int):
        self.threshold = threshold
        self.needed = max(1, min_ms // FRAME_MS)
        self._count = 0

    def reset(self) -> None:
        self._count = 0

    def feed(self, prob: float) -> bool:
        if prob >= self.threshold:
            self._count += 1
        else:
            self._count = max(0, self._count - 2)
        return self._count >= self.needed

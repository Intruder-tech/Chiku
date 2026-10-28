"""Offline speech recognition with faster-whisper (CTranslate2)."""

from __future__ import annotations

import logging
import os
import re
import site
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from chiku.config import STTSettings
from chiku.models import whisper_dir, whisper_present
from chiku.paths import cli_command

log = logging.getLogger(__name__)

# Phrases Whisper is known to hallucinate on silence/noise.
_HALLUCINATIONS = {
    "you", "thank you for watching", "thanks for watching", "please subscribe",
    "subtitles by the amaraorg community", "", "bye",
}


class ModelMissing(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    audio_s: float
    elapsed_s: float


class SpeechToText(Protocol):
    name: str

    def load(self) -> None: ...

    def transcribe(self, audio: np.ndarray) -> Transcript: ...


class WhisperSTT:
    def __init__(self, settings: STTSettings, models_dir: Path):
        self.settings = settings
        self.models_dir = models_dir
        self._model = None
        self.device_used = "unloaded"
        self.name = f"whisper {settings.model}"

    def load(self) -> None:
        if self._model is not None:
            return
        if not whisper_present(self.models_dir, self.settings.model):
            raise ModelMissing(
                f"Speech model '{self.settings.model}' isn't downloaded. "
                "Open Settings → AI model → Manage models, or run "
                f"{cli_command()} --download-models."
            )
        from faster_whisper import WhisperModel

        path = str(whisper_dir(self.models_dir, self.settings.model))
        if self.settings.device in ("cuda", "auto"):
            try:
                _register_cuda_dlls()
                model = WhisperModel(path, device="cuda", compute_type="float16")
                # Loading succeeds even when kernels can't run; prove it with a tiny decode.
                list(model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)[0])
                self._model = model
                self.device_used = "cuda"
            except Exception as exc:  # CUDA libs missing / unsupported GPU
                log.warning("whisper on CUDA unavailable (%s); using CPU", exc)
        if self._model is None:
            self._model = WhisperModel(
                path, device="cpu", compute_type=self.settings.compute_type,
                cpu_threads=self.settings.cpu_threads,
            )
            self.device_used = "cpu"
        self.name = f"whisper {self.settings.model} ({self.device_used})"

    def transcribe(self, audio: np.ndarray) -> Transcript:
        self.load()
        assert self._model is not None
        t0 = time.perf_counter()
        segments, _info = self._model.transcribe(
            audio.astype(np.float32, copy=False),
            language=self.settings.language or None,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
        )
        parts = []
        for seg in segments:
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                continue
            parts.append(seg.text.strip())
        text = " ".join(p for p in parts if p).strip()
        if _normalise(text) in _HALLUCINATIONS or re.fullmatch(r"[\[(].*[\])]", text):
            text = ""
        return Transcript(text, len(audio) / 16000, time.perf_counter() - t0)


def _register_cuda_dlls() -> bool:
    """Make pip-installed cuBLAS/cuDNN (nvidia-*-cu12 wheels) loadable. True if found."""
    if sys.platform != "win32":
        return False
    from chiku.gpu_pack import pack_dir
    from chiku.paths import default_paths

    dirs = [d for base in map(Path, site.getsitepackages())
            for d in (base / "nvidia").glob("*/bin")]
    dirs.append(pack_dir(default_paths().data_dir))  # NVIDIA speech pack (installed app)
    found = False
    for bin_dir in dirs:
        if bin_dir.is_dir() and any(bin_dir.glob("*.dll")):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            found = True
    return found


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z ]", "", text.lower()).strip()

"""Offline speech recognition with faster-whisper (CTranslate2)."""

from __future__ import annotations

import logging
import os
import re
import site
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from chiku.config import STTSettings
from chiku.language import choose as choose_language
from chiku.models import whisper_dir, whisper_present
from chiku.paths import cli_command

log = logging.getLogger(__name__)

# Phrases Whisper is known to hallucinate on silence/noise.
_HALLUCINATION_TEXT = (
    "you", "thank you for watching", "thanks for watching", "please subscribe",
    "subtitles by the amara.org community", "", "bye",
    "धन्यवाद", "सब्सक्राइब करें", "शुक्रिया",
)


class ModelMissing(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    audio_s: float
    elapsed_s: float
    language: str = "en"  # ISO 639-1 code the text was transcribed in
    language_prob: float = 1.0  # Whisper's confidence in its own language detection


class SpeechToText(Protocol):
    name: str

    def load(self) -> None: ...

    def transcribe(self, audio: np.ndarray) -> Transcript: ...


class WhisperSTT:
    def __init__(self, settings: STTSettings, models_dir: Path, prompt: str | None = None):
        self.settings = settings
        self.models_dir = models_dir
        self.prompt = prompt  # Whisper's initial_prompt: biases spelling of expected words
        self._model = None
        self.device_used = "unloaded"
        self.name = f"whisper {settings.model}"

    def load(self) -> None:
        # One model load at a time, process-wide: the startup/reload thread and the first voice
        # turn may both call this.
        with _LOAD_LOCK:
            self._load_locked()

    def _load_locked(self) -> None:
        global _CUDA_FAILED
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
        if self.settings.device in ("cuda", "auto") and not _CUDA_FAILED:
            reason = _cuda_unavailable_reason()
            if reason:
                # Don't even try: a failed CUDA initialisation leaves CTranslate2 in a state
                # where the next attempt in the same process hangs forever.
                _CUDA_FAILED = True
                log.warning("whisper on CUDA unavailable (%s); using CPU", reason)
            else:
                try:
                    model = WhisperModel(path, device="cuda", compute_type="float16")
                    # Loading succeeds even when kernels can't run; prove it with a tiny decode.
                    list(model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)[0])
                    self._model = model
                    self.device_used = "cuda"
                except Exception as exc:  # unsupported GPU / driver problem
                    _CUDA_FAILED = True  # never retry in this process (see above)
                    log.warning("whisper on CUDA unavailable (%s); using CPU", exc)
        if self._model is None:
            self._model = WhisperModel(
                path, device="cpu", compute_type=self.settings.compute_type,
                cpu_threads=self.settings.cpu_threads,
            )
            self.device_used = "cpu"
        self.name = f"whisper {self.settings.model} ({self.device_used})"

    @property
    def multilingual(self) -> bool:
        return not self.settings.model.endswith(".en")

    def transcribe(self, audio: np.ndarray) -> Transcript:
        self.load()
        assert self._model is not None
        t0 = time.perf_counter()
        audio = audio.astype(np.float32, copy=False)
        lang = self.settings.language if self.multilingual else "en"
        segments, info = self._decode(audio, None if lang == "auto" else lang)
        language, prob = info.language, info.language_probability
        if lang == "auto":
            # Detection happens inside transcribe() before any text is decoded, so switching
            # language here costs one more encoder pass, not a second full transcription.
            chosen = choose_language(language, prob, info.all_language_probs,
                                     self.settings.preferred_languages)
            if chosen != language:
                log.info("speech detected as %s (p=%.2f); transcribing as %s",
                         language, prob, chosen)
                segments, _info = self._decode(audio, chosen)
                language = chosen
        parts = []
        for seg in segments:
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
                continue
            parts.append(seg.text.strip())
        text = " ".join(p for p in parts if p).strip()
        if _normalise(text) in _HALLUCINATIONS or re.fullmatch(r"[\[(].*[\])]", text):
            text = ""
        return Transcript(text, len(audio) / 16000, time.perf_counter() - t0,
                          language or "en", float(prob or 0.0))

    def _decode(self, audio: np.ndarray, language: str | None):  # noqa: ANN202
        assert self._model is not None
        return self._model.transcribe(
            audio,
            language=language,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
            initial_prompt=self.prompt,
        )


_LOAD_LOCK = threading.Lock()
_CUDA_FAILED = False  # set after the first CUDA failure; CUDA is never retried in-process
_CUDA_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")


def _cuda_unavailable_reason() -> str | None:
    """Why CUDA can't work here, checked safely *before* touching CTranslate2's CUDA path."""
    if sys.platform != "win32":
        return None
    _register_cuda_dlls()
    import ctypes

    for dll in _CUDA_DLLS:
        try:
            ctypes.WinDLL(dll)
        except OSError:
            return f"{dll} not found; install the NVIDIA speech pack for GPU speech recognition"
    return None


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
    # \w keeps letters of every script: Hindi text must not normalise to "" (a hallucination).
    return re.sub(r"[^\w ]|[\d_]", "", text.lower()).strip()


_HALLUCINATIONS = {_normalise(t) for t in _HALLUCINATION_TEXT}

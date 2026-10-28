"""Natural female speech with Kokoro-82M (ONNX, runs on CPU)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

from chiku.config import TTSSettings
from chiku.models import KOKORO_MODEL, KOKORO_VOICES
from chiku.paths import cli_command

log = logging.getLogger(__name__)

# Kokoro voice ids: <language letter><gender letter>_<name>; gender 'f' = female.
_LANG_BY_PREFIX = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}
DEFAULT_VOICE = "af_heart"


class TTSError(RuntimeError):
    pass


class TextToSpeech(Protocol):
    sample_rate: int
    name: str

    def load(self) -> None: ...

    def voices(self) -> list[str]: ...

    def synthesize(self, text: str, voice: str | None = None,
                   speed: float | None = None) -> np.ndarray: ...


def is_female_voice(voice_id: str) -> bool:
    return len(voice_id) > 2 and voice_id[1] == "f" and voice_id[2] == "_"


def voice_lang(voice_id: str) -> str:
    return _LANG_BY_PREFIX.get(voice_id[:1], "en-us")


class KokoroTTS:
    sample_rate = 24000

    def __init__(self, settings: TTSSettings, models_dir: Path, threads: int = 6):
        self.settings = settings
        self.models_dir = models_dir
        self.threads = threads
        self._kokoro = None
        self._lock = threading.Lock()
        self.name = f"kokoro {settings.voice}"

    def load(self) -> None:
        if self._kokoro is not None:
            return
        model = self.models_dir / KOKORO_MODEL.relpath
        voices = self.models_dir / KOKORO_VOICES.relpath
        if not model.exists() or not voices.exists():
            raise TTSError("Voice model isn't downloaded. Restart Chiku to open setup, or run "
                           f"{cli_command()} --download-models")
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        sess = ort.InferenceSession(
            str(model), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._kokoro = Kokoro.from_session(sess, str(voices))

    def voices(self) -> list[str]:
        self.load()
        assert self._kokoro is not None
        return [v for v in self._kokoro.get_voices() if is_female_voice(v)]

    def _voice(self, requested: str | None = None) -> str:
        v = requested or self.settings.voice
        if self._kokoro is not None and v not in self._kokoro.voices:
            log.warning("voice %s not available; using %s", v, DEFAULT_VOICE)
            return DEFAULT_VOICE
        return v

    def synthesize(self, text: str, voice: str | None = None,
                   speed: float | None = None) -> np.ndarray:
        """Speak `text` with the configured voice (or a one-off override for previews)."""
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)
        self.load()
        assert self._kokoro is not None
        voice = self._voice(voice)
        speed = min(2.0, max(0.5, speed if speed is not None else self.settings.speed))
        try:
            with self._lock:
                audio, _sr = self._kokoro.create(
                    text, voice=voice, speed=speed, lang=voice_lang(voice)
                )
        except Exception as exc:
            raise TTSError(f"Speech synthesis failed: {exc}") from exc
        return np.asarray(audio, dtype=np.float32)

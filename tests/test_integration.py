"""Tests against real models / services. Opt in with: pytest -m "models or ollama"."""

from __future__ import annotations

import numpy as np
import pytest

from chiku import models
from chiku.audio.capture import resample
from chiku.cancel import CancelToken
from chiku.config import Settings
from chiku.llm.base import ChatMessage
from chiku.paths import AppPaths, default_paths


@pytest.fixture
def real_paths(monkeypatch: pytest.MonkeyPatch) -> AppPaths:
    monkeypatch.delenv("CHIKU_HOME", raising=False)
    p = default_paths()
    if models.missing(p.models_dir, Settings().stt.model):
        pytest.skip("models not downloaded")
    return p


@pytest.mark.models
def test_voice_round_trip_and_vad(real_paths: AppPaths) -> None:
    from chiku.audio.vad import SileroVAD
    from chiku.stt.whisper import WhisperSTT
    from chiku.tts.kokoro import KokoroTTS, is_female_voice

    s = Settings()
    tts = KokoroTTS(s.tts, real_paths.models_dir)
    assert is_female_voice(s.tts.voice) and s.tts.voice in tts.voices()
    audio = tts.synthesize("Chiku, please open Google Chrome.")
    assert audio.dtype == np.float32 and 0.05 < float(np.abs(audio).max()) <= 1.0

    a16 = resample(audio, tts.sample_rate, 16000)
    vad = SileroVAD(real_paths.models_dir / models.SILERO_VAD.relpath)
    speech = [vad(a16[i:i + 512]) for i in range(0, len(a16) - 512, 512)]
    vad.reset()
    silence = [vad(np.zeros(512, dtype=np.float32)) for _ in range(30)]
    assert max(speech) > 0.8 and max(silence) < 0.2

    stt = WhisperSTT(s.stt, real_paths.models_dir)
    text = stt.transcribe(a16).text.lower()
    assert "open" in text and "chrome" in text


class _AudioFeed:
    """Stands in for the microphone: plays a recording into the pipeline at real-time pace."""

    def __init__(self, audio16k: np.ndarray):
        pad = np.zeros(16000, dtype=np.float32)
        x = np.concatenate([pad, audio16k, pad, pad])
        self.frames = [x[i:i + 512] for i in range(0, len(x) - 512, 512)]
        self.level = 0.0
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        import time

        time.sleep(0.032)
        return self.frames.pop(0) if self.frames else np.zeros(512, dtype=np.float32)


@pytest.mark.models
@pytest.mark.ollama
def test_full_voice_turn_with_real_models(real_paths: AppPaths) -> None:
    """Spoken question -> Silero VAD -> Whisper -> Ollama -> Kokoro, through the real engine."""
    import time

    from chiku.assistant import Assistant, Message, TurnMetrics
    from chiku.audio.vad import SileroVAD
    from chiku.bootstrap import build_registry, make_llm
    from chiku.stt.whisper import WhisperSTT
    from chiku.tools.apps import AppCatalog
    from chiku.tools.registry import ToolExecutor
    from chiku.tts.kokoro import KokoroTTS

    from .conftest import FakePlayer

    s = Settings()
    if not make_llm(s.llm).status().available:
        pytest.skip("Ollama model not available")
    tts = KokoroTTS(s.tts, real_paths.models_dir)
    question = tts.synthesize("Hey Chiku, what is two plus two?")
    feed = _AudioFeed(resample(question, tts.sample_rate, 16000))
    events: list = []
    player = FakePlayer()
    reg = build_registry(lambda: s, AppCatalog(loader=list))
    a = Assistant(
        s, make_stt=lambda st: WhisperSTT(st, real_paths.models_dir), tts=tts,
        make_llm=make_llm,
        make_vad=lambda: SileroVAD(real_paths.models_dir / models.SILERO_VAD.relpath),
        make_capture=lambda _n: feed, player=player, registry=reg,
        executor=ToolExecutor(reg), on_event=events.append,
    )
    a.start()
    a.set_mic(True)
    deadline = time.monotonic() + 120
    replies: list[Message] = []
    while time.monotonic() < deadline and not replies:
        replies = [e for e in list(events) if isinstance(e, Message)
                   and e.role == "assistant" and e.final]
        time.sleep(0.1)
    a.shutdown()
    users = [e.text.lower() for e in events if isinstance(e, Message) and e.role == "user"]
    assert users and "two plus two" in users[0].replace("2", "two")
    assert replies and ("4" in replies[0].text or "four" in replies[0].text.lower())
    assert player.played, "reply was synthesized to audio"
    metrics = [e.values for e in events if isinstance(e, TurnMetrics)]
    print("turn metrics:", metrics)


@pytest.mark.ollama
def test_real_ollama_stream() -> None:
    from chiku.llm.ollama import OllamaProvider

    p = OllamaProvider(Settings().llm)
    st = p.status()
    if not st.available:
        pytest.skip(st.message)
    text = "".join(c.text for c in p.stream_chat(
        [ChatMessage("user", "Reply with exactly: ready")], None, CancelToken()))
    assert "ready" in text.lower() and "<think>" not in text

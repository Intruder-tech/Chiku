"""Regression tests for the installed-app hang: CUDA must never be retried after a failure."""

import threading
import time
from pathlib import Path

import numpy as np
import pytest

import chiku.stt.whisper as w
from chiku.config import STTSettings


class FakeModel:
    calls: list[str] = []
    fail_cuda = True

    def __init__(self, path: str, device: str, **kw) -> None:  # noqa: ANN003
        FakeModel.calls.append(device)
        time.sleep(0.05)  # widen the race window
        self.device = device

    def transcribe(self, audio: np.ndarray, **kw):  # noqa: ANN003, ANN201
        if self.device == "cuda" and FakeModel.fail_cuda:
            raise RuntimeError("Library cublas64_12.dll is not found")
        return iter(()), None


@pytest.fixture(autouse=True)
def _fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # noqa: ANN202
    import faster_whisper

    FakeModel.calls = []
    FakeModel.fail_cuda = True
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(w, "whisper_present", lambda *_a: True)
    monkeypatch.setattr(w, "_CUDA_FAILED", False)
    return tmp_path


def make(tmp_path: Path) -> w.WhisperSTT:
    return w.WhisperSTT(STTSettings(device="auto"), tmp_path)


def test_missing_dlls_skip_cuda_entirely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(w, "_cuda_unavailable_reason", lambda: "cublas64_12.dll not found")
    s = make(tmp_path)
    s.load()
    assert s.device_used == "cpu" and FakeModel.calls == ["cpu"]  # CUDA never touched


def test_cuda_failure_is_never_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(w, "_cuda_unavailable_reason", lambda: None)
    first = make(tmp_path)
    first.load()
    assert first.device_used == "cpu" and FakeModel.calls == ["cuda", "cpu"]
    second = make(tmp_path)  # e.g. the reload after onboarding
    second.load()
    assert FakeModel.calls == ["cuda", "cpu", "cpu"]  # no second CUDA attempt


def test_concurrent_loads_are_serialised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(w, "_cuda_unavailable_reason", lambda: None)
    s = make(tmp_path)
    threads = [threading.Thread(target=s.load) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert FakeModel.calls == ["cuda", "cpu"]  # one load, not four racing ones


def test_working_cuda_is_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(w, "_cuda_unavailable_reason", lambda: None)
    FakeModel.fail_cuda = False
    s = make(tmp_path)
    s.load()
    assert s.device_used == "cuda"


def test_reload_keeps_loaded_stt(harness) -> None:  # noqa: ANN001
    from .conftest import wait_for

    a = harness.started()
    before = a._stt
    a.reload()
    assert wait_for(lambda: a._ready.is_set())
    assert a._stt is before  # same settings -> same (already loaded) instance


class _Seg:
    def __init__(self, text: str) -> None:
        self.text, self.no_speech_prob, self.avg_logprob = text, 0.0, -0.2


class _Info:
    def __init__(self, language: str, prob: float) -> None:
        self.language, self.language_probability = language, prob
        self.all_language_probs = [(language, prob), ("hi", 0.3), ("en", 0.1)]


class LangModel:
    """Detects Urdu, as Whisper often does for spoken Hindi."""

    def __init__(self) -> None:
        self.languages: list[str | None] = []

    def transcribe(self, audio: np.ndarray, language: str | None = None, **kw):  # noqa: ANN003, ANN201
        self.languages.append(language)
        detected = language or "ur"
        return iter([_Seg("नमस्ते" if detected == "hi" else "نمستے")]), _Info(detected, 0.7)


def test_auto_language_redecodes_in_users_language(tmp_path: Path) -> None:
    s = w.WhisperSTT(STTSettings(model="small", language="auto",
                                 preferred_languages=["en", "hi"]), tmp_path)
    s._model = LangModel()
    tr = s.transcribe(np.zeros(16000, dtype=np.float32))
    assert tr.text == "नमस्ते" and tr.language == "hi"
    assert s._model.languages == [None, "hi"]  # detect, then decode as Hindi


def test_english_only_model_never_detects(tmp_path: Path) -> None:
    s = w.WhisperSTT(STTSettings(model="small.en", language="auto"), tmp_path)
    s._model = LangModel()
    tr = s.transcribe(np.zeros(16000, dtype=np.float32))
    assert s._model.languages == ["en"] and tr.language == "en"

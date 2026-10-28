"""Shared fakes: every hardware/model dependency of the assistant has a test double here."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from chiku.assistant import Assistant, Event
from chiku.audio.capture import AudioInputError
from chiku.audit import AuditLog
from chiku.cancel import Cancelled, CancelToken
from chiku.config import Settings, TTSSettings
from chiku.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    ProviderStatus,
    StreamChunk,
)
from chiku.memory import MemoryStore
from chiku.stt.whisper import Transcript
from chiku.tools.apps import AppCatalog, AppEntry, FindAppTool, OpenAppTool, Snapshot
from chiku.tools.memory_tools import memory_tools
from chiku.tools.registry import ToolExecutor, ToolRegistry
from chiku.tools.research import ReadWebPageTool, SourceSession, WebSearchTool
from chiku.tools.system_info import SystemInfoTool
from chiku.tools.web import OpenUrlTool
from chiku.tools.windows_ctl import AppWindow, window_tools
from chiku.tts.kokoro import TTSError
from chiku.web.fetch import Page
from chiku.web.search import SearchResult


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIKU_HOME", str(tmp_path / "chiku-home"))


def wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class FakeSTT:
    name = "fake stt"

    def __init__(self, text: str = "hello there") -> None:
        self.text = text
        self.calls = 0

    def load(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray) -> Transcript:
        self.calls += 1
        return Transcript(self.text, len(audio) / 16000, 0.01)


class FakeTTS:
    sample_rate = 24000
    name = "fake voice"

    def __init__(self, fail: bool = False) -> None:
        self.settings = TTSSettings()
        self.spoken: list[str] = []
        self.fail = fail

    def load(self) -> None:
        pass

    def voices(self) -> list[str]:
        return ["af_heart", "bf_emma"]

    def synthesize(self, text: str, voice: str | None = None,
                   speed: float | None = None) -> np.ndarray:
        if self.fail:
            raise TTSError("voice broke")
        self.spoken.append(text)
        return np.full(2400, 0.1, dtype=np.float32)


class FakePlayer:
    def __init__(self) -> None:
        self.level = 0.0
        self.device_name: str | None = None
        self.played: list[np.ndarray] = []
        self.stops = 0
        self.playing = False
        self.hold = threading.Event()  # set() to make wait_until_drained block until cancel
        self.closed = 0

    @property
    def is_playing(self) -> bool:
        return self.playing

    def play(self, audio: np.ndarray) -> None:
        self.played.append(audio)

    def stop(self) -> None:
        self.stops += 1
        self.playing = False

    def wait_until_drained(self, cancel: CancelToken) -> bool:
        if self.hold.is_set():
            self.playing = True
            while not cancel.cancelled:
                time.sleep(0.01)
            self.playing = False
            return False
        return True

    def close_if_idle(self, idle_s: float = 20.0) -> None:
        pass

    def close(self) -> None:
        self.closed += 1


class ScriptedLLM(LLMProvider):
    """Each call to stream_chat consumes the next script (a list of chunks or an exception)."""

    is_local = True
    label = "local · fake-model (test)"

    def __init__(self, scripts: list[Any] | None = None, available: bool = True) -> None:
        self.scripts = list(scripts or [])
        self.calls: list[list[ChatMessage]] = []
        self.available = available
        self.block = threading.Event()  # set() to stall mid-stream until cancelled

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.available, "ok" if self.available else "Ollama is not running")

    def stream_chat(self, messages, tools, cancel) -> Iterator[StreamChunk]:  # noqa: ANN001
        self.calls.append(list(messages))
        script = self.scripts.pop(0) if self.scripts else [StreamChunk("Okay.", done=True)]
        if isinstance(script, Exception):
            raise script
        for chunk in script:
            if self.block.is_set():
                while not cancel.cancelled:
                    time.sleep(0.01)
            if cancel.cancelled:
                raise Cancelled(cancel.reason)
            yield chunk


class FakeVAD:
    name = "fake"

    def __call__(self, frame: np.ndarray) -> float:
        return float(frame[0])  # tests encode the speech probability in the first sample

    def reset(self) -> None:
        pass


class FakeCapture:
    """Yields scripted 512-sample frames; frame[0] carries the fake speech probability."""

    def __init__(self, probs: list[float], fail_start: int = 0, then_silence: bool = True):
        self.frames = [np.full(512, p, dtype=np.float32) for p in probs]
        self.level = 0.0
        self._active = False
        self.fail_start = fail_start
        self.then_silence = then_silence
        self.starts = 0

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        self.starts += 1
        if self.fail_start:
            self.fail_start -= 1
            raise AudioInputError("no microphone")
        self._active = True

    def stop(self) -> None:
        self._active = False

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        if self.frames:
            self.level = 0.05
            return self.frames.pop(0)
        if self.then_silence:
            time.sleep(0.005)
            return np.zeros(512, dtype=np.float32)
        time.sleep(timeout)
        return None


FAKE_APPS = [
    AppEntry("Google Chrome", "lnk", r"C:\Start\Google Chrome.lnk",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    AppEntry("Notepad", "appx", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"),
    AppEntry("Command Prompt", "lnk", r"C:\Start\Command Prompt.lnk",
             r"C:\Windows\System32\cmd.exe"),
    AppEntry("Visual Studio Code", "lnk", r"C:\Start\Visual Studio Code.lnk",
             r"C:\Code\Code.exe"),
]


class LaunchRecorder:
    def __init__(self, verified: tuple[bool, str] = (True, "process started"),
                 error: OSError | None = None) -> None:
        self.launched: list[AppEntry] = []
        self.verified = verified
        self.error = error

    def launch(self, entry: AppEntry) -> None:
        if self.error:
            raise self.error
        self.launched.append(entry)

    def verify(self, entry: AppEntry, before: Snapshot, ctx: Any) -> tuple[bool, str]:
        return self.verified


@pytest.fixture
def catalog() -> AppCatalog:
    return AppCatalog(loader=lambda: list(FAKE_APPS))


@pytest.fixture
def recorder() -> LaunchRecorder:
    return LaunchRecorder()


class StubSearch:
    name = "Stub Search"

    def __init__(self) -> None:
        self.has_key = True
        self.queries: list[str] = []
        self.results = [SearchResult("Rain expected in Mumbai", "https://weather.example/mum",
                                     "Heavy rain likely tomorrow."),
                        SearchResult("IMD forecast", "https://imd.example/", "Orange alert.")]

    def configured(self) -> bool:
        return self.has_key

    def search(self, query: str, count: int) -> list[SearchResult]:
        self.queries.append(query)
        return self.results[:count]


class StubWindows:
    def __init__(self) -> None:
        self.wins = [AppWindow(3, 20, "notes.txt - Notepad", "Notepad.exe")]
        self.focused: list[int] = []
        self.pressed: list[int] = []

    def windows(self) -> list[AppWindow]:
        return list(self.wins)

    def focus(self, hwnd: int) -> bool:
        self.focused.append(hwnd)
        return True

    def close(self, hwnd: int) -> None:
        self.wins = [w for w in self.wins if w.hwnd != hwnd]

    def exists(self, hwnd: int) -> bool:
        return any(w.hwnd == hwnd for w in self.wins)

    def press(self, vk: int) -> None:
        self.pressed.append(vk)


def make_registry(settings: Settings, catalog: AppCatalog, recorder: LaunchRecorder,
                  opened_urls: list[str] | None = None, search: StubSearch | None = None,
                  windows: StubWindows | None = None,
                  page_text: str = "Page body text.", memory: MemoryStore | None = None,
                  user_text: Callable[[], str] = lambda: "") -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(OpenAppTool(catalog, lambda: settings.permissions, launcher=recorder.launch,
                             verifier=recorder.verify, snapshot=lambda: Snapshot(set(), set())))
    reg.register(FindAppTool(catalog))
    urls = opened_urls if opened_urls is not None else []
    reg.register(OpenUrlTool(lambda: settings.permissions,
                             opener=lambda u: urls.append(u) or True))
    reg.register(SystemInfoTool())
    session = SourceSession()
    reg.register(WebSearchTool(search or StubSearch(), lambda: settings.web, session))
    reg.register(ReadWebPageTool(lambda: settings.web, session,
                                 fetcher=lambda u: Page(u, "A page", page_text, False)))
    for tool in window_tools(windows or StubWindows(), catalog, lambda: settings.permissions):
        reg.register(tool)
    if memory is not None:
        for tool in memory_tools(memory, lambda: settings.memory, user_text):
            reg.register(tool)
    return reg


class Harness:
    def __init__(self, tmp_path: Path, catalog: AppCatalog, recorder: LaunchRecorder) -> None:
        self.settings = Settings()
        self.settings.audio.min_speech_ms = 64
        self.settings.audio.min_silence_ms = 200
        self.settings.audio.barge_in_min_ms = 96
        self.stt = FakeSTT()
        self.tts = FakeTTS()
        self.llm = ScriptedLLM()
        self.player = FakePlayer()
        self.events: list[Event] = []
        self.capture: FakeCapture | None = None
        self.recorder = recorder
        self.audit = AuditLog(tmp_path / "audit.jsonl")
        self.search = StubSearch()
        self.windows = StubWindows()
        self.page_text = "Page body text."
        self.memory = MemoryStore(tmp_path / "memory.db")
        self.registry = make_registry(
            self.settings, catalog, recorder, search=self.search, windows=self.windows,
            memory=self.memory,
            user_text=lambda: self.assistant.current_user_text() if self.assistant else "")
        self.executor = ToolExecutor(self.registry, audit=self.audit,
                                     disabled=lambda: self.settings.tools.disabled)
        self.catalog = catalog
        self.assistant: Assistant | None = None

    def build(self) -> Assistant:
        def make_capture(_name: str | None) -> FakeCapture:
            assert self.capture is not None
            return self.capture

        self.assistant = Assistant(
            self.settings,
            make_stt=lambda _s: self.stt,
            tts=self.tts,
            make_llm=lambda _s: self.llm,
            make_vad=FakeVAD,
            make_capture=make_capture,
            player=self.player,
            registry=self.registry,
            executor=self.executor,
            catalog=self.catalog,
            memory=self.memory,
            on_event=self.events.append,
        )
        return self.assistant

    def started(self) -> Assistant:
        a = self.build()
        a.start()
        assert wait_for(lambda: a._ready.is_set())
        self.tts.spoken.clear()  # drop the warm-up synthesis
        return a

    def of(self, kind: type) -> list:
        return [e for e in list(self.events) if isinstance(e, kind)]


@pytest.fixture
def harness(tmp_path: Path, catalog: AppCatalog, recorder: LaunchRecorder) -> Iterator[Harness]:
    h = Harness(tmp_path, catalog, recorder)
    yield h
    if h.assistant is not None:
        h.assistant.shutdown()
    h.memory.close()


__all__ = ["LLMError", "wait_for"]

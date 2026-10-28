"""Conversation engine.

    microphone -> VAD -> utterance -> STT -> (fast command | LLM + tools) -> TTS -> speaker

Threads:
  * listener  - reads mic frames, runs VAD, detects utterances and barge-in; while the mic is
                off but the wake word is on, checks short speech segments for "Hey Chiku"
  * worker    - runs one turn at a time (STT, LLM streaming, tool calls)
  * speaker   - per turn, synthesizes sentences while the LLM is still streaming

Everything here is UI-agnostic; the UI subscribes through `on_event`.
"""

from __future__ import annotations

import itertools
import logging
import queue
import random
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

import numpy as np

from chiku.audio.capture import AudioInputError
from chiku.audio.devices import rescan
from chiku.audio.playback import AudioOutputError
from chiku.audio.vad import (
    EnergyVAD,
    SpeechDetector,
    SpeechEnded,
    SpeechStarted,
    SustainedSpeech,
    UtteranceSegmenter,
)
from chiku.cancel import Cancelled, CancelToken
from chiku.config import LLMSettings, Settings, STTSettings
from chiku.intents import parse_command, research_query
from chiku.language import guess_text, script_language, speech_language, voice_for
from chiku.language import name as language_name
from chiku.llm.base import ChatMessage, LLMError, LLMProvider, LLMUnavailable, ToolCall
from chiku.memory import MemoryStore, prompt_section
from chiku.persona import apply_pronunciations, build_system_prompt, time_prefix
from chiku.stt.whisper import SpeechToText
from chiku.text import SentenceChunker, ThinkFilter, clean_for_speech
from chiku.tools.apps import AppCatalog
from chiku.tools.base import ToolResult
from chiku.tools.registry import ToolExecutor, ToolRegistry
from chiku.tts.kokoro import TextToSpeech, TTSError
from chiku.wake import MAX_SEGMENT_S, WakeHit, chime, strip_wake

log = logging.getLogger(__name__)


# -- events -----------------------------------------------------------------------

class State(StrEnum):
    LOADING = "loading"
    IDLE = "idle"  # microphone off
    WAITING = "waiting"  # microphone off, but listening for the wake word
    LISTENING = "listening"  # microphone on, waiting for speech
    HEARING = "hearing"  # user is speaking
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class StateChanged:
    state: State
    detail: str = ""


@dataclass
class Message:
    id: int
    role: Literal["user", "assistant", "notice"]
    text: str
    final: bool
    source: str = ""
    #: Web pages the reply is based on: [{"title": ..., "url": ...}]
    sources: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ToolActivity:
    name: str
    status: Literal["running", "ok", "failed"]
    message: str


@dataclass
class ComponentStatus:
    component: str
    ok: bool
    message: str


@dataclass
class MicChanged:
    on: bool


@dataclass
class TurnMetrics:
    values: dict[str, float] = field(default_factory=dict)


@dataclass
class Woke:
    """The wake word was heard; Chiku is now listening for a request."""

    phrase: str


Event = StateChanged | Message | ToolActivity | ComponentStatus | MicChanged | TurnMetrics | Woke


class Capture(Protocol):
    level: float

    @property
    def active(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read(self, timeout: float = 0.5) -> np.ndarray | None: ...


class WakeDetector(Protocol):
    name: str

    def load(self) -> None: ...

    def check(self, audio: np.ndarray) -> WakeHit | None: ...


class Player(Protocol):
    level: float
    device_name: str | None

    @property
    def is_playing(self) -> bool: ...

    def play(self, audio: np.ndarray) -> None: ...

    def stop(self) -> None: ...

    def wait_until_drained(self, cancel: CancelToken) -> bool: ...

    def close_if_idle(self, idle_s: float = 20.0) -> None: ...

    def close(self) -> None: ...


# What Chiku says around built-in commands, per command language. Hindi is written in
# Devanagari so the Hindi voice reads it; Chiku speaks of herself in the feminine.
_PHRASES: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "open": ("Sure, opening {name} for you.", "On it, opening {name}.",
                 "Okay, launching {name}."),
        "url": ("Opening {name}.",),
        "install": ("Let me find {name} for you.",),
        "uninstall": ("Okay, looking for {name}.",),
        "update": ("Checking for an update to {name}.",),
        "updates": ("Let me check for updates.",),
        "switch": ("Switching to {name}.",),
        "close": ("Closing {name}.",),
        "done": ("Done, it's up.",),
        "running": ("It was already running, so it should be on your screen.",),
        "failed": ("Hmm, that didn't work. {message}",),
        "remembered": ("Got it, I'll remember that.", "Okay, noted.",
                       "Sure, I'll keep that in mind."),
        "forgot": ("Okay, I've forgotten that.",),
        "message": ("Okay, opening {app} to message {name}.",),
    },
    "hi": {
        "open": ("{name} खोल रही हूँ।", "अभी {name} खोलती हूँ।"),
        "url": ("{name} खोल रही हूँ।",),
        "install": ("{name} ढूँढ रही हूँ।",),
        "uninstall": ("ठीक है, {name} ढूँढ रही हूँ।",),
        "update": ("{name} का अपडेट देख रही हूँ।",),
        "updates": ("अपडेट्स देख रही हूँ।",),
        "switch": ("{name} पर ले चलती हूँ।",),
        "close": ("{name} बंद कर रही हूँ।",),
        "done": ("हो गया।",),
        "running": ("वो पहले से खुला है, आपकी स्क्रीन पर होना चाहिए।",),
        "failed": ("माफ़ कीजिए, ये नहीं हो पाया। {message}",),
        "remembered": ("ठीक है, मैं याद रखूँगी।", "ठीक है, नोट कर लिया।"),
        "forgot": ("ठीक है, मैंने भुला दिया।",),
        "message": ("{app} पर {name} को मैसेज लिख रही हूँ।",),
    },
}
_LOOKUPS = ("Let me look that up.", "One sec, I'll check online.", "Let me check the web.")
BUILTIN_SOURCE = "Chiku · built-in command (no AI model)"
_PAUSED = "paused"  # listener result: wake-word listening paused (screen locked)
FILE_TOOLS = frozenset({"find_files", "list_folder", "open_file", "read_text_file",
                        "create_folder", "move_or_rename", "move_to_recycle_bin"})


@dataclass
class _Turn:
    audio: np.ndarray | None = None
    text: str | None = None
    t_speech_end: float | None = None
    language: str | None = None  # spoken language (voice), or implied by the script (text)
    wake: bool = False  # starts with the wake phrase ("Hey Chiku, what time is it?")


# -- speech output pipeline ---------------------------------------------------------

class _Speaker:
    """Synthesizes queued sentences on a background thread and feeds the player."""

    def __init__(self, owner: Assistant, cancel: CancelToken, on_first_audio: Callable[[], None],
                 language: str | None = None):
        self._owner = owner
        self._cancel = cancel
        self._on_first_audio = on_first_audio
        self._language = language
        self._q: queue.Queue[tuple[str, str | None] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="chiku-speaker")
        self._started = False
        self.spoken: list[str] = []
        self.failed = False

    def say(self, text: str) -> None:
        speech = clean_for_speech(apply_pronunciations(text, self._owner.settings.persona))
        if not speech:
            return
        voiced, voice = self._owner._voice_for(speech, self._language)
        if not voiced:
            return  # no voice for this language: the reply stays text only
        if not self._started:
            self._thread.start()
            self._started = True
        self._q.put((speech, voice))

    def finish(self, wait_for_playback: bool = True) -> None:
        if not self._started:
            return
        self._q.put(None)
        if self._cancel.cancelled:
            # Don't wait for a sentence that is mid-synthesis: it will be discarded anyway,
            # and waiting would delay the user's next turn after a barge-in.
            return
        self._thread.join(timeout=30)
        if wait_for_playback and self.spoken and not self.failed:
            try:
                self._owner._player.wait_until_drained(self._cancel)
            except AudioOutputError as exc:
                self._owner._component("speaker", False, str(exc))

    def _run(self) -> None:
        tts, player = self._owner._tts, self._owner._player
        while True:
            item = self._q.get()
            if item is None:
                return
            text, voice = item
            if self._cancel.cancelled or self.failed:
                continue
            try:
                audio = tts.synthesize(text) if voice is None else tts.synthesize(text,
                                                                                    voice=voice)
            except TTSError as exc:
                self.failed = True
                self._owner._component("voice", False, str(exc))
                continue
            if self._cancel.cancelled:
                continue
            try:
                player.play(audio)
            except AudioOutputError as exc:
                self.failed = True
                self._owner._component("speaker", False, str(exc))
                continue
            if not self.spoken:
                self._on_first_audio()
            self.spoken.append(text)


# -- the assistant ------------------------------------------------------------------

def _new_id() -> str:
    return "call_" + uuid.uuid4().hex[:10]


def _ensure_ids(calls: list[ToolCall]) -> None:
    for c in calls:
        if not c.id:
            c.id = _new_id()


class Assistant:
    MAX_TOOL_ROUNDS = 4

    def __init__(
        self,
        settings: Settings,
        *,
        make_stt: Callable[[STTSettings], SpeechToText],
        tts: TextToSpeech,
        make_llm: Callable[[LLMSettings], LLMProvider],
        make_vad: Callable[[], SpeechDetector],
        make_capture: Callable[[str | None], Capture],
        player: Player,
        registry: ToolRegistry,
        executor: ToolExecutor,
        catalog: AppCatalog | None = None,
        memory: MemoryStore | None = None,
        on_event: Callable[[Event], None] | None = None,
        make_wake: Callable[[], WakeDetector] | None = None,
        screen_locked: Callable[[], bool] = lambda: False,
    ):
        self.settings = settings
        self._make_stt = make_stt
        self._make_llm = make_llm
        self._stt = make_stt(settings.stt)
        self._tts = tts
        self._llm = make_llm(settings.llm)
        self._make_vad = make_vad
        self._make_capture = make_capture
        self._player = player
        self._registry = registry
        self._executor = executor
        self._catalog = catalog
        self._memory = memory
        self._user_text = ""
        self._voiceless_told: set[str] = set()  # languages already told "no voice for this"
        self._language: str | None = None  # language of the conversation so far
        self._on_event = on_event or (lambda _e: None)
        executor.confirm = self._confirm
        #: Set by the UI: (tool name, description) -> approved. Default: deny.
        self.confirm_handler: Callable[[str, str], bool] = lambda _n, _d: False

        self._state = State.LOADING
        self._state_lock = threading.RLock()
        self._history: list[ChatMessage] = []
        self._turns: queue.Queue[_Turn | None] = queue.Queue()
        self._current: CancelToken | None = None
        self._turn_lock = threading.Lock()
        self._ready = threading.Event()
        self._ids = itertools.count(1)
        self._vad: SpeechDetector | None = None
        self._capture: Capture | None = None
        self._mic_on = False
        self._listener: threading.Thread | None = None
        self._listener_stop = threading.Event()
        self._listener_lock = threading.RLock()
        # Wake word: while the mic is off, the listener checks speech for "Hey Chiku".
        self._make_wake = make_wake
        self._screen_locked = screen_locked
        self._wake: WakeDetector | None = None
        self._wake_on = settings.wake.enabled  # the user's choice
        self._wake_ready = False  # the wake model loaded
        self._wake_paused = False  # screen locked
        self._wake_session = False  # mic was switched on by the wake word, times out
        self._wake_until = 0.0
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="chiku-turns")
        self._closed = False

    # -- public API -----------------------------------------------------------------
    @property
    def state(self) -> State:
        return self._state

    @property
    def llm_label(self) -> str:
        return self._llm.label

    @property
    def mic_active(self) -> bool:
        """The microphone is open for conversation (not just for the wake word)."""
        return self._mic_on and self._capturing

    @property
    def wake_listening(self) -> bool:
        """The microphone is open only to listen for the wake word."""
        return (self._wake_on and self._wake_ready and not self._mic_on
                and not self._wake_paused and self._capturing)

    @property
    def _capturing(self) -> bool:
        cap = self._capture
        return bool(cap is not None and cap.active)

    def levels(self) -> tuple[float, float]:
        """(input, output) RMS levels of streams that are actually running."""
        cap = self._capture
        inp = cap.level if cap is not None and self.mic_active else 0.0
        out = self._player.level if self._player.is_playing else 0.0
        return inp, out

    def start(self) -> None:
        self._worker.start()
        threading.Thread(target=self._load, daemon=True, name="chiku-load").start()

    def reload(self) -> None:
        """Re-run component loading (e.g. after models were downloaded in onboarding)."""
        if getattr(self._stt, "settings", None) != self.settings.stt:
            self._stt = self._make_stt(self.settings.stt)
        # Otherwise keep the same instance: if it is already loaded this is a no-op, and if
        # models were just downloaded its load() now succeeds.
        threading.Thread(target=self._load, daemon=True, name="chiku-reload").start()

    def set_mic(self, on: bool) -> None:
        self._wake_session = False  # the user decides now: no automatic switch-off
        if on == self._mic_on and (not on or self._listener_alive()):
            return
        self._mic_on = on
        self._sync_listener()
        if self._current is None and self._state is not State.LOADING:
            if on:
                self._settle_state()  # the wake-word listener already has the mic open
            else:
                self._set_state(State.WAITING if self.wake_listening else State.IDLE)
        self._emit(MicChanged(on))

    def set_wake(self, on: bool) -> None:
        """Listen (or stop listening) for the wake word while the mic is off."""
        self._wake_on = on
        if on and not self._wake_ready:
            threading.Thread(target=self._start_wake, daemon=True, name="chiku-wake-load").start()
            return
        self._sync_listener()
        if self._current is None and self._state is not State.LOADING:
            self._settle_state()

    def _start_wake(self) -> None:
        if self._load_wake():
            self._sync_listener()
            if self._current is None and self._state is not State.LOADING:
                self._settle_state()

    def _listener_alive(self) -> bool:
        return self._listener is not None and self._listener.is_alive()

    def _sync_listener(self, restart: bool = False) -> None:
        """Run the listener thread exactly when the mic or the wake word needs it."""
        with self._listener_lock:
            want = self._mic_on or (self._wake_on and self._wake_ready)
            if self._listener_alive() and (restart or not want):
                self._listener_stop.set()
                assert self._listener is not None
                self._listener.join(timeout=2)
                self._listener = None
            if want and not self._listener_alive():
                self._listener_stop.clear()
                self._listener = threading.Thread(
                    target=self._listen_loop, daemon=True, name="chiku-listener"
                )
                self._listener.start()

    def interrupt(self, reason: str = "user") -> None:
        with self._turn_lock:
            tok = self._current
        if tok is not None:
            tok.cancel(reason)
        self._player.stop()
        log.info("interrupted (%s)", reason)

    def submit_text(self, text: str) -> None:
        text = text.strip()
        if text:
            if self._current is not None:
                self.interrupt("new typed message")
            self._turns.put(_Turn(text=text[:4000]))

    def clear_history(self) -> None:
        self._history.clear()

    def set_history(self, messages: list[ChatMessage]) -> None:
        """Switch conversation: stop any reply in progress and continue from `messages`."""
        if self._current is not None:
            self.interrupt("switched conversation")
        self._history = list(messages)
        self._language = None

    def available_voices(self) -> list[str]:
        try:
            return self._tts.voices()
        except Exception:
            return []

    def preview_voice(self, voice: str, speed: float, text: str | None = None) -> None:
        """Speak a sample with a voice that isn't saved yet (settings dialog)."""
        name = self.settings.persona.name

        def run() -> None:
            try:
                audio = self._tts.synthesize(
                    text or f"Hi, I'm {name}. This is how I'll sound.", voice=voice, speed=speed
                )
                self._player.stop()
                self._player.play(audio)
            except (TTSError, AudioOutputError) as exc:
                self._component("voice", False, str(exc))

        threading.Thread(target=run, daemon=True, name="chiku-preview").start()

    def apply_settings(self, new: Settings) -> None:
        old = self.settings
        self.settings = new
        self._tts.settings = new.tts  # type: ignore[attr-defined]
        if new.audio.output_device != old.audio.output_device:
            self._player.close()
            self._player.device_name = new.audio.output_device
        if new.llm != old.llm:
            self._llm.close()
            self._llm = self._make_llm(new.llm)
            threading.Thread(target=self._check_llm, daemon=True).start()
        if new.stt != old.stt:
            self._stt = self._make_stt(new.stt)
            threading.Thread(target=self._load_stt, daemon=True).start()
        if new.audio != old.audio:
            self._sync_listener(restart=True)
        if new.wake.enabled != self._wake_on:
            self.set_wake(new.wake.enabled)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wake_on = False
        self.set_mic(False)
        self._sync_listener()  # set_mic is a no-op when only the wake word had the mic
        self.interrupt("shutdown")
        self._turns.put(None)
        self._executor.shutdown()
        self._player.close()
        self._llm.close()

    # -- internals: events & state --------------------------------------------------
    def _emit(self, ev: Event) -> None:
        try:
            self._on_event(ev)
        except Exception:
            log.exception("event handler failed")

    def _set_state(self, state: State, detail: str = "") -> None:
        with self._state_lock:
            if state is self._state and not detail:
                return
            self._state = state
        self._emit(StateChanged(state, detail))

    def _settle_state(self) -> None:
        if self._wake_session:  # a reply or a sound counts as activity: keep listening
            self._wake_until = time.monotonic() + self.settings.wake.follow_up_s
        with self._state_lock:
            if self._state is State.HEARING:
                return
            if self.mic_active:
                self._set_state(State.LISTENING)
            elif self.wake_listening:
                self._set_state(State.WAITING)
            elif self._state is not State.ERROR or self._mic_on:
                self._set_state(State.IDLE)

    def _component(self, name: str, ok: bool, message: str) -> None:
        self._emit(ComponentStatus(name, ok, message))
        if not ok:
            log.warning("%s: %s", name, message)

    def _notice(self, text: str) -> None:
        self._emit(Message(next(self._ids), "notice", text, True))

    # -- loading --------------------------------------------------------------------
    def _load(self) -> None:
        self._set_state(State.LOADING, "loading voice activity detection")
        try:
            self._vad = self._make_vad()
            note = "Silero VAD" if self._vad.name == "silero" else "energy-based fallback"
            self._component("voice detection", True, note)
        except Exception as exc:
            self._vad = EnergyVAD()
            self._component("voice detection", True, f"energy-based fallback ({exc})")
        self._set_state(State.LOADING, "loading voice, speech recognition and language model")

        def load_voice() -> None:
            try:
                self._tts.load()
                self._tts.synthesize("Hi.")  # first synthesis is slow; pay it now
                self._component("voice", True, self._tts.name)
            except Exception as exc:
                self._component("voice", False, str(exc))

        # Voice (CPU), speech recognition (GPU when available) and the LLM server load
        # independently, so loading them in parallel cuts startup to the slowest one.
        workers = [threading.Thread(target=fn, daemon=True, name=f"chiku-load-{fn.__name__}")
                   for fn in (load_voice, self._load_stt, self._check_llm)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        if self._wake_on and self._load_wake():
            self._sync_listener()
        self._ready.set()
        if self.mic_active:
            self._set_state(State.LISTENING)
        else:
            self._set_state(State.WAITING if self._wake_on and self._wake_ready else State.IDLE)

    def _load_stt(self) -> None:
        try:
            self._stt.load()
            self._component("speech recognition", True, self._stt.name)
        except Exception as exc:
            self._component("speech recognition", False, str(exc))

    def _load_wake(self) -> bool:
        if self._wake_ready:
            return True
        if self._wake is None and self._make_wake is not None:
            self._wake = self._make_wake()
        if self._wake is None:
            return False
        try:
            self._wake.load()
        except Exception as exc:  # model missing or unloadable
            self._component("wake word", False, str(exc))
            return False
        self._wake_ready = True
        self._component("wake word", True, f"say “Hey Chiku” or “Wake up” ({self._wake.name})")
        return True

    def _check_llm(self) -> None:
        st = self._llm.status()
        self._component("language model", st.available, st.message)
        if st.available:
            self._llm.warm_up()
        else:
            self._notice(st.message)

    # -- listener -------------------------------------------------------------------
    def _listen_loop(self) -> None:
        failures = 0
        while not self._listener_stop.is_set():
            if not self._mic_on and self._screen_locked():
                # Wake-word listening never runs on the lock screen; the mic stays closed.
                if not self._wake_paused:
                    self._wake_paused = True
                    log.info("wake word paused: screen locked")
                    self._settle_state()
                if self._listener_stop.wait(2.0):
                    return
                continue
            if self._wake_paused:
                self._wake_paused = False
                log.info("wake word resumed")
            cap = self._make_capture(self.settings.audio.input_device)
            try:
                cap.start()
            except AudioInputError as exc:
                failures += 1
                self._component("microphone", False, str(exc))
                if failures > 3 or self._listener_stop.wait(1.0 * failures):
                    self._mic_failed(str(exc))
                    return
                rescan()
                continue
            self._capture = cap
            name = self.settings.audio.input_device or "default microphone"
            self._component("microphone", True, f"Listening on {name}")
            if self._current is None:
                self._settle_state()
            problem = self._pump(cap)
            cap.stop()
            self._capture = None
            if problem is None:
                return
            if problem is _PAUSED:
                continue
            failures += 1
            self._component("microphone", False, problem)
            if failures > 3:
                self._mic_failed(problem)
                return
            if self._listener_stop.wait(1.0):
                return
            rescan()

    def _mic_failed(self, message: str) -> None:
        self._mic_on = False
        self._wake_session = False
        self._capture = None
        if self._wake_on:
            self._wake_on = False
            self._component("wake word", False, "Stopped: the microphone isn't working.")
        self._emit(MicChanged(False))
        self._set_state(State.ERROR, f"Microphone problem: {message}")

    def _pump(self, cap: Capture) -> str | None:
        """Process mic frames until stopped (None), paused (_PAUSED) or broken (a message)."""
        a = self.settings.audio
        vad = self._vad or EnergyVAD()
        vad.reset()
        seg = UtteranceSegmenter(a.vad_threshold, a.min_speech_ms, a.min_silence_ms,
                                 a.max_utterance_s)
        wake_seg = UtteranceSegmenter(a.vad_threshold, a.min_speech_ms, a.min_silence_ms,
                                      MAX_SEGMENT_S)
        barge = SustainedSpeech(a.barge_in_threshold, a.barge_in_min_ms)
        recent: deque[tuple[np.ndarray, float]] = deque(maxlen=24)
        last_frame = lock_checked = time.monotonic()
        while not self._listener_stop.is_set():
            frame = cap.read(0.25)
            now = time.monotonic()
            if frame is None:
                if now - last_frame > 2.5:
                    return "The microphone stopped sending audio."
                self._player.close_if_idle()
                continue
            last_frame = now
            prob = vad(frame)
            if not self._mic_on:  # only listening for the wake word
                if now - lock_checked >= 2.0:
                    lock_checked = now
                    if self._screen_locked():
                        return _PAUSED
                if self._player.is_playing or self._current is not None:
                    wake_seg.reset()  # never wake on Chiku's own voice
                    continue
                for ev in wake_seg.process(frame, prob):
                    if isinstance(ev, SpeechEnded) and ev.duration_s >= 0.35 \
                            and self._check_wake(ev.audio, now):
                        seg.reset()
                        barge.reset()
                        recent.clear()
                continue
            if self._wake_session and self._wake_expired(now, seg):
                self._end_wake_session()
                wake_seg.reset()
                continue
            recent.append((frame, prob))
            if self._player.is_playing:
                seg.reset()
                if a.barge_in and barge.feed(prob):
                    barge.reset()
                    self.interrupt("barge-in")
                    self._set_state(State.HEARING)
                    for f, p in recent:  # don't lose the words that triggered barge-in
                        seg.process(f, max(p, seg.threshold))
                continue
            barge.reset()
            for ev in seg.process(frame, prob):
                if isinstance(ev, SpeechStarted):
                    self._wake_until = now + self.settings.wake.follow_up_s
                    if self._current is not None:
                        # User kept talking: drop the in-flight reply; their earlier words stay
                        # in history so the next turn sees everything they said.
                        self.interrupt("user continued speaking")
                    self._set_state(State.HEARING)
                elif ev.duration_s < 0.35:
                    self._settle_state()
                else:
                    self._set_state(State.THINKING, "transcribing")
                    self._turns.put(_Turn(audio=ev.audio, t_speech_end=now))
        return None

    # -- wake word ------------------------------------------------------------------
    def _check_wake(self, audio: np.ndarray, now: float) -> bool:
        if self._wake is None or not self._wake_ready:
            return False
        try:
            hit = self._wake.check(audio)
        except Exception:
            log.exception("wake word check failed")
            return False
        if hit is None:
            return False  # what was said is discarded, never logged
        log.info("wake word heard (%s)", hit.phrase)
        self._wake_session = True
        self._wake_until = now + self.settings.wake.follow_up_s
        self._mic_on = True
        self._emit(MicChanged(True))
        self._emit(Woke(hit.phrase))
        try:
            self._player.play(chime())
        except AudioOutputError as exc:
            self._component("speaker", False, str(exc))
        if hit.request:
            # "Hey Chiku, what time is it?" in one breath: the main model transcribes it all.
            self._set_state(State.THINKING, "transcribing")
            self._turns.put(_Turn(audio=audio, t_speech_end=now, wake=True))
        else:
            self._set_state(State.LISTENING)
        return True

    def _wake_expired(self, now: float, seg: UtteranceSegmenter) -> bool:
        return (now > self._wake_until and self._current is None and self._turns.empty()
                and not seg.in_speech and not self._player.is_playing)

    def _end_wake_session(self) -> None:
        log.info("no request for %ds; back to waiting for the wake word",
                 self.settings.wake.follow_up_s)
        self._wake_session = False
        self._mic_on = False
        self._emit(MicChanged(False))
        self._settle_state()

    # -- turns ----------------------------------------------------------------------
    def _worker_loop(self) -> None:
        while True:
            turn = self._turns.get()
            if turn is None:
                return
            self._ready.wait()
            token = CancelToken()
            with self._turn_lock:
                self._current = token
            try:
                self._run_turn(turn, token)
            except Cancelled:
                log.info("turn cancelled: %s", token.reason)
            except Exception:
                log.exception("turn failed")
                self._notice("Something went wrong while answering. Details are in the log.")
            finally:
                with self._turn_lock:
                    self._current = None
                self._settle_state()

    def _run_turn(self, turn: _Turn, token: CancelToken) -> None:
        metrics: dict[str, float] = {}
        if turn.audio is not None:
            self._set_state(State.THINKING, "transcribing")
            try:
                tr = self._stt.transcribe(turn.audio)
            except Exception as exc:
                self._component("speech recognition", False, f"Speech recognition failed: {exc}")
                return
            metrics["audio_s"] = round(tr.audio_s, 2)
            metrics["stt_s"] = round(tr.elapsed_s, 3)
            text = strip_wake(tr.text) if turn.wake else tr.text
            if not text:
                log.info("empty transcript (%.1fs audio)", tr.audio_s)
                return
            turn.language = tr.language
            if tr.language != "en":
                log.info("spoken language %s (p=%.2f)", tr.language, tr.language_prob)
        else:
            text = (turn.text or "").strip()
            if not text:
                return
            # "ok", "thanks", an app name: too short to switch the conversation's language
            guess = guess_text(text)
            if self._language and guess != self._language and len(text.split()) < 3 \
                    and script_language(text) is None:
                guess = self._language
            turn.language = guess or self._language
        self._user_text = text
        self._language = turn.language
        self._emit(Message(next(self._ids), "user", text, True))
        self._history.append(ChatMessage("user", text))
        reader = self._registry.get("read_web_page")
        if reader is not None and hasattr(reader, "session"):
            # Links the user typed or said themselves need no extra approval to read.
            for url in re.findall(r"https?://[^\s<>\"']+", text):
                reader.session.remember(url.rstrip(".,!?)"))
        log.info("turn: %d chars from %s", len(text), "voice" if turn.audio is not None else "text")
        token.raise_if_cancelled()
        self._set_state(State.THINKING)
        if not self._fast_path(text, token, turn, metrics):
            self._llm_turn(token, turn, metrics)
        if metrics:
            log.info("turn metrics %s", metrics)
            self._emit(TurnMetrics(metrics))

    def _speaker(self, token: CancelToken, turn: _Turn, metrics: dict[str, float]) -> _Speaker:
        t_start = time.monotonic()

        def first_audio() -> None:
            now = time.monotonic()
            metrics["reply_to_audio_s"] = round(now - t_start, 3)
            if turn.t_speech_end is not None:
                metrics["end_of_speech_to_audio_s"] = round(now - turn.t_speech_end, 3)
            if not token.cancelled:
                self._set_state(State.SPEAKING)

        return _Speaker(self, token, first_audio, turn.language)

    def _voice_for(self, speech: str, turn_language: str | None) -> tuple[bool, str | None]:
        """(speak it?, voice) for one sentence. Voice None = the user's English voice; not
        spoken when Chiku has no voice for the sentence's language (the text still shows)."""
        lang = speech_language(speech, turn_language)
        if lang == "en":
            return True, None
        voice = voice_for(lang, self.settings.tts.voice, self.settings.tts.voices)
        if voice is None:
            if lang not in self._voiceless_told:
                self._voiceless_told.add(lang)
                self._notice(f"I can understand and write {language_name(lang)}, but I don't "
                             f"have a {language_name(lang)} voice yet, so I'll reply in text.")
            return False, None
        return True, voice

    def current_user_text(self) -> str:
        """The message being answered right now (memory tools use it to detect consent)."""
        return self._user_text

    @property
    def memory(self) -> MemoryStore | None:
        return self._memory

    def _tool_on(self, name: str) -> bool:
        if self._registry.get(name) is None or name in self.settings.tools.disabled:
            return False
        if name in ("remember", "forget_memory", "search_memory"):
            return self.settings.memory.enabled and self._memory is not None
        return True

    def _plan_fast(self, text: str) -> tuple[str, dict[str, object], str, str] | None:
        """(tool, args, acknowledgement, language) for a recognised simple command, else None."""
        intent = parse_command(text)
        if intent is None:
            return None
        say = _PHRASES.get(intent.lang, _PHRASES["en"])
        lang = intent.lang if intent.lang in _PHRASES else "en"
        target = intent.target
        if intent.kind == "open_app":
            hit = self._catalog.best(target, min_score=0.8) if self._catalog else None
            if hit is None:
                return None
            name = hit[0].name
            ack = random.choice(say["open"]).format(name=name)  # noqa: S311 - not security
            return "open_app", {"app_name": name}, ack, lang
        if intent.kind == "open_url":
            return "open_url", {"url": target}, say["url"][0].format(name=target), lang
        if intent.kind == "remember":
            return ("remember", {"text": target[:300], "category": "other"}, "",
                    lang) if self._tool_on("remember") else None
        if intent.kind == "forget":
            if not self._tool_on("forget_memory"):
                return None
            return "forget_memory", {"query": target[:200]}, "", lang
        packages = {"install": "install_package", "uninstall": "uninstall_package",
                    "update": "update_package"}
        if intent.kind in packages:
            return (packages[intent.kind], {"query": target},
                    say[intent.kind][0].format(name=target), lang)
        if intent.kind == "message":
            if not self._tool_on("send_message") or len(intent.extra) != 3:
                return None
            app, to, body = intent.extra
            label = "WhatsApp" if app == "whatsapp" else "Discord"
            return ("send_message", {"app": app, "to": to, "text": body[:2000]},
                    say["message"][0].format(app=label, name=to), lang)
        if intent.kind == "check_updates":
            return "list_updates", {}, say["updates"][0], lang
        if intent.kind == "media":
            if self._registry.get("media_control") is None:
                return None
            return "media_control", {"action": target}, "", lang
        tool_name = "switch_to_app" if intent.kind == "switch_app" else "close_app"
        tool = self._registry.get(tool_name)
        if tool is None or not getattr(tool, "has_match", lambda _n: False)(target):
            return None  # no such window: let the model handle it (maybe it means something else)
        key = "switch" if intent.kind == "switch_app" else "close"
        return tool_name, {"app_name": target}, say[key][0].format(name=target), lang

    def _fast_path(
        self, text: str, token: CancelToken, turn: _Turn, metrics: dict[str, float]
    ) -> bool:
        plan = self._plan_fast(text)
        if plan is None or not self._tool_on(plan[0]):
            return False
        tool, args, ack, lang = plan
        say = _PHRASES[lang]
        mid = next(self._ids)
        sp = self._speaker(token, turn, metrics)
        if ack:
            self._emit(Message(mid, "assistant", ack, False, BUILTIN_SOURCE))
            sp.say(ack)
        self._emit(ToolActivity(tool, "running", ack or tool))
        res = self._executor.execute(tool, args, token)
        self._emit(ToolActivity(tool, "ok" if res.ok else "failed", res.message))
        if res.ok and res.data.get("verified") == "already running":
            follow = say["running"][0]
        elif res.ok:
            follow = say["done"][0] if tool == "open_app" else res.message
        else:
            follow = say["failed"][0].format(message=res.message)
        if tool == "media_control" and res.ok:
            follow = ""  # stay quiet: talking over the music it just changed is annoying
        elif tool == "remember" and res.ok:
            follow = random.choice(say["remembered"])  # noqa: S311
        elif tool == "forget_memory" and res.ok:
            follow = say["forgot"][0]
        elif tool in ("remember", "forget_memory"):
            follow = res.message
        full = f"{ack} {follow}".strip() or res.message
        self._emit(Message(mid, "assistant", full, True, BUILTIN_SOURCE))
        chunker = SentenceChunker()
        for sentence in [*chunker.feed(follow), *chunker.flush()]:
            sp.say(sentence)  # one sentence at a time: each can be in a different language
        sp.finish()
        self._history.append(ChatMessage("assistant", full))
        return True

    def _capabilities(self) -> tuple[str, set[str]]:
        """Web search state and the tools to hide from the model right now."""
        hidden: set[str] = set(self.settings.tools.disabled)
        if not self.settings.memory.enabled or self._memory is None:
            hidden |= {"remember", "forget_memory", "search_memory"}
        ws = self._registry.get("web_search")
        web = ws.state() if ws is not None and hasattr(ws, "state") else "off"
        if web == "off":
            hidden |= {"web_search", "read_web_page"}
        if not self.settings.web.allow_page_reading:
            hidden.add("read_web_page")
        f = self.settings.files
        if not f.enabled:
            hidden |= FILE_TOOLS
        else:
            if not f.allow_reading:
                hidden.add("read_text_file")
            if not f.allow_changes:
                hidden |= {"create_folder", "move_or_rename", "move_to_recycle_bin"}
        return web, hidden

    def _llm_turn(self, token: CancelToken, turn: _Turn, metrics: dict[str, float]) -> None:
        llm = self._llm
        source = llm.label
        web, hidden = self._capabilities()
        files_on = self.settings.files.enabled and self._registry.get("find_files") is not None
        mem = self.settings.memory
        memory_on = mem.enabled and self._memory is not None and "remember" not in hidden
        share = llm.is_local or mem.share_with_cloud
        memory_text = ""
        if memory_on and share and self._memory is not None:
            memory_text = prompt_section(self._memory.all(), mem.max_in_prompt)
        system = build_system_prompt(self.settings.persona, model_label=source,
                                     web_search=web, files=files_on, memory=memory_text,
                                     memory_enabled=memory_on and share)
        if memory_on and not share:
            hidden |= {"remember", "forget_memory", "search_memory"}
        messages = [ChatMessage("system", system), *self._trimmed_history()]
        for i in range(len(messages) - 1, 0, -1):
            if messages[i].role == "user":  # clock on the newest message only (cache-friendly)
                reply_in = language_name(turn.language) if turn.language else None
                messages[i] = ChatMessage("user", time_prefix(language=reply_in)
                                          + messages[i].content)
                break
        tools = self._registry.schemas(exclude=hidden)
        sources: list[dict[str, str]] = []
        sp_early = None
        if "read_web_page" not in hidden:
            self._prefetch_links(messages, sources, token)
        last_user = self._user_text  # raw text, without the time prefix
        query = research_query(last_user) if "web_search" not in hidden else None
        if query and not sources:
            sp_early = self._speaker(token, turn, metrics)
            sp_early.say(random.choice(_LOOKUPS))  # noqa: S311 - fills the silence
            self._prefetch_search(query, "read_web_page" not in hidden, messages, sources,
                                  token)
        if sp_early is not None:
            sp_early.finish(wait_for_playback=False)  # audio stays queued ahead of the answer
        mid = next(self._ids)
        self._emit(Message(mid, "assistant", "", False, source))
        sp = self._speaker(token, turn, metrics)
        shown = ""
        last_round = ""
        t0 = time.monotonic()
        try:
            for _round in range(self.MAX_TOOL_ROUNDS):
                think, chunker = ThinkFilter(), SentenceChunker()
                last_round = ""
                calls = []
                for chunk in llm.stream_chat(messages, tools, token):
                    if "llm_first_token_s" not in metrics and (chunk.text or chunk.tool_calls):
                        metrics["llm_first_token_s"] = round(time.monotonic() - t0, 3)
                    visible = think.feed(chunk.text) if chunk.text else ""
                    if visible:
                        last_round += visible
                        shown += visible
                        self._emit(Message(mid, "assistant", shown, False, source))
                        for sentence in chunker.feed(visible):
                            sp.say(sentence)
                    calls.extend(chunk.tool_calls)
                tail = think.flush()
                if tail:
                    last_round += tail
                    shown += tail
                    chunker.feed(tail)
                for sentence in chunker.flush():
                    sp.say(sentence)
                if not calls:
                    break
                _ensure_ids(calls)
                call_msg = ChatMessage("assistant", last_round, tool_calls=calls)
                messages.append(call_msg)
                self._history.append(call_msg)
                for call in calls:
                    token.raise_if_cancelled()
                    self._run_tool_into(call, messages, sources, token)
                if shown and not shown.endswith((" ", "\n")):
                    shown += " "
            else:
                note = "Sorry, I got stuck going back and forth with my tools."
                sp.say(note)
                shown += note
                last_round = note
        except Cancelled:
            sp.finish(wait_for_playback=False)
            partial = shown.strip()
            if partial:
                self._history.append(ChatMessage("assistant", partial + " [interrupted]"))
            shown_partial = (partial + " …") if partial else "…"
            self._emit(Message(mid, "assistant", shown_partial, True, source, sources))
            raise
        except LLMError as exc:
            self._component("language model", False, str(exc))
            spoken = ("I can't reach my language model right now."
                      if isinstance(exc, LLMUnavailable)
                      else "Sorry, my language model ran into a problem.")
            sp.say(spoken)
            shown = (shown + " " + spoken).strip()
            last_round = ""
            self._notice(str(exc))
        sp.finish()
        interrupted = " [interrupted while speaking]" if token.cancelled else ""
        if last_round.strip():
            self._history.append(ChatMessage("assistant", last_round.strip() + interrupted))
        self._emit(Message(mid, "assistant", shown.strip() or "(no reply)", True, source, sources))

    def _run_tool_into(self, call: ToolCall, messages: list[ChatMessage],
                       sources: list[dict[str, str]], token: CancelToken) -> ToolResult:
        """Execute one tool call and record it in the turn messages, history and sources."""
        self._emit(ToolActivity(call.name, "running", f"{call.name} {call.arguments}"))
        res = self._executor.execute(call.name, call.arguments, token)
        self._emit(ToolActivity(call.name, "ok" if res.ok else "failed", res.message))
        if res.ok:
            for src in res.data.get("sources") or []:
                if src.get("url") and all(s["url"] != src["url"] for s in sources):
                    sources.append({"title": str(src.get("title") or src["url"]),
                                    "url": str(src["url"])})
        full_result = res.for_model()
        messages.append(ChatMessage("tool", full_result, tool_name=call.name,
                                    tool_call_id=call.id))
        # Long page/file text stays in this turn only; history keeps a short copy so later
        # turns don't overflow the context window.
        kept = full_result if len(full_result) <= 1500 else full_result[:1500] + " …[trimmed]"
        self._history.append(ChatMessage("tool", kept, tool_name=call.name,
                                         tool_call_id=call.id))
        return res

    def _prefetch_search(self, query: str, read_top: bool, messages: list[ChatMessage],
                         sources: list[dict[str, str]], token: CancelToken) -> None:
        """Search (and read the best page) before the model answers a fresh-info question.

        Small local models tend to answer from stale memory instead of calling web_search,
        so requests that clearly need current information are routed deterministically.
        """
        search_call = ToolCall("web_search", {"query": query}, _new_id())
        messages.append(ChatMessage("assistant", "", tool_calls=[search_call]))
        self._history.append(messages[-1])
        res = self._run_tool_into(search_call, messages, sources, token)
        if not (res.ok and read_top):
            return
        for row in (res.data.get("results") or [])[:2]:
            token.raise_if_cancelled()
            read_call = ToolCall("read_web_page", {"url": row["url"]}, _new_id())
            messages.append(ChatMessage("assistant", "", tool_calls=[read_call]))
            self._history.append(messages[-1])
            if self._run_tool_into(read_call, messages, sources, token).ok:
                break  # one good page plus the snippets is enough for a spoken answer
            # A failed optional read only distracts small models from the good snippets they
            # already have; drop it from the context (it stays in the audit log and UI).
            del messages[-2:]
            del self._history[-2:]

    def _prefetch_links(self, messages: list[ChatMessage], sources: list[dict[str, str]],
                        token: CancelToken) -> None:
        """If the user's message contains links, read them before asking the model.

        Small local models often decline to call the page reader on their own; reading the
        pages the user explicitly pointed at is deterministic and goes through the same
        permissioned, audited executor.
        """
        last_user = self._user_text  # raw text, without the time prefix
        urls = [u.rstrip(".,!?)") for u in re.findall(r"https?://[^\s<>\"']+", last_user)][:2]
        if not urls:
            return
        calls = [ToolCall("read_web_page", {"url": u}, _new_id()) for u in urls]
        call_msg = ChatMessage("assistant", "", tool_calls=calls)
        messages.append(call_msg)
        self._history.append(call_msg)
        for call in calls:
            token.raise_if_cancelled()
            self._run_tool_into(call, messages, sources, token)

    def open_link(self, url: str) -> None:
        """Open a source link clicked in the UI, through the normal permissioned tool path."""
        def run() -> None:
            res = self._executor.execute("open_url", {"url": url}, CancelToken())
            if not res.ok:
                self._notice(res.message)

        threading.Thread(target=run, daemon=True, name="chiku-link").start()

    def _trimmed_history(self) -> list[ChatMessage]:
        h = self._history
        users = [i for i, m in enumerate(h) if m.role == "user"]
        n = self.settings.llm.max_history_turns
        if len(users) > n:
            h = h[users[-n]:]
        budget = self.settings.llm.context_tokens * 3  # ~3 chars per token, leaves headroom
        while sum(len(m.content) for m in h) > budget:
            later = [i for i, m in enumerate(h) if m.role == "user" and i > 0]
            if not later:
                break
            h = h[later[0]:]
        if len(self._history) > 200:
            self._history = list(h)
        return list(h)

    # -- confirmation -----------------------------------------------------------------
    def _confirm(self, name: str, description: str) -> bool:
        self._set_state(State.THINKING, f"waiting for your OK: {description}")
        self._notice(f"Approval needed: {description}")
        try:
            return bool(self.confirm_handler(name, description))
        finally:
            self._set_state(State.THINKING)

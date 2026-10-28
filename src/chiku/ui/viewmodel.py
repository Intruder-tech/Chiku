"""View-model: turns assistant events (from worker threads) into Qt signals on the UI thread.

Widgets never talk to the engine's threads directly; they subscribe to these signals and call
the small command API below. This keeps the widgets dumb and testable.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot

from chiku.assistant import (
    Assistant,
    ComponentStatus,
    Event,
    Message,
    MicChanged,
    State,
    StateChanged,
    ToolActivity,
    TurnMetrics,
)


class AssistantViewModel(QObject):
    state_changed = Signal(object, str)  # State, detail
    message = Signal(object)  # Message
    tool = Signal(object)  # ToolActivity
    component = Signal(str, bool, str)
    mic_changed = Signal(bool)
    metrics = Signal(dict)
    _event = Signal(object)  # internal: crosses from engine threads to the UI thread
    confirm_requested = Signal(str, str, object)

    def __init__(self) -> None:
        super().__init__()
        self.assistant: Assistant | None = None
        self.state: State = State.LOADING
        self.detail = ""
        self.components: dict[str, tuple[bool, str]] = {}
        self._event.connect(self._dispatch)

    # engine -> UI ------------------------------------------------------------------------
    def on_event(self, ev: Event) -> None:
        """Called from any thread (passed to the Assistant as on_event)."""
        self._event.emit(ev)

    @Slot(object)
    def _dispatch(self, ev: object) -> None:
        if isinstance(ev, StateChanged):
            self.state, self.detail = ev.state, ev.detail
            self.state_changed.emit(ev.state, ev.detail)
        elif isinstance(ev, Message):
            self.message.emit(ev)
        elif isinstance(ev, ToolActivity):
            self.tool.emit(ev)
        elif isinstance(ev, ComponentStatus):
            self.components[ev.component] = (ev.ok, ev.message)
            self.component.emit(ev.component, ev.ok, ev.message)
        elif isinstance(ev, MicChanged):
            self.mic_changed.emit(ev.on)
        elif isinstance(ev, TurnMetrics):
            self.metrics.emit(dict(ev.values))

    def confirm_from_worker(self, name: str, description: str) -> bool:
        """Blocking confirmation for tools; the window shows the dialog on the UI thread."""
        box: dict[str, object] = {"done": threading.Event(), "ok": False}
        self.confirm_requested.emit(name, description, box)
        done = box["done"]
        assert isinstance(done, threading.Event)
        return bool(box["ok"]) if done.wait(timeout=180) else False

    # UI -> engine ------------------------------------------------------------------------
    def attach(self, assistant: Assistant) -> None:
        self.assistant = assistant
        assistant.confirm_handler = self.confirm_from_worker

    def set_mic(self, on: bool) -> None:
        if self.assistant:
            self.assistant.set_mic(on)

    def send(self, text: str) -> None:
        if self.assistant and text.strip():
            self.assistant.submit_text(text.strip())

    def interrupt(self, reason: str = "user") -> None:
        if self.assistant:
            self.assistant.interrupt(reason)

    def levels(self) -> tuple[float, float]:
        return self.assistant.levels() if self.assistant else (0.0, 0.0)

    @property
    def mic_active(self) -> bool:
        return bool(self.assistant and self.assistant.mic_active)

    @property
    def mic_wanted(self) -> bool:
        return bool(self.assistant and self.assistant._mic_on)  # noqa: SLF001

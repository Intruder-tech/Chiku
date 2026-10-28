"""Chat page header: animated mascot, state text, live level meter, component status."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from chiku.assistant import State
from chiku.ui.components import label
from chiku.ui.mascot import MascotWidget
from chiku.ui.waveform import Waveform

STATE_TEXT = {
    State.LOADING: "Getting ready…",
    State.IDLE: "Mic is off. Turn it on or type below.",
    State.WAITING: "Say “Hey Chiku” or “Wake up”, or type below.",
    State.LISTENING: "Listening",
    State.HEARING: "Hearing you…",
    State.THINKING: "Thinking…",
    State.SPEAKING: "Speaking · talk or press Esc to interrupt",
    State.ERROR: "Something went wrong",
}
SHORT = {"microphone": "mic", "voice detection": "VAD", "speech recognition": "speech-to-text",
         "language model": "AI model"}
ORDER = ["microphone", "voice detection", "speech recognition", "language model", "voice",
         "speaker"]


def state_text(state: State, detail: str) -> str:
    if detail and state in (State.LOADING, State.THINKING, State.ERROR):
        return detail[0].upper() + detail[1:] + ("" if state is State.ERROR else "…")
    return STATE_TEXT[state]


class VoiceBar(QWidget):
    def __init__(self, mascot_size: int = 104, vertical: bool = False):
        super().__init__()
        # vertical: mascot above the text (the workspace's right panel)
        lay = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 4)
        self.mascot = MascotWidget(mascot_size)
        lay.addWidget(self.mascot)
        col = QVBoxLayout()
        self.state = label(STATE_TEXT[State.LOADING], "state", wrap=True)
        if vertical:
            self.state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.state)
        self.wave = Waveform()
        self.wave.setMinimumHeight(36)
        self.wave.setMaximumHeight(44)
        col.addWidget(self.wave)
        self.status = label("", "status", wrap=True)
        col.addWidget(self.status)
        lay.addLayout(col, 1)
        self.components: dict[str, tuple[bool, str]] = {}

    def set_state(self, state: State, detail: str) -> None:
        self.state.setText(state_text(state, detail))
        self.state.setProperty("error", state is State.ERROR)
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def tick(self, state: State, inp: float, out: float, mic_active: bool) -> None:
        self.mascot.update_state(state, out if out > 0 else inp)
        if out > 0:
            self.wave.push(out, "output")
        elif mic_active:
            self.wave.push(inp, "input")
        else:
            self.wave.push(0.0, "idle")

    def set_component(self, name: str, ok: bool, message: str) -> None:
        self.components[name] = (ok, message)
        parts = [f"{SHORT.get(n, n)} {'✓' if self.components[n][0] else '✕'}"
                 for n in ORDER if n in self.components]
        bad = [f"{n}: {m}" for n, (o, m) in self.components.items() if not o]
        self.status.setText("   ".join(parts) + ("\n" + bad[-1] if bad else ""))
        self.status.setToolTip("\n".join(f"{n}: {m}" for n, (_o, m) in self.components.items()))
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft)

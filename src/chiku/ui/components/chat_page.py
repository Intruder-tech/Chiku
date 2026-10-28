"""Chat page. A new chat greets you in the middle of the page with the message box under the
greeting; once the first message is sent the greeting is gone for that chat, the transcript
fills the page and the box docks at the bottom (as in desktop chat apps)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from chiku.assistant import State
from chiku.ui import theme
from chiku.ui.components import button, icons, label, named
from chiku.ui.components.chat import ChatView
from chiku.ui.components.input_bar import InputBar, icon_button
from chiku.ui.components.voice_bar import state_text
from chiku.ui.mascot import MascotWidget
from chiku.ui.waveform import Mode, Waveform

SUGGESTIONS = ("What's the weather tomorrow?", "Open Notepad", "Remember that I like jazz",
               "Check for app updates")
COMPOSER_WIDTH = 800


def greeting_for(name: str | None, now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    part = "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening"
    return f"Good {part}, {name}" if name else f"Good {part}"


class StatusStrip(QWidget):
    """Top of the chat: Chiku's live state and voice level, and the side-panel switch."""

    panel_toggled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("statusStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 10, 0)
        row.setSpacing(8)
        self.mascot = MascotWidget(22)
        self.mascot.setFixedSize(40, 46)
        row.addWidget(self.mascot)
        self.state = label(state_text(State.LOADING, ""), "state")
        row.addWidget(self.state)
        self.wave = Waveform(bars=20, background="BG")
        self.wave.setMinimumHeight(20)
        self.wave.setFixedSize(96, 22)
        row.addWidget(self.wave)
        row.addStretch(1)
        self.panel_button = icon_button(icons.PANEL, "Show or hide the side panel (Ctrl+.)")
        self.panel_button.clicked.connect(self.panel_toggled.emit)
        row.addWidget(self.panel_button)
        self.refresh_icons()

    def refresh_icons(self) -> None:
        self.panel_button.setIcon(icons.glyph_icon(icons.PANEL, theme.MUTED, 16))

    def set_state(self, state: State, detail: str) -> None:
        self.state.setText(state_text(state, detail))

    def tick(self, state: State, inp: float, out: float, mic_active: bool) -> None:
        level = out if out > 0 else inp
        self.mascot.update_state(state, level)
        mode: Mode = "output" if out > 0 else "input" if mic_active else "idle"
        self.wave.push(level if mode != "idle" else 0.0, mode)


class ChatPage(QWidget):
    suggestion = Signal(str)

    def __init__(self, on_link: Callable[[str], None] = lambda _u: None):
        super().__init__()
        self.setObjectName("page")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.strip = StatusStrip()
        col.addWidget(self.strip)
        self._top = self._spacer()
        col.addWidget(self._top, 2)
        self.chat = ChatView(on_link=on_link)
        col.addWidget(self.chat, 1)

        self.greeting_box = named(QWidget(), "welcome")
        g = QVBoxLayout(self.greeting_box)
        g.setContentsMargins(16, 0, 16, 18)
        g.setSpacing(6)
        self.greeting = label(greeting_for(None), "greeting")
        self.greeting.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.subtitle = label("How can I help you today? Type below, or press the mic and talk.",
                              "greetingSub", wrap=True)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        g.addWidget(self.greeting)
        g.addWidget(self.subtitle)
        col.addWidget(self.greeting_box)

        self.input = InputBar()
        composer = QWidget()
        cl = QHBoxLayout(composer)
        cl.setContentsMargins(0, 0, 0, 0)
        self.input.setMaximumWidth(COMPOSER_WIDTH)
        cl.addStretch(1)
        cl.addWidget(self.input, 100)
        cl.addStretch(1)
        col.addWidget(composer)

        self.chips = QWidget()
        chips = QHBoxLayout(self.chips)
        chips.setContentsMargins(16, 0, 16, 0)
        chips.setSpacing(8)
        chips.addStretch(1)
        for text in SUGGESTIONS:
            chip = button(text, "chip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, t=text: self.suggestion.emit(t))
            chips.addWidget(chip)
        chips.addStretch(1)
        col.addWidget(self.chips)
        self._bottom = self._spacer()
        col.addWidget(self._bottom, 3)

        self._empty = True
        self.chat.started.connect(lambda: self.set_empty(False))
        self.set_empty(True)

    @staticmethod
    def _spacer() -> QWidget:
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        return w

    @property
    def is_new_chat(self) -> bool:
        return self._empty

    def set_empty(self, empty: bool) -> None:
        """New chat: greeting in the middle. Started chat: transcript, box at the bottom."""
        self._empty = empty
        for w in (self._top, self.greeting_box, self.chips, self._bottom):
            w.setVisible(empty)
        self.chat.setVisible(not empty)

    def set_user(self, name: str | None) -> None:
        self.greeting.setText(greeting_for(name))

    def reset(self) -> None:
        """Start over with an empty chat (the greeting comes back for the new chat)."""
        self.chat.clear()
        self.set_empty(True)

    def refresh_icons(self) -> None:
        self.input.refresh_icons()
        self.strip.refresh_icons()

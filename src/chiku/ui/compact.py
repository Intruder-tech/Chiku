"""Compact mode: a small, draggable, always-on-top Chiku with mascot, state and mic."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from chiku.assistant import State
from chiku.ui.components import button, label, named
from chiku.ui.components.voice_bar import state_text
from chiku.ui.mascot import MascotWidget


class CompactWindow(QWidget):
    mic_toggled = Signal(bool)
    expand = Signal()
    stop = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Chiku (compact)")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = named(QWidget(), "compact")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer.addWidget(card)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(8, 6, 10, 6)
        self.mascot = MascotWidget(64)
        lay.addWidget(self.mascot)
        col = QVBoxLayout()
        self.state = label("Getting ready…", "state", wrap=True)
        self.state.setFixedWidth(150)
        col.addWidget(self.state)
        row = QHBoxLayout()
        self.mic = button("Mic", "mic")
        self.mic.setCheckable(True)
        self.mic.clicked.connect(lambda: self.mic_toggled.emit(self.mic.isChecked()))
        row.addWidget(self.mic)
        stop = button("■", "ghost")
        stop.setToolTip("Stop talking")
        stop.clicked.connect(self.stop.emit)
        row.addWidget(stop)
        full = button("⤢", "ghost")
        full.setToolTip("Open the full window")
        full.clicked.connect(self.expand.emit)
        row.addWidget(full)
        col.addLayout(row)
        lay.addLayout(col)
        self._drag: QPoint | None = None
        self.resize(270, 96)

    def set_state(self, state: State, detail: str) -> None:
        self.state.setText(state_text(state, detail))

    def tick(self, state: State, level: float, mic_on: bool) -> None:
        self.mascot.update_state(state, level)
        if self.mic.isChecked() != mic_on:
            self.mic.setChecked(mic_on)

    def place_default(self) -> None:
        screen = self.screen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    # drag anywhere to move the frameless window
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:  # noqa: N802
        self._drag = None

    def mouseDoubleClickEvent(self, _e: QMouseEvent) -> None:  # noqa: N802
        self.expand.emit()

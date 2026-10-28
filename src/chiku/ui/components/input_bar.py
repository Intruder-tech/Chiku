"""Composer: the rounded message box with mic, stop and send, as in desktop chat apps."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from chiku.ui import theme
from chiku.ui.components import icons, named


def icon_button(glyph: str, tip: str, name: str = "iconButton") -> QPushButton:
    b = QPushButton()
    b.setObjectName(name)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setIconSize(QSize(18, 18))
    b.setProperty("glyph", glyph)
    return b


class InputBar(QWidget):
    mic_toggled = Signal(bool)
    send = Signal(str)
    stop = Signal()

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 6, 16, 14)
        frame = named(QFrame(), "composer")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 8, 8, 8)
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setObjectName("composerInput")
        self.input.setPlaceholderText("Message Chiku…")
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)
        self.mic = icon_button(icons.MIC, "Talk to Chiku: microphone on/off (Ctrl+M)", "mic")
        self.mic.setCheckable(True)
        self.mic.clicked.connect(lambda: self.mic_toggled.emit(self.mic.isChecked()))
        self.mic.toggled.connect(lambda _on: self.refresh_icons())
        self.stop_button = icon_button(icons.STOP, "Stop talking / cancel (Esc)")
        self.stop_button.clicked.connect(self.stop.emit)
        self.send_button = icon_button(icons.SEND, "Send (Enter)", "sendButton")
        self.send_button.clicked.connect(self._send)
        for b in (self.stop_button, self.mic, self.send_button):
            row.addWidget(b)
        outer.addWidget(frame)
        self.refresh_icons()

    def refresh_icons(self) -> None:
        """Re-draw the icons in the current theme's colours (after a theme switch)."""
        self.mic.setIcon(icons.glyph_icon(
            icons.MIC, theme.ACCENT_TEXT if self.mic.isChecked() else theme.TEXT))
        self.stop_button.setIcon(icons.glyph_icon(icons.STOP, theme.MUTED, 16))
        self.send_button.setIcon(icons.glyph_icon(icons.SEND, theme.ACCENT_TEXT, 16))

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.send.emit(text)

    def set_mic(self, on: bool) -> None:
        if self.mic.isChecked() != on:
            self.mic.setChecked(on)

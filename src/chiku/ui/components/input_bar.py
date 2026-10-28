"""Bottom bar: mic toggle, message box, stop button."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

from chiku.ui.components import button


class InputBar(QWidget):
    mic_toggled = Signal(bool)
    send = Signal(str)
    stop = Signal()

    def __init__(self) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 16, 12)
        lay.setSpacing(8)
        self.mic = button("Mic", "mic")
        self.mic.setCheckable(True)
        self.mic.setToolTip("Turn the microphone on or off (Ctrl+M)")
        self.mic.clicked.connect(lambda: self.mic_toggled.emit(self.mic.isChecked()))
        lay.addWidget(self.mic)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message Chiku…")
        self.input.returnPressed.connect(self._send)
        lay.addWidget(self.input, 1)
        send = button("Send", "primary")
        send.clicked.connect(self._send)
        lay.addWidget(send)
        stop = button("Stop")
        stop.setToolTip("Stop talking / cancel (Esc)")
        stop.clicked.connect(self.stop.emit)
        lay.addWidget(stop)

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.send.emit(text)

    def set_mic(self, on: bool) -> None:
        if self.mic.isChecked() != on:
            self.mic.setChecked(on)

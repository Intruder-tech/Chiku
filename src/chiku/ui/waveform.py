"""Level meter driven by real audio RMS from the capture/playback streams."""

from __future__ import annotations

from collections import deque
from typing import Literal

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from chiku.ui import theme

Mode = Literal["idle", "input", "output"]


class Waveform(QWidget):
    BARS = 48

    def __init__(self, parent: QWidget | None = None, bars: int = BARS,
                 background: str = "BG"):
        super().__init__(parent)
        self.BARS = bars
        self.background = background  # theme colour name behind the bars
        self._levels: deque[float] = deque([0.0] * self.BARS, maxlen=self.BARS)
        self._mode: Mode = "idle"
        self.setMinimumHeight(64)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def push(self, rms: float, mode: Mode) -> None:
        self._mode = mode
        if mode == "idle":
            # No stream is running: collapse quickly instead of scrolling stale bars out.
            for i, old in enumerate(self._levels):
                self._levels[i] = old * 0.5
            self._levels.append(0.0)
        else:
            # Speech RMS sits around 0.01-0.2; compress so quiet speech is still visible.
            self._levels.append(min(1.0, (rms * 9.0) ** 0.6))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(getattr(theme, self.background)))
        w, h = self.width(), self.height()
        gap = 3.0
        bar_w = max(2.0, (w - gap * (self.BARS - 1)) / self.BARS)
        mid = h / 2
        if self._mode == "idle":
            color = QColor(theme.FAINT)
        elif self._mode == "input":
            color = QColor(theme.MINT)  # your voice: mint
        else:
            color = QColor(theme.ACCENT)  # Chiku's voice: mascot yellow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for i, v in enumerate(self._levels):
            bh = max(2.0, v * (h - 8))
            x = i * (bar_w + gap)
            p.drawRoundedRect(QRectF(x, mid - bh / 2, bar_w, bh), bar_w / 2, bar_w / 2)
        p.end()

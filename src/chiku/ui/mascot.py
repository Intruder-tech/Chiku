"""Animated Chiku mascot, driven only by real engine state and real audio levels.

idle      -> resting, slightly faded (mic is off)
listening -> slow breathing; a soft ring grows with the actual microphone level
hearing   -> perks up and bobs with your voice level
thinking  -> gentle side-to-side tilt
speaking  -> bounces with the level of her own voice output
error     -> faded
The artwork itself is never altered; only scale, rotation, offset and opacity change.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from chiku.assistant import State
from chiku.branding import AVATAR, BRAND_YELLOW, asset
from chiku.ui import theme


class MascotWidget(QWidget):
    def __init__(self, size: int = 112, parent: QWidget | None = None):
        super().__init__(parent)
        self._pix = QPixmap(str(asset(AVATAR)))
        self._size = size
        self._state = State.LOADING
        self._level = 0.0
        self._smooth = 0.0
        self._t0 = time.monotonic()
        self.setFixedHeight(size + 24)
        self.setMinimumWidth(size + 24)
        self.setAccessibleName("Chiku")

    def update_state(self, state: State, level: float) -> None:
        self._state = state
        self._level = max(0.0, min(1.0, (level * 9.0) ** 0.6)) if level > 0 else 0.0
        self._smooth += (self._level - self._smooth) * 0.35
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(theme.BG))
        t = time.monotonic() - self._t0
        s, lvl = self._state, self._smooth
        scale, angle, dy, opacity, ring = 1.0, 0.0, 0.0, 1.0, 0.0
        if s in (State.IDLE, State.LOADING):
            opacity = 0.55 if s is State.IDLE else 0.75
        elif s is State.LISTENING:
            scale = 1.0 + 0.02 * math.sin(t * 2.0)
            ring = lvl
        elif s is State.HEARING:
            scale = 1.03 + 0.05 * lvl
            dy = -6.0 * lvl
            ring = 0.4 + 0.6 * lvl
        elif s is State.THINKING:
            angle = 5.0 * math.sin(t * 2.4)
            scale = 1.0 + 0.015 * math.sin(t * 4.8)
        elif s is State.SPEAKING:
            scale = 1.0 + 0.07 * lvl
            dy = -8.0 * lvl
        elif s is State.ERROR:
            opacity = 0.35
        cx, cy = self.width() / 2, self.height() / 2 + 4
        if ring > 0.02:
            r = self._size * (0.42 + 0.12 * ring)
            glow = QColor(BRAND_YELLOW)
            glow.setAlphaF(0.10 + 0.25 * ring)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(cx, cy), r, r)
        p.setOpacity(opacity)
        p.translate(cx, cy + dy)
        p.rotate(angle)
        p.scale(scale, scale)
        half = self._size / 2
        p.drawPixmap(QRectF(-half, -half, self._size, self._size), self._pix,
                     QRectF(self._pix.rect()))
        p.end()

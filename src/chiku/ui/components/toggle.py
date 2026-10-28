"""On/off switch: a checkbox drawn as a pill switch, with its label on the left (settings)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QCheckBox, QSizePolicy, QWidget

from chiku.ui import theme

_W, _H, _GAP = 38, 22, 16


class Toggle(QCheckBox):
    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def _text_rect(self, width: int) -> QRect:
        return QRect(0, 0, max(40, width - _W - _GAP), 10_000)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        fm = self.fontMetrics()
        text = fm.boundingRect(self._text_rect(width), Qt.TextFlag.TextWordWrap, self.text())
        return max(_H + 12, text.height() + 12)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        w = min(560, self.fontMetrics().horizontalAdvance(self.text()) + _W + _GAP + 4)
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(_W + _GAP + 60, _H + 12)

    def hitButton(self, pos: QPoint) -> bool:  # noqa: N802 - Qt override: whole row clicks
        return self.rect().contains(pos)

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        on, enabled = self.isChecked(), self.isEnabled()
        p.setPen(QColor(theme.TEXT if enabled else theme.FAINT))
        p.drawText(r.adjusted(0, 0, -(_W + _GAP), 0),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                   | int(Qt.TextFlag.TextWordWrap), self.text())
        x, y = r.right() - _W, r.center().y() - _H / 2 + 1
        track = QColor(theme.ACCENT if on else theme.BORDER)
        if not enabled:
            track.setAlphaF(0.45)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(x, y, _W, _H), _H / 2, _H / 2)
        d = _H - 6
        knob_x = x + (_W - d - 3 if on else 3)
        p.setBrush(QColor(theme.ACCENT_TEXT if on else theme.SURFACE))
        p.drawEllipse(QRectF(knob_x, y + 3, d, d))
        if self.hasFocus():
            focus = QColor(theme.ACCENT)
            focus.setAlphaF(0.5)
            p.setPen(focus)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(x - 2, y - 2, _W + 4, _H + 4), _H / 2 + 2, _H / 2 + 2)
        p.end()

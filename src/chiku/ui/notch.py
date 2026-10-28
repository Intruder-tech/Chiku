"""Notch: minimised Chiku as a small pill hanging from the top edge of a screen.

Collapsed it shows the mascot, live state, a level meter and the mic. Hover or click to expand
it with the last reply and quick actions. Drag it anywhere, including to another monitor: on
release it snaps to the top edge of the screen under the cursor, and its position is
remembered per screen. When a monitor is unplugged it moves to one that is still there.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QEnterEvent, QGuiApplication, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from chiku.assistant import State
from chiku.ui import theme
from chiku.ui.components import button, label
from chiku.ui.components.voice_bar import state_text
from chiku.ui.mascot import MascotWidget
from chiku.ui.waveform import Mode, Waveform

Screen = tuple[str, QRect]  # name, available geometry; the primary screen first

WIDTH = 400
COLLAPSED = 58
EXPANDED = 176
_RADIUS = 22
_DRAG_PX = 5  # a press that moves less than this is a click (expand / collapse)
_REPLY_CHARS = 260


def _top_left(geo: QRect, centre_x: float, width: int) -> QPoint:
    x = round(centre_x - width / 2)
    x = max(geo.left(), min(x, geo.left() + geo.width() - width))
    return QPoint(x, geo.top())


def _fraction(geo: QRect, top_left: QPoint, width: int) -> float:
    return round((top_left.x() + width / 2 - geo.left()) / geo.width(), 4)


def _distance2(geo: QRect, p: QPoint) -> int:
    dx = max(geo.left() - p.x(), 0, p.x() - (geo.left() + geo.width() - 1))
    dy = max(geo.top() - p.y(), 0, p.y() - (geo.top() + geo.height() - 1))
    return dx * dx + dy * dy


def placement(screens: list[Screen], positions: dict[str, float], preferred: str | None,
              width: int = WIDTH) -> tuple[str, QPoint]:
    """Where the notch goes: its last screen if still connected, else the primary screen, at
    the position remembered for that screen (centred the first time)."""
    by_name = dict(screens)
    name = preferred if preferred in by_name else screens[0][0]
    geo = by_name[name]
    frac = min(1.0, max(0.0, positions.get(name, 0.5)))
    return name, _top_left(geo, geo.left() + frac * geo.width(), width)


def snap(screens: list[Screen], cursor: QPoint, centre_x: float,
         width: int = WIDTH) -> tuple[str, QPoint, float]:
    """After a drag: the top edge of the screen under the cursor (or the nearest screen)."""
    name, geo = min(screens, key=lambda s: _distance2(s[1], cursor))
    pt = _top_left(geo, centre_x, width)
    return name, pt, _fraction(geo, pt, width)


def current_screens() -> list[Screen]:
    primary = QGuiApplication.primaryScreen()
    ordered = [primary] + [s for s in QGuiApplication.screens() if s is not primary]
    return [(s.name(), s.availableGeometry()) for s in ordered if s is not None]


class NotchWindow(QWidget):
    mic_toggled = Signal(bool)
    stop = Signal()
    open_full = Signal()
    open_card = Signal()
    hide_requested = Signal()
    moved = Signal(str, float)  # screen name, centre as a fraction of its width

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Chiku (notch)")
        self.positions: dict[str, float] = {}
        self.screen_name: str | None = None

        col = QVBoxLayout(self)
        col.setContentsMargins(16, 0, 14, 8)
        col.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.mascot = MascotWidget(30)
        row.addWidget(self.mascot)
        self.state = label("Getting ready…", "state")
        self.state.setFixedWidth(150)
        row.addWidget(self.state)
        self.wave = Waveform(bars=16, background="SURFACE")
        self.wave.setMinimumHeight(24)
        self.wave.setFixedSize(84, 24)
        row.addWidget(self.wave)
        self.mic = button("Mic", "notchMic")
        self.mic.setCheckable(True)
        self.mic.setToolTip("Microphone on/off (Ctrl+M in the full window)")
        self.mic.clicked.connect(lambda: self.mic_toggled.emit(self.mic.isChecked()))
        row.addWidget(self.mic)
        col.addLayout(row)

        self.panel = QWidget()
        pv = QVBoxLayout(self.panel)
        pv.setContentsMargins(4, 0, 4, 0)
        self.reply = label("", "notchReply", wrap=True)
        self.reply.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        pv.addWidget(self.reply, 1)
        actions = QHBoxLayout()
        for text, tip, signal in (("■ Stop", "Stop talking", self.stop),
                                  ("Open", "Open the full window", self.open_full),
                                  ("Card", "Switch to the floating card", self.open_card),
                                  ("Hide", "Hide; Chiku stays in the tray", self.hide_requested)):
            b = button(text, "ghost")
            b.setToolTip(tip)
            b.clicked.connect(signal.emit)
            actions.addWidget(b)
        pv.addLayout(actions)
        self.panel.hide()
        col.addWidget(self.panel, 1)

        self._expanded = False
        self._press: QPoint | None = None
        self._offset = QPoint()
        self._dragging = False
        self._hover = QTimer(self)
        self._hover.setSingleShot(True)
        self._hover.timeout.connect(self._hover_done)
        self._want_expanded = False
        self._full_state = ""
        self.setFixedSize(WIDTH, COLLAPSED)
        app = QGuiApplication.instance()
        if isinstance(app, QGuiApplication):
            app.screenAdded.connect(self._screens_changed)
            app.screenRemoved.connect(self._screens_changed)

    # -- content -------------------------------------------------------------------------------
    def set_state(self, state: State, detail: str) -> None:
        self._full_state = state_text(state, detail)
        self.state.setToolTip(self._full_state)
        self.state.setText(self.state.fontMetrics().elidedText(
            self._full_state, Qt.TextElideMode.ElideRight, self.state.width()))

    def set_reply(self, text: str) -> None:
        text = " ".join(text.split())
        if len(text) > _REPLY_CHARS:
            text = text[:_REPLY_CHARS - 1].rstrip() + "…"
        self.reply.setText(text)

    def tick(self, state: State, level: float, mic_on: bool, mode: Mode) -> None:
        self.mascot.update_state(state, level)
        self.wave.push(level, mode)
        if self.mic.isChecked() != mic_on:
            self.mic.setChecked(mic_on)

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, on: bool) -> None:
        if on == self._expanded:
            return
        self._expanded = on
        self.panel.setVisible(on)
        self.setFixedSize(WIDTH, EXPANDED if on else COLLAPSED)

    # -- position --------------------------------------------------------------------------------
    def place(self) -> None:
        """Move to the remembered spot on the remembered (or primary) screen."""
        screens = current_screens()
        if not screens:
            return
        self.screen_name, pt = placement(screens, self.positions, self.screen_name)
        self.move(pt)

    def snap_to(self, cursor: QPoint) -> None:
        screens = current_screens()
        if not screens:
            return
        name, pt, frac = snap(screens, cursor, self.x() + WIDTH / 2)
        self.move(pt)
        self.screen_name = name
        self.positions[name] = frac
        self.moved.emit(name, frac)

    def _screens_changed(self, _screen: object = None) -> None:
        if self.isVisible():
            # Deferred: Qt reports the change before the remaining screens' geometry settles.
            QTimer.singleShot(300, self.place)

    # -- mouse -----------------------------------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._offset = self._press - self.pos()
            self._dragging = False

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._press is None:
            return
        pos = e.globalPosition().toPoint()
        if not self._dragging and (pos - self._press).manhattanLength() >= _DRAG_PX:
            self._dragging = True
            self._hover.stop()
            self.set_expanded(False)
        if self._dragging:
            self.move(pos - self._offset)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._press is None:
            return
        if self._dragging:
            self.snap_to(e.globalPosition().toPoint())
        else:
            self.set_expanded(not self._expanded)
        self._press = None
        self._dragging = False

    def mouseDoubleClickEvent(self, _e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        self.open_full.emit()

    def enterEvent(self, _e: QEnterEvent) -> None:  # noqa: N802 - Qt override
        self._want_expanded = True
        self._hover.start(350)

    def leaveEvent(self, _e: object) -> None:  # noqa: N802 - Qt override
        self._want_expanded = False
        if not self._dragging:
            self._hover.start(700)

    def _hover_done(self) -> None:
        if not self._dragging:
            self.set_expanded(self._want_expanded)

    # -- look --------------------------------------------------------------------------------------
    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Rounded only at the bottom: the top corners are pushed above the window edge, so the
        # pill looks attached to the top of the screen.
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, -_RADIUS, self.width() - 1, self.height() + _RADIUS - 0.5),
                            _RADIUS, _RADIUS)
        p.setPen(QColor(theme.BORDER))
        p.setBrush(QColor(theme.SURFACE))
        p.drawPath(path)
        p.end()

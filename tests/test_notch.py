"""Notch: placement and snapping across screens (pure functions), and the widget offscreen."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QRect  # noqa: E402

from chiku.ui.notch import WIDTH, placement, snap  # noqa: E402

from .test_ui import app  # noqa: E402, F401  (the shared, cleanly torn down QApplication)

# A laptop (primary) and a bigger monitor to its right, mounted higher.
LAPTOP = ("\\\\.\\DISPLAY1", QRect(0, 0, 1920, 1040))
MONITOR = ("\\\\.\\DISPLAY2", QRect(1920, -200, 2560, 1400))
SCREENS = [LAPTOP, MONITOR]


def test_first_time_is_centred_on_the_primary_screen() -> None:
    assert placement(SCREENS, {}, None) == (LAPTOP[0], QPoint(960 - WIDTH // 2, 0))


def test_remembered_screen_and_position() -> None:
    name, pt = placement(SCREENS, {MONITOR[0]: 0.25}, MONITOR[0])
    assert name == MONITOR[0]
    assert pt == QPoint(1920 + 640 - WIDTH // 2, -200)  # top edge of that screen


def test_unplugged_screen_falls_back_to_primary() -> None:
    name, pt = placement([LAPTOP], {MONITOR[0]: 0.25, LAPTOP[0]: 0.1}, MONITOR[0])
    assert name == LAPTOP[0] and pt == QPoint(0, 0)  # 0.1 would overhang: clamped to the edge


def test_snap_to_the_screen_under_the_cursor() -> None:
    name, pt, frac = snap(SCREENS, QPoint(3000, 500), centre_x=3000)
    assert name == MONITOR[0]
    assert pt == QPoint(3000 - WIDTH // 2, -200)
    assert frac == pytest.approx((3000 - 1920) / 2560, abs=1e-4)


def test_snap_clamps_inside_the_screen() -> None:
    name, pt, frac = snap(SCREENS, QPoint(1910, 300), centre_x=1915)  # straddling the border
    assert name == LAPTOP[0] and pt == QPoint(1920 - WIDTH, 0)
    assert 0 < frac < 1


def test_snap_from_outside_every_screen_picks_the_nearest() -> None:
    name, pt, _ = snap(SCREENS, QPoint(5000, -500), centre_x=5000)
    assert name == MONITOR[0] and pt == QPoint(1920 + 2560 - WIDTH, -200)


@pytest.mark.usefixtures("app")
def test_notch_widget() -> None:
    from chiku.assistant import State
    from chiku.ui.notch import COLLAPSED, EXPANDED, NotchWindow

    n = NotchWindow()
    n.set_state(State.SPEAKING, "")
    assert n.state.toolTip().startswith("Speaking")
    assert n.state.fontMetrics().horizontalAdvance(n.state.text()) <= n.state.width()
    n.set_reply("word " * 200)
    assert len(n.reply.text()) <= 260 and n.reply.text().endswith("…")
    n.tick(State.LISTENING, 0.05, True, "input")
    assert n.mic.isChecked()
    assert n.height() == COLLAPSED and n.panel.isHidden()
    n.set_expanded(True)
    assert n.height() == EXPANDED and not n.panel.isHidden()
    n.set_expanded(False)

    n.place()  # offscreen platform: one screen
    screen = n.screen().availableGeometry()
    assert n.y() == screen.top()
    moves: list[tuple[str, float]] = []
    n.moved.connect(lambda s, f: moves.append((s, f)))
    n.move(screen.left() + 10, screen.top() + 300)  # dragged down and left
    n.snap_to(QPoint(screen.left() + 20, screen.top() + 310))
    assert n.y() == screen.top() and n.x() == screen.left() + 10  # same x, back on the top
    assert moves and moves[0][0] == n.screen_name and n.positions[n.screen_name] == moves[0][1]
    n.close()
    n.deleteLater()

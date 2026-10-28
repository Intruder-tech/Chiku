"""Command palette (Ctrl+K): type to find a page, action, setting, conversation or app."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

MAX_RESULTS = 60


@dataclass
class Command:
    title: str
    group: str  # "Page", "Action", "Settings", "Chat", "App"
    run: Callable[[], None]
    keywords: str = ""
    hidden_until_typed: bool = False  # e.g. apps: hundreds of them


def rank(commands: list[Command], query: str) -> list[Command]:
    """Commands containing every typed word, best first: title starts with the query, then a
    title word starts with it, then anywhere. Ties keep their original order."""
    words = query.lower().split()
    if not words:
        return [c for c in commands if not c.hidden_until_typed][:MAX_RESULTS]
    q = " ".join(words)
    scored: list[tuple[int, int, Command]] = []
    for i, c in enumerate(commands):
        title = c.title.lower()
        haystack = f"{title} {c.group.lower()} {c.keywords.lower()}"
        if not all(w in haystack for w in words):
            continue
        if title.startswith(q):
            score = 0
        elif any(t.startswith(words[0]) for t in title.split()):
            score = 1
        else:
            score = 2
        scored.append((score, i, c))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _s, _i, c in scored[:MAX_RESULTS]]


class _Search(QLineEdit):
    def __init__(self, owner: CommandPalette):
        super().__init__()
        self.owner = owner

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self.owner.move_selection(e.key())
            return
        super().keyPressEvent(e)


class CommandPalette(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("palette")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.commands: list[Command] = []
        self.shown: list[Command] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.search = _Search(self)
        self.search.setPlaceholderText("Type a page, action, setting, chat or app…")
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self.run_selected)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.itemActivated.connect(lambda _i: self.run_selected())
        lay.addWidget(self.list, 1)
        self.resize(580, 420)

    def open_with(self, commands: list[Command], parent: QWidget) -> None:
        self.commands = commands
        self.search.clear()
        self._refresh()
        geo = parent.frameGeometry()
        self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + 90)
        self.show()
        self.raise_()
        self.search.setFocus()

    def _refresh(self) -> None:
        self.shown = rank(self.commands, self.search.text())
        self.list.clear()
        for c in self.shown:
            self.list.addItem(QListWidgetItem(f"{c.title}    ·  {c.group}"))
        if self.shown:
            self.list.setCurrentRow(0)

    def move_selection(self, key: int) -> None:
        n = self.list.count()
        if not n:
            return
        step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 8,
                Qt.Key.Key_PageUp: -8}.get(Qt.Key(key), 0)
        self.list.setCurrentRow(max(0, min(n - 1, self.list.currentRow() + step)))

    def run_selected(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.shown):
            cmd = self.shown[row]
            self.close()
            QTimer.singleShot(0, cmd.run)  # after the popup is gone (dialogs, focus)

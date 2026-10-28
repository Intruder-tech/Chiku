"""Chat transcript: bubbles with timestamps, provenance, sources, tool lines and notices."""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayoutItem,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from chiku.assistant import Message, ToolActivity
from chiku.branding import AVATAR, asset
from chiku.tools.web import UrlRejected, validate_url
from chiku.ui import theme
from chiku.ui.components import label, named


def _dispose(item: QLayoutItem | None) -> None:
    if item is None:
        return
    w = item.widget()
    if w is not None:
        w.hide()  # gone now; the actual delete waits for the event loop
        w.deleteLater()
        return
    lay = item.layout()
    if lay is not None:
        while lay.count():
            _dispose(lay.takeAt(0))
        lay.deleteLater()


class Bubble(QFrame):
    def __init__(self, role: str, source: str = "", stamp: str = ""):
        super().__init__()
        self.role = role
        self.setObjectName("userBubble" if role == "user" else "assistantBubble")
        lay = self.body = QVBoxLayout(self)
        if role == "user":
            lay.setContentsMargins(14, 9, 14, 7)
        else:  # Chiku's replies: plain text, no bubble
            lay.setContentsMargins(0, 3, 0, 0)
        lay.setSpacing(3)
        self.text = label("", "bubbleText", wrap=True)
        self.text.setTextFormat(Qt.TextFormat.PlainText)  # model output is never HTML
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.text)
        stamp = stamp or datetime.now().strftime("%H:%M")
        self.meta = label(stamp if role == "user" else f"Chiku · {stamp} · {source}", "time")
        self.meta.setAlignment(Qt.AlignmentFlag.AlignRight if role == "user"
                               else Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.meta)
        self.sources_added = False

    def set_text(self, text: str) -> None:
        self.text.setText(text or "…")
        if self.role == "user":
            # A wrapping label in a shrink-to-fit bubble collapses to a narrow column; give it
            # the width of its longest line (up to the bubble's maximum) instead.
            self.text.ensurePolished()  # measure with the stylesheet's chat font size
            fm = self.text.fontMetrics()
            longest = max((fm.horizontalAdvance(line) for line in (text or "…").splitlines()),
                          default=0)
            self.text.setMinimumWidth(min(longest + 8, 560))

    def add_sources(self, sources: list[dict[str, str]], on_link: Callable[[str], None]) -> None:
        if self.sources_added or not sources:
            return
        self.sources_added = True
        rows: list[str] = []
        for s in sources[:8]:
            try:
                url = validate_url(s.get("url", ""))
            except UrlRejected:
                continue
            host = urlsplit(url).hostname or ""
            rows.append(f'{len(rows) + 1}. <a href="{html.escape(url, quote=True)}" '
                        f'style="color:{theme.ACCENT}; text-decoration:none">'
                        f'{html.escape(s.get("title", host)[:90])}</a> '
                        f'<span style="color:{theme.FAINT}">· {html.escape(host)}</span>')
        src = label("<br>".join(rows), "sources", wrap=True)
        src.setTextFormat(Qt.TextFormat.RichText)
        src.setOpenExternalLinks(False)
        src.linkActivated.connect(on_link)  # opens through the permissioned open_url tool
        self.body.insertWidget(1, src)
        self.meta.setText(self.meta.text() + " · with web sources")


class ChatView(QScrollArea):
    """The transcript: a centred reading column. Chiku's replies are plain text beside her
    avatar; yours are bubbles on the right. Emits `started` when the first message arrives."""

    started = Signal()
    COLUMN_WIDTH = 780

    def __init__(self, on_link: Callable[[str], None] = lambda _u: None):
        super().__init__()
        self.on_link = on_link
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = named(QWidget(), "page")
        outer = QHBoxLayout(holder)
        outer.setContentsMargins(16, 12, 16, 12)
        inner = QWidget()
        inner.setMaximumWidth(self.COLUMN_WIDTH)
        self.column = QVBoxLayout(inner)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(14)
        self.column.addStretch()
        outer.addStretch(1)
        outer.addWidget(inner, 100)
        outer.addStretch(1)
        self.setWidget(holder)
        self.verticalScrollBar().rangeChanged.connect(
            lambda _a, mx: self.verticalScrollBar().setValue(mx))
        self.bubbles: dict[int, Bubble] = {}
        self._avatar = QPixmap(str(asset(AVATAR))).scaled(
            28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    @property
    def is_empty(self) -> bool:
        return self.column.count() <= 1  # only the stretch

    def _add_row(self, row: QHBoxLayout | QWidget) -> None:
        was_empty = self.is_empty
        if isinstance(row, QWidget):
            self.column.insertWidget(self.column.count() - 1, row)
        else:
            self.column.insertLayout(self.column.count() - 1, row)
        if was_empty:
            self.started.emit()

    def _bubble(self, role: str, source: str, stamp: str = "") -> Bubble:
        b = Bubble(role, source, stamp)
        row = QHBoxLayout()
        row.setSpacing(10)
        if role == "user":
            b.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
            b.setMaximumWidth(int(self.COLUMN_WIDTH * 0.8))
            row.addStretch(1)
            row.addWidget(b)
        else:
            avatar = QLabel()
            avatar.setPixmap(self._avatar)
            avatar.setFixedSize(30, 30)
            avatar.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
            b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            row.addWidget(b, 1)
        self._add_row(row)
        return b

    def show_message(self, ev: Message) -> None:
        if ev.role == "notice":
            self.add_line(ev.text, "notice")
            return
        b = self.bubbles.get(ev.id)
        if b is None:
            b = self.bubbles[ev.id] = self._bubble(ev.role, ev.source)
        b.set_text(ev.text)
        if ev.final and ev.sources:
            b.add_sources(ev.sources, self.on_link)

    def add_entry(self, role: str, text: str, source: str = "",
                  sources: list[dict[str, str]] | None = None, stamp: str = "") -> None:
        """Show a message from a saved conversation."""
        b = self._bubble(role, source, stamp)
        b.set_text(text)
        if sources:
            b.add_sources(sources, self.on_link)

    def clear(self) -> None:
        """Empty the transcript (new or switched conversation)."""
        col = self.column
        while col.count() > 1:  # keep the stretch (last)
            _dispose(col.takeAt(0))
        self.bubbles.clear()

    def show_tool(self, ev: ToolActivity) -> None:
        if ev.status != "running":
            self.add_line(f"{'✓' if ev.status == 'ok' else '✕'} {ev.name} · {ev.message}", "tool")

    def add_line(self, text: str, kind: str) -> None:
        lab = label(text, kind, wrap=True)
        lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setContentsMargins(40, 0, 0, 0)  # aligned with Chiku's text, past her avatar
        self._add_row(lab)

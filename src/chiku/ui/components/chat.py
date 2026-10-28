"""Chat transcript: bubbles with timestamps, provenance, sources, tool lines and notices."""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from chiku.assistant import Message, ToolActivity
from chiku.branding import WELCOME, asset
from chiku.tools.web import UrlRejected, validate_url
from chiku.ui import theme
from chiku.ui.components import label, named


class Bubble(QFrame):
    def __init__(self, role: str, source: str = ""):
        super().__init__()
        self.role = role
        self.setObjectName("userBubble" if role == "user" else "assistantBubble")
        lay = self.body = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 6)
        lay.setSpacing(3)
        self.text = label("", "bubbleText", wrap=True)
        self.text.setTextFormat(Qt.TextFormat.PlainText)  # model output is never HTML
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.text)
        stamp = datetime.now().strftime("%H:%M")
        self.meta = label(stamp if role == "user" else f"{stamp} · {source}", "time")
        self.meta.setAlignment(Qt.AlignmentFlag.AlignRight if role == "user"
                               else Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.meta)
        self.sources_added = False

    def set_text(self, text: str) -> None:
        self.text.setText(text or "…")

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
    def __init__(self, on_link: Callable[[str], None] = lambda _u: None):
        super().__init__()
        self.on_link = on_link
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = named(QWidget(), "page")
        self.column = QVBoxLayout(holder)
        self.column.setContentsMargins(16, 8, 16, 8)
        self.column.setSpacing(10)
        self.column.addStretch()
        self.setWidget(holder)
        self.verticalScrollBar().rangeChanged.connect(
            lambda _a, mx: self.verticalScrollBar().setValue(mx))
        self.bubbles: dict[int, Bubble] = {}
        self.welcome = self._welcome()
        self.column.insertWidget(0, self.welcome)

    def _welcome(self) -> QWidget:
        card = named(QWidget(), "welcome")
        lay = QVBoxLayout(card)
        img = QLabel()
        img.setPixmap(QPixmap(str(asset(WELCOME))).scaled(
            170, 170, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        img.setFixedSize(170, 170)
        lay.addWidget(img, 0, Qt.AlignmentFlag.AlignHCenter)
        hi = label("Hi, I'm Chiku! Press the mic and talk to me, or type below.\nTry “open "
                   "Notepad”, “what's the weather tomorrow?”, “install VLC” or “remember "
                   "that I like jazz”.", "notice", wrap=True)
        hi.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(hi)
        return card

    def _insert(self, w: QWidget, align_right: bool = False) -> None:
        row = QHBoxLayout()
        if align_right:
            row.addStretch(1)
        row.addWidget(w, 0 if align_right else 1)
        if not align_right:
            row.addStretch(0)
        self.column.insertLayout(self.column.count() - 1, row)

    def show_message(self, ev: Message) -> None:
        if ev.role == "notice":
            self.add_line(ev.text, "notice")
            return
        self.welcome.hide()
        b = self.bubbles.get(ev.id)
        if b is None:
            b = Bubble(ev.role, ev.source)
            b.setSizePolicy(QSizePolicy.Policy.Maximum if ev.role == "user"
                            else QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            b.setMaximumWidth(max(260, int(self.viewport().width() * 0.82)))
            self.bubbles[ev.id] = b
            self._insert(b, align_right=ev.role == "user")
        b.set_text(ev.text)
        if ev.final and ev.sources:
            b.add_sources(ev.sources, self.on_link)

    def show_tool(self, ev: ToolActivity) -> None:
        if ev.status != "running":
            self.add_line(f"{'✓' if ev.status == 'ok' else '✕'} {ev.name} · {ev.message}", "tool")

    def add_line(self, text: str, kind: str) -> None:
        lab = label(text, kind, wrap=True)
        lab.setTextFormat(Qt.TextFormat.PlainText)
        self.column.insertWidget(self.column.count() - 1, lab)

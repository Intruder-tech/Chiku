"""Right panel of the workspace: live voice and mascot, sources of the last answer, and the
latest actions. Everything shown comes from real engine events."""

from __future__ import annotations

import html
from collections import deque
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout

from chiku.assistant import ToolActivity
from chiku.tools.web import UrlRejected, validate_url
from chiku.ui import theme
from chiku.ui.components import label
from chiku.ui.components.voice_bar import VoiceBar

MAX_ACTIONS = 6


class ContextPanel(QFrame):
    def __init__(self, on_link: Callable[[str], None] = lambda _u: None):
        super().__init__()
        self.setObjectName("context")
        self.setFixedWidth(290)
        self.on_link = on_link
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 12, 12, 12)
        lay.setSpacing(6)
        self.voice = VoiceBar(mascot_size=120, vertical=True)
        lay.addWidget(self.voice)
        lay.addSpacing(6)
        self.sources_title = label("Sources", "sectionTitle")
        self.sources = label("", "sources", wrap=True)
        self.sources.setTextFormat(Qt.TextFormat.RichText)
        self.sources.setOpenExternalLinks(False)
        self.sources.linkActivated.connect(lambda url: self.on_link(url))
        for w in (self.sources_title, self.sources):
            lay.addWidget(w)
            w.hide()
        lay.addSpacing(6)
        lay.addWidget(label("Recent actions", "sectionTitle"))
        self.recent_label = label("Nothing yet. Things Chiku does on this PC show up here.",
                                  "tool", wrap=True)
        self.recent_label.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.recent_label)
        lay.addStretch()
        self._recent: deque[str] = deque(maxlen=MAX_ACTIONS)

    def set_sources(self, sources: list[dict[str, str]]) -> None:
        rows: list[str] = []
        for s in sources[:6]:
            try:
                url = validate_url(s.get("url", ""))
            except UrlRejected:
                continue
            host = urlsplit(url).hostname or ""
            rows.append(f'<a href="{html.escape(url, quote=True)}" '
                        f'style="color:{theme.ACCENT}; text-decoration:none">'
                        f'{html.escape(s.get("title", host)[:70])}</a><br>'
                        f'<span style="color:{theme.FAINT}">{html.escape(host)}</span>')
        self.sources.setText("<br>".join(rows))
        for w in (self.sources_title, self.sources):
            w.setVisible(bool(rows))

    def add_tool(self, ev: ToolActivity) -> None:
        if ev.status == "running":
            return
        mark = "✓" if ev.status == "ok" else "✕"
        self._recent.appendleft(f"{datetime.now():%H:%M} {mark} {ev.name}: {ev.message}"[:160])
        self.recent_label.setText("\n".join(self._recent))

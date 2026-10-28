"""Left navigation: mascot, pages, compact-mode button."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QVBoxLayout

from chiku.branding import AVATAR, asset
from chiku.ui.components import button, label

PAGES = (("chat", "Chat"), ("memory", "Memory"), ("activity", "Activity"),
         ("settings", "Settings"))


class Sidebar(QFrame):
    page_selected = Signal(str)
    compact_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(168)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 16, 12, 12)
        lay.setSpacing(4)
        avatar = QLabel()
        avatar.setPixmap(QPixmap(str(asset(AVATAR))).scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        avatar.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(avatar)
        word = label("chiku", "wordmark")
        word.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(word)
        lay.addSpacing(14)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key, text in PAGES:
            b = button(text, "nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self.page_selected.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        self.buttons["chat"].setChecked(True)
        lay.addStretch()
        compact = button("Compact mode", "ghost")
        compact.setToolTip("Small floating Chiku that stays on top (Ctrl+Shift+Space)")
        compact.clicked.connect(self.compact_requested.emit)
        lay.addWidget(compact)
        self.badge = label("", "badge", wrap=True)
        lay.addWidget(self.badge)

    def select(self, key: str) -> None:
        self.buttons[key].setChecked(True)

    def set_provider(self, text: str) -> None:
        self.badge.setText(text if len(text) <= 40 else text[:39] + "…")
        self.badge.setToolTip(f"{text}\nWhere replies are generated. 'local' means on this PC.")

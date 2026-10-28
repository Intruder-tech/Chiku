"""Left pane: mascot, pages, conversations, minimise button and where replies come from."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
)

from chiku.branding import AVATAR, asset
from chiku.ui.components import button, label

PAGES = (("chat", "Chat"), ("profile", "Profile"), ("apps", "Apps"), ("memory", "Memory"),
         ("activity", "Activity"), ("settings", "Settings"))


class Sidebar(QFrame):
    page_selected = Signal(str)
    compact_requested = Signal()
    new_chat = Signal()
    conversation_selected = Signal(str)
    conversation_deleted = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(210)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(3)
        head = QHBoxLayout()
        avatar = QLabel()
        avatar.setPixmap(QPixmap(str(asset(AVATAR))).scaled(
            40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        head.addWidget(avatar)
        head.addWidget(label("chiku", "wordmark"), 1)
        lay.addLayout(head)
        hint = label("Ctrl+K: search everything", "status")
        lay.addWidget(hint)
        lay.addSpacing(8)
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
        lay.addSpacing(10)
        row = QHBoxLayout()
        row.addWidget(label("Chats", "sectionTitle"), 1)
        new = button("+ New", "ghost")
        new.setToolTip("Start a new conversation (Ctrl+N)")
        new.clicked.connect(self.new_chat.emit)
        row.addWidget(new)
        lay.addLayout(row)
        self.chats = QListWidget()
        self.chats.setObjectName("chats")
        self.chats.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chats.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.chats.itemClicked.connect(
            lambda item: self.conversation_selected.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.chats.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chats.customContextMenuRequested.connect(self._chat_menu)
        lay.addWidget(self.chats, 1)
        compact = button("Minimise", "ghost")
        compact.setToolTip("Notch at the top of the screen or a floating card, as chosen in "
                           "Settings → Persona (Ctrl+Shift+Space)")
        compact.clicked.connect(self.compact_requested.emit)
        lay.addWidget(compact)
        self.badge = label("", "badge", wrap=True)
        lay.addWidget(self.badge)

    def select(self, key: str) -> None:
        self.buttons[key].setChecked(True)

    def set_provider(self, text: str) -> None:
        self.badge.setText(text if len(text) <= 40 else text[:39] + "…")
        self.badge.setToolTip(f"{text}\nWhere replies are generated. 'local' means on this PC.")

    def set_conversations(self, items: list[tuple[str, str]], current: str) -> None:
        """items: (id, title), most recent first."""
        self.chats.blockSignals(True)
        self.chats.clear()
        for conv_id, title in items:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, conv_id)
            item.setToolTip(title)
            self.chats.addItem(item)
            if conv_id == current:
                item.setSelected(True)
                self.chats.setCurrentItem(item)
        self.chats.blockSignals(False)

    def _chat_menu(self, pos: QPoint) -> None:
        item = self.chats.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        delete = QAction("Delete this chat", menu)
        delete.triggered.connect(
            lambda: self.conversation_deleted.emit(item.data(Qt.ItemDataRole.UserRole)))
        menu.addAction(delete)
        menu.exec(self.chats.mapToGlobal(pos))

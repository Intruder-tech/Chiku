"""Left pane: new chat, search, pages, recent chats, and you (with the theme switch)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from chiku.branding import AVATAR, asset
from chiku.ui import theme
from chiku.ui.components import button, icons, label
from chiku.ui.components.input_bar import icon_button

PAGES = (("chat", "Chat"), ("profile", "Profile"), ("apps", "Apps"), ("memory", "Memory"),
         ("activity", "Activity"), ("settings", "Settings"))
_ICONS = {"chat": icons.CHAT, "profile": icons.PERSON, "apps": icons.APPS,
          "memory": icons.MEMORY, "activity": icons.ACTIVITY, "settings": icons.SETTINGS}


def avatar_icon(name: str, picture: Path | None, size: int = 28) -> QIcon:
    """The user's picture cut into a circle, or their initials on the accent colour."""
    scale = 2
    px = size * scale
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, px, px))
    img = QImage(str(picture)) if picture is not None and picture.exists() else QImage()
    if not img.isNull():
        p.setClipPath(path)
        p.drawImage(QRectF(0, 0, px, px), img)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawPath(path)
        font = QFont()
        font.setPixelSize(int(px * 0.42))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(theme.ACCENT_TEXT))
        letters = "".join(w[0] for w in name.split()[:2]).upper() or "?"
        p.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, letters)
    p.end()
    out.setDevicePixelRatio(scale)
    return QIcon(out)


class Sidebar(QFrame):
    page_selected = Signal(str)
    compact_requested = Signal()
    new_chat = Signal()
    conversation_selected = Signal(str)
    conversation_deleted = Signal(str)
    theme_toggled = Signal()
    search_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(248)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 14, 12, 10)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(asset(AVATAR))).scaled(
            30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        head.addWidget(logo)
        head.addWidget(label("Chiku", "wordmark"), 1)
        self.minimise = icon_button(icons.MINIMISE, "Minimise to the notch or card "
                                    "(Ctrl+Shift+Space)")
        self.minimise.clicked.connect(self.compact_requested.emit)
        head.addWidget(self.minimise)
        lay.addLayout(head)
        lay.addSpacing(12)

        self.new_button = button("New chat", "newChat")
        self.new_button.setToolTip("Start a new conversation (Ctrl+N)")
        self.new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_button.clicked.connect(self.new_chat.emit)
        lay.addWidget(self.new_button)
        self.search_button = button("Search   Ctrl+K", "nav")
        self.search_button.setToolTip("Find a page, setting, chat or app (Ctrl+K)")
        self.search_button.clicked.connect(self.search_requested.emit)
        lay.addWidget(self.search_button)
        lay.addSpacing(8)

        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in PAGES:
            b = button(text, "nav")
            b.setCheckable(True)
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.page_selected.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        self.buttons["chat"].setChecked(True)
        lay.addSpacing(14)
        lay.addWidget(label("Recents", "sectionTitle"))
        self.chats = QListWidget()
        self.chats.setObjectName("chats")
        self.chats.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chats.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.chats.itemClicked.connect(
            lambda item: self.conversation_selected.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.chats.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chats.customContextMenuRequested.connect(self._chat_menu)
        lay.addWidget(self.chats, 1)

        foot = QHBoxLayout()
        foot.setSpacing(4)
        self.user = button("You", "userChip")
        self.user.setIconSize(QSize(28, 28))
        self.user.setToolTip("Your profile")
        self.user.setCursor(Qt.CursorShape.PointingHandCursor)
        self.user.clicked.connect(lambda: self.page_selected.emit("profile"))
        foot.addWidget(self.user, 1)
        self.theme_button = icon_button(icons.MOON, "Switch between light and dark")
        self.theme_button.clicked.connect(self.theme_toggled.emit)
        foot.addWidget(self.theme_button)
        lay.addLayout(foot)
        self.badge = label("", "badge", wrap=True)
        lay.addWidget(self.badge)
        self._user_name = ""
        self._picture: Path | None = None
        self.refresh_icons()

    def refresh_icons(self) -> None:
        """Re-draw icons in the current theme's colours (after a theme switch)."""
        for key, b in self.buttons.items():
            b.setIcon(icons.glyph_icon(_ICONS[key], theme.MUTED))
        self.new_button.setIcon(icons.glyph_icon(icons.ADD, theme.TEXT, 16))
        self.search_button.setIcon(icons.glyph_icon(icons.SEARCH, theme.MUTED))
        self.minimise.setIcon(icons.glyph_icon(icons.MINIMISE, theme.MUTED, 16))
        dark = theme.current_mode == "dark"
        self.theme_button.setIcon(icons.glyph_icon(icons.SUN if dark else icons.MOON,
                                                   theme.MUTED))
        self.theme_button.setToolTip("Switch to light" if dark else "Switch to dark")
        self.user.setIcon(avatar_icon(self._user_name, self._picture))

    def set_user(self, name: str | None, picture: Path | None) -> None:
        self._user_name = name or ""
        self._picture = picture
        self.user.setText(name or "You")
        self.user.setIcon(avatar_icon(self._user_name, picture))

    def select(self, key: str) -> None:
        self.buttons[key].setChecked(True)

    def set_provider(self, text: str) -> None:
        self.badge.setText(text if len(text) <= 44 else text[:43] + "…")
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

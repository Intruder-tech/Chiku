"""Embeds the existing dialogs (Memory, Activity, Settings) as sidebar pages."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from chiku.ui.components import named
from chiku.ui.components.page import PageHeader


class DialogPage(QWidget):
    """Hosts a QDialog inline. When it closes (Save/Cancel/Close) a fresh one replaces it, so
    the page always shows current data; `on_accept` runs after a Save."""

    def __init__(self, title: str, factory: Callable[[QWidget], QDialog],
                 on_accept: Callable[[], None] = lambda: None, subtitle: str = "",
                 margins: tuple[int, int, int, int] = (32, 26, 32, 18)):
        super().__init__()
        self.setObjectName("page")
        self.factory = factory
        self.on_accept = on_accept
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        if title:
            self.lay.addWidget(PageHeader(title, subtitle))
        self.dialog: QDialog | None = None
        self.refresh()

    def refresh(self) -> None:
        if self.dialog is not None:
            old = self.dialog
            self.lay.removeWidget(old)
            old.hide()  # gone now; the actual delete waits for the event loop
            old.deleteLater()
        d = self.factory(self)
        d.setWindowFlags(Qt.WindowType.Widget)
        named(d, "page")
        d.finished.connect(self._finished)
        self.lay.addWidget(d, 1)
        d.show()
        self.dialog = d

    def _finished(self, result: int) -> None:
        if result == QDialog.DialogCode.Accepted:
            self.on_accept()
        self.refresh()

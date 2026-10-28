"""Embeds the existing dialogs (Memory, Activity, Settings) as sidebar pages."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from chiku.ui.components import label, named


class DialogPage(QWidget):
    """Hosts a QDialog inline. When it closes (Save/Cancel/Close) a fresh one replaces it, so
    the page always shows current data; `on_accept` runs after a Save."""

    def __init__(self, title: str, factory: Callable[[QWidget], QDialog],
                 on_accept: Callable[[], None] = lambda: None):
        super().__init__()
        self.setObjectName("page")
        self.factory = factory
        self.on_accept = on_accept
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 16, 20, 12)
        self.lay.addWidget(label(title, "pageTitle"))
        self.dialog: QDialog | None = None
        self.refresh()

    def refresh(self) -> None:
        if self.dialog is not None:
            self.dialog.deleteLater()
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

"""Inspect, add, edit, delete and export what Chiku remembers."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chiku.memory import CATEGORIES, MemoryItem, MemoryRejected, MemoryStore


def _local(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%d %b %Y %H:%M")
    except ValueError:
        return ts


class MemoryDialog(QDialog):
    def __init__(self, store: MemoryStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("What Chiku remembers")
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        intro = QLabel("Chiku only keeps things you asked her to remember or approved. "
                       "Stored on this computer only (memory.db). Double-click to edit.")
        intro.setObjectName("notice")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Memory", "Type", "Updated", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._edit())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in (("Add…", self._add), ("Edit…", self._edit), ("Delete", self._delete),
                           ("Export…", self._export), ("Forget everything", self._clear)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self._items: list[MemoryItem] = []
        self.refresh()

    def refresh(self) -> None:
        self._items = self.store.all()
        self.table.setRowCount(len(self._items))
        for r, it in enumerate(self._items):
            for c, val in enumerate((it.text, it.category, _local(it.updated), it.source)):
                cell = QTableWidgetItem(val)
                cell.setToolTip(val)
                self.table.setItem(r, c, cell)

    def _selected(self) -> MemoryItem | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self._items[min(rows)] if rows else None

    def _ask(self, title: str, text: str = "", category: str = "other") -> tuple[str, str] | None:
        value, ok = QInputDialog.getMultiLineText(self, title, "What should Chiku remember?",
                                                  text)
        if not ok or not value.strip():
            return None
        cats = list(CATEGORIES)
        cat, ok = QInputDialog.getItem(self, title, "Type", cats, cats.index(category)
                                       if category in cats else 3, False)
        return (value, cat) if ok else None

    def _add(self) -> None:
        got = self._ask("Add memory")
        if got:
            try:
                self.store.add(got[0], got[1], source="added in settings")
            except MemoryRejected as exc:
                QMessageBox.warning(self, "Chiku", str(exc))
            self.refresh()

    def _edit(self) -> None:
        it = self._selected()
        if it is None:
            return
        got = self._ask("Edit memory", it.text, it.category)
        if got:
            try:
                self.store.update(it.id, got[0], got[1])
            except MemoryRejected as exc:
                QMessageBox.warning(self, "Chiku", str(exc))
            self.refresh()

    def _delete(self) -> None:
        it = self._selected()
        if it is not None:
            self.store.delete(it.id)
            self.refresh()

    def _clear(self) -> None:
        if not self._items:
            return
        if QMessageBox.question(
            self, "Chiku", f"Permanently delete all {len(self._items)} memories?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.store.clear()
            self.refresh()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export memories", "chiku-memories.json",
                                              "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.store.export_json())


"""Action history: every tool Chiku ran, whether it was approved, and what happened."""

from __future__ import annotations

import json
import os
from datetime import datetime

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chiku.audit import AuditLog
from chiku.ui import theme
from chiku.ui.components.page import tidy_table

_APPROVAL = {"auto": "automatic", "user": "you approved", "denied": "blocked / declined"}


def _local(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%d %b %H:%M:%S")
    except ValueError:
        return ts


def summarize_args(args: object) -> str:
    if isinstance(args, dict):
        return ", ".join(f"{k}={v}" for k, v in args.items() if v not in (None, "", []))[:160]
    return json.dumps(args, ensure_ascii=False)[:160]


class ActivityDialog(QDialog):
    def __init__(self, audit: AuditLog, parent: QWidget | None = None):
        super().__init__(parent)
        self.audit = audit
        self.setWindowTitle("Chiku activity")
        self.resize(820, 480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        row = QHBoxLayout()
        for text, slot in (("Refresh", self.refresh), ("Open folder", self._open_folder)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        self.count_label = QLabel("")
        self.count_label.setObjectName("status")
        row.addWidget(self.count_label)
        clear = QPushButton("Clear history")
        clear.setObjectName("danger")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        lay.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["When", "Action", "Details", "Permission",
                                              "Result"])
        tidy_table(self.table)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for col, width in ((0, 130), (1, 150), (3, 130)):
            self.table.setColumnWidth(col, width)
        lay.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        rows = list(reversed(self.audit.tail(500)))
        self.count_label.setText(f"{len(rows)} action{'' if len(rows) == 1 else 's'}")
        self.table.setRowCount(len(rows))
        for r, e in enumerate(rows):
            result = ("✓ " if e.get("ok") else "✕ ") + str(e.get("message", ""))
            vals = [_local(str(e.get("ts", ""))), str(e.get("tool", "")),
                    summarize_args(e.get("args")),
                    _APPROVAL.get(str(e.get("approval")), str(e.get("approval"))), result]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setToolTip(v)
                if c == 4 and not e.get("ok"):
                    cell.setForeground(QColor(theme.ERROR))
                self.table.setItem(r, c, cell)

    def _open_folder(self) -> None:
        os.startfile(self.audit.path.parent)  # noqa: S606

    def _clear(self) -> None:
        if QMessageBox.question(
            self, "Chiku", "Delete the whole action history? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.audit.clear()
            self.refresh()

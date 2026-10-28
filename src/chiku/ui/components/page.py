"""Page building blocks shared by every page: header, section card, centred content column,
segmented control and tidy tables."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from chiku.ui.components import label, named

CONTENT_WIDTH = 860  # pages read best as a centred column, like the chat


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title, "pageTitle"))
        self.subtitle = label(subtitle, "pageSubtitle", wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        col.addWidget(self.subtitle)
        lay.addLayout(col, 1)
        self.action_row = QHBoxLayout()
        self.action_row.setSpacing(8)
        lay.addLayout(self.action_row)

    def add_action(self, widget: QWidget) -> None:
        self.action_row.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)


def card(title: str = "", description: str = "") -> tuple[QFrame, QVBoxLayout]:
    """A rounded section with an optional heading and one-line description."""
    frame = named(QFrame(), "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(20, 16, 20, 18)
    lay.setSpacing(10)
    if title:
        lay.addWidget(label(title, "cardTitle"))
    if description:
        lay.addWidget(label(description, "cardDescription", wrap=True))
    return frame, lay


def centred(widget: QWidget, max_width: int = CONTENT_WIDTH) -> QWidget:
    """`widget` in a column no wider than `max_width`, centred in the available space."""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    widget.setMaximumWidth(max_width)
    lay.addStretch(1)
    lay.addWidget(widget, 100)
    lay.addStretch(1)
    return holder


class Segmented(QWidget):
    """A row of mutually exclusive buttons (e.g. System / Light / Dark)."""

    changed = Signal(object)  # the chosen option's data

    def __init__(self, options: list[tuple[str, object]], current: object = None):
        super().__init__()
        self.setObjectName("segmented")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self._data: list[object] = []
        for text, data in options:
            b = QPushButton(text)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, len(self._data))
            self._data.append(data)
            lay.addWidget(b)
        self.setCurrentData(current if current in self._data else self._data[0])
        self.group.idClicked.connect(lambda i: self.changed.emit(self._data[i]))

    def currentData(self) -> object:  # noqa: N802 - mirrors QComboBox
        return self._data[max(0, self.group.checkedId())]

    def setCurrentData(self, data: object) -> None:  # noqa: N802 - mirrors QComboBox
        if data in self._data:
            button = self.group.button(self._data.index(data))
            if button is not None:
                button.setChecked(True)


def tidy_table(table: QTableWidget) -> None:
    """The clean list look used on every page: no grid, roomy rows, quiet header."""
    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(40)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    table.setWordWrap(False)
    table.setObjectName("listTable")


__all__ = ["CONTENT_WIDTH", "PageHeader", "QHeaderView", "Segmented", "card", "centred",
           "tidy_table"]

"""Apps page: installed apps, available updates, and installing new apps with winget.

Listing and searching are read-only winget calls. Install, update and uninstall go through the
same tools Chiku uses when you ask by voice, so each one shows the confirmation dialog with the
exact package and is written to the action log.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chiku.cancel import CancelToken
from chiku.tools.base import ToolResult
from chiku.tools.packages import Package, WingetError
from chiku.ui.components import button, label, named
from chiku.ui.components.page import PageHeader, tidy_table

RunTool = Callable[[str, dict[str, Any]], ToolResult]


class PackageSource(Protocol):
    def installed_all(self, cancel: CancelToken) -> list[Package]: ...

    def upgrades(self, cancel: CancelToken) -> list[Package]: ...

    def search(self, query: str, cancel: CancelToken, limit: int = 8) -> list[Package]: ...


class _Bridge(QObject):
    done = Signal(str, object)  # job name, list[Package] | ToolResult | Exception


class _PackageTable(QTableWidget):
    COLUMNS = ("Name", "Id", "Version", "Available", "Source")

    def __init__(self) -> None:
        super().__init__(0, len(self.COLUMNS))
        self.setHorizontalHeaderLabels(self.COLUMNS)
        tidy_table(self)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Ids and versions can be very long (ARP\Machine\X64\…): fixed widths the user can
        # drag, elided text, the full value on hover.
        for col, width in ((1, 200), (2, 110), (3, 100), (4, 70)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(col, width)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setWordWrap(False)
        self.packages: list[Package] = []

    def show_packages(self, packages: list[Package], text_filter: str = "") -> None:
        f = text_filter.lower().strip()
        self.packages = [p for p in packages
                         if not f or f in p.name.lower() or f in p.id.lower()]
        self.setRowCount(len(self.packages))
        for row, p in enumerate(self.packages):
            for col, value in enumerate((p.name, p.id, p.version, p.available, p.source)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.setItem(row, col, item)

    def selected(self) -> Package | None:
        rows = {i.row() for i in self.selectedIndexes()}
        return self.packages[rows.pop()] if len(rows) == 1 else None


class AppsPage(QWidget):
    def __init__(self, winget: PackageSource, run_tool: RunTool):
        super().__init__()
        self.setObjectName("page")
        self.winget = winget
        self.run_tool = run_tool
        self._bridge = _Bridge()
        self._bridge.done.connect(self._finished)
        self._busy: set[str] = set()
        self._quiet: set[str] = set()  # refreshes that keep the last result message visible
        self._cancel = CancelToken()
        self._installed: list[Package] = []
        self.loaded = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 18)
        lay.setSpacing(12)
        lay.addWidget(PageHeader("Apps", "Install, update and remove apps with winget's official "
                                 "sources. Every change asks you first and is logged."))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._installed_tab(), "Installed")
        self.tabs.addTab(self._updates_tab(), "Updates")
        self.tabs.addTab(self._get_tab(), "Get apps")
        lay.addWidget(self.tabs, 1)
        self.status = label("", "status", wrap=True)
        lay.addWidget(self.status)

    # -- tabs -------------------------------------------------------------------------------
    def _installed_tab(self) -> QWidget:
        w = named(QWidget(), "page")
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter installed apps")
        self.filter.textChanged.connect(self._filter_installed)
        row.addWidget(self.filter, 1)
        refresh = button("Refresh")
        refresh.clicked.connect(self.load_installed)
        row.addWidget(refresh)
        self.uninstall_btn = button("Uninstall…")
        self.uninstall_btn.clicked.connect(self._uninstall)
        row.addWidget(self.uninstall_btn)
        v.addLayout(row)
        self.installed = _PackageTable()
        v.addWidget(self.installed, 1)
        return w

    def _filter_installed(self, text: str) -> None:
        self.installed.show_packages(self._installed, text)

    def _updates_tab(self) -> QWidget:
        w = named(QWidget(), "page")
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        check = button("Check for updates")
        check.clicked.connect(self.load_updates)
        row.addWidget(check)
        row.addStretch()
        self.update_btn = button("Update…", "primary")
        self.update_btn.clicked.connect(self._update)
        row.addWidget(self.update_btn)
        v.addLayout(row)
        self.updates = _PackageTable()
        v.addWidget(self.updates, 1)
        return w

    def _get_tab(self) -> QWidget:
        w = named(QWidget(), "page")
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search apps, e.g. VLC, 7-Zip, Spotify")
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        go = button("Search")
        go.clicked.connect(self.search)
        row.addWidget(go)
        self.install_btn = button("Install…", "primary")
        self.install_btn.clicked.connect(self._install)
        row.addWidget(self.install_btn)
        v.addLayout(row)
        self.results = _PackageTable()
        v.addWidget(self.results, 1)
        return w

    # -- background work ----------------------------------------------------------------------
    def _start(self, job: str, text: str | None, work: Callable[[], object]) -> bool:
        """Run `work` on a thread; `text` (None = keep the current message) is shown meanwhile."""
        if job in self._busy:
            return False
        self._busy.add(job)
        if text is None:
            self._quiet.add(job)
        else:
            self.status.setText(text)
        bridge = self._bridge

        def run() -> None:
            try:
                result: object = work()
            except (WingetError, OSError) as exc:
                result = exc
            bridge.done.emit(job, result)

        threading.Thread(target=run, daemon=True, name=f"chiku-apps-{job}").start()
        return True

    def ensure_loaded(self) -> None:
        """First visit: list installed apps (winget takes a few seconds)."""
        if not self.loaded:
            self.loaded = True
            self.load_installed()

    def load_installed(self, quiet: bool = False) -> None:
        self._start("installed", None if quiet else "Reading installed apps…",
                    lambda: self.winget.installed_all(self._cancel))

    def load_updates(self, quiet: bool = False) -> None:
        self._start("updates", None if quiet else "Checking for updates…",
                    lambda: self.winget.upgrades(self._cancel))

    def search(self) -> None:
        q = self.query.text().strip()
        if q:
            self._start("search", f"Searching for “{q}”…",
                        lambda: self.winget.search(q, self._cancel, limit=25))

    def _change(self, tool: str, pkg: Package | None, verb: str) -> None:
        if pkg is None:
            self.status.setText(f"Select an app to {verb} first.")
            return
        args: dict[str, Any] = {"id": pkg.id}
        if tool == "install_package":
            args["source"] = pkg.source if pkg.source in ("winget", "msstore") else "winget"
        self._start(tool, f"{verb.capitalize()}: {pkg.name}… (confirm in the dialog)",
                    lambda: self.run_tool(tool, args))

    def _install(self) -> None:
        self._change("install_package", self.results.selected(), "install")

    def _update(self) -> None:
        self._change("update_package", self.updates.selected(), "update")

    def _uninstall(self) -> None:
        self._change("uninstall_package", self.installed.selected(), "uninstall")

    def _finished(self, job: str, result: object) -> None:
        self._busy.discard(job)
        quiet = job in self._quiet
        self._quiet.discard(job)
        if isinstance(result, Exception):
            self.status.setText(str(result))
            return
        if isinstance(result, ToolResult):
            self.status.setText(("✓ " if result.ok else "✕ ") + result.message)
            if result.ok:  # refresh the lists, keeping this message on screen
                self.load_installed(quiet=True)
                if job == "update_package":
                    self.load_updates(quiet=True)
            return
        packages = result if isinstance(result, list) else []
        if job == "installed":
            self._installed = sorted(packages, key=lambda p: p.name.lower())
            self.installed.show_packages(self._installed, self.filter.text())
            if not quiet:
                self.status.setText(f"{len(packages)} apps installed.")
        elif job == "updates":
            self.updates.show_packages(packages)
            if not quiet:
                self.status.setText(f"{len(packages)} updates available." if packages
                                    else "Everything is up to date.")
        elif job == "search":
            self.results.show_packages(packages)
            self.status.setText(f"{len(packages)} found." if packages else "Nothing found.")

    def shutdown(self) -> None:
        self._cancel.cancel("closing")

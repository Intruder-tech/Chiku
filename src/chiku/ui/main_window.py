"""Main window: sidebar navigation, chat page, embedded pages, compact mode and tray icon.

Composition only: engine events arrive through AssistantViewModel; widgets live in
chiku.ui.components; Memory/Activity/Settings are the existing dialogs embedded as pages.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from chiku.assistant import Message, State, ToolActivity
from chiku.bootstrap import build_assistant
from chiku.branding import ICON, asset
from chiku.config import Settings
from chiku.paths import AppPaths
from chiku.ui import theme
from chiku.ui.compact import CompactWindow
from chiku.ui.components import named
from chiku.ui.components.chat import ChatView
from chiku.ui.components.input_bar import InputBar
from chiku.ui.components.sidebar import Sidebar
from chiku.ui.components.voice_bar import VoiceBar
from chiku.ui.pages import DialogPage
from chiku.ui.viewmodel import AssistantViewModel


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, paths: AppPaths):
        super().__init__()
        self.paths = paths
        self.setWindowTitle("Chiku")
        self.resize(900, 720)
        self.setMinimumSize(640, 520)

        self.vm = AssistantViewModel()
        self.assistant = build_assistant(settings, paths, on_event=self.vm.on_event)
        self.vm.attach(self.assistant)

        self._build()
        self.compact = CompactWindow()
        self.compact.mic_toggled.connect(self.vm.set_mic)
        self.compact.stop.connect(lambda: self.vm.interrupt("stop button"))
        self.compact.expand.connect(self.show_full)
        self._tray()
        self._wire()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.assistant.start()

    # -- layout --------------------------------------------------------------------------
    def _build(self) -> None:
        central = named(QWidget(), "central")
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = Sidebar()
        self.sidebar.set_provider(self.assistant.llm_label)
        row.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)

        chat = named(QWidget(), "page")
        col = QVBoxLayout(chat)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.voice = VoiceBar()
        self.chat = ChatView(on_link=self.assistant.open_link)
        self.input = InputBar()
        col.addWidget(self.voice)
        col.addWidget(self.chat, 1)
        col.addWidget(self.input)
        self.pages: dict[str, QWidget] = {"chat": chat}
        self.stack.addWidget(chat)
        self.setCentralWidget(central)

    def _page(self, key: str) -> QWidget:
        if key not in self.pages:
            if key == "memory":
                from chiku.ui.memory_dialog import MemoryDialog

                store = self.assistant.memory
                assert store is not None
                page = DialogPage("What Chiku remembers", lambda p: MemoryDialog(store, p))
            elif key == "activity":
                from chiku.ui.activity_dialog import ActivityDialog

                audit = self.assistant._executor.audit  # noqa: SLF001
                assert audit is not None
                page = DialogPage("Activity", lambda p: ActivityDialog(audit, p))
            else:
                from chiku.ui.settings_dialog import SettingsDialog

                page = DialogPage("Settings",
                                  lambda p: SettingsDialog(self.assistant, self.paths, p),
                                  on_accept=self._settings_saved)
            self.pages[key] = page
            self.stack.addWidget(page)
        return self.pages[key]

    def show_page(self, key: str) -> None:
        page = self._page(key)
        if key in ("memory", "activity") and isinstance(page, DialogPage):
            page.refresh()  # always show current data
        self.stack.setCurrentWidget(page)
        self.sidebar.select(key)

    def _wire(self) -> None:
        vm = self.vm
        vm.state_changed.connect(self._on_state)
        vm.message.connect(self._on_message)
        vm.tool.connect(lambda ev: self.chat.show_tool(ev))
        vm.component.connect(self._on_component)
        vm.mic_changed.connect(self.input.set_mic)
        vm.confirm_requested.connect(self._on_confirm)
        self.input.mic_toggled.connect(vm.set_mic)
        self.input.send.connect(vm.send)
        self.input.stop.connect(lambda: vm.interrupt("stop button"))
        self.sidebar.page_selected.connect(self.show_page)
        self.sidebar.compact_requested.connect(self.show_compact)
        for keys, action in (("Ctrl+M", self.input.mic.click),
                             ("Esc", lambda: vm.interrupt("esc")),
                             ("Ctrl+,", lambda: self.show_page("settings")),
                             ("Ctrl+Shift+Space", self.toggle_compact)):
            QShortcut(QKeySequence(keys), self).activated.connect(action)
            QShortcut(QKeySequence(keys), self.compact).activated.connect(action)

    def _tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(str(asset(ICON))), self)
        self.tray.setToolTip("Chiku")
        menu = QMenu()
        for text, slot in (("Open Chiku", self.show_full), ("Compact mode", self.show_compact),
                           ("Microphone on/off", self.input.mic.click), ("Quit", self.close)):
            act = QAction(text, menu)
            act.triggered.connect(slot)
            menu.addAction(act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.show_full() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self._tray_menu = menu
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    # -- modes ------------------------------------------------------------------------------
    def show_compact(self) -> None:
        if not self.compact.isVisible():
            self.compact.place_default()
        self.compact.set_state(self.vm.state, self.vm.detail)
        self.compact.show()
        self.compact.raise_()
        self.hide()

    def show_full(self) -> None:
        self.compact.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_compact(self) -> None:
        (self.show_full if self.compact.isVisible() else self.show_compact)()

    # -- events ------------------------------------------------------------------------------
    @Slot(object, str)
    def _on_state(self, state: State, detail: str) -> None:
        self.voice.set_state(state, detail)
        self.compact.set_state(state, detail)

    @Slot(object)
    def _on_message(self, ev: Message) -> None:
        self.chat.show_message(ev)

    @Slot(str, bool, str)
    def _on_component(self, name: str, ok: bool, message: str) -> None:
        self.voice.set_component(name, ok, message)
        if name == "language model":
            self.sidebar.set_provider(self.assistant.llm_label)

    def _tick(self) -> None:
        inp, out = self.vm.levels()
        state = self.assistant.state
        if self.isVisible():
            self.voice.tick(state, inp, out, self.vm.mic_active)
            live = self.vm.mic_active
            if self.input.mic.isChecked() != live and not (
                    self.input.mic.isChecked() and self.vm.mic_wanted):
                self.input.mic.setChecked(live)
        if self.compact.isVisible():
            self.compact.tick(state, out if out > 0 else inp,
                              self.vm.mic_active or self.vm.mic_wanted)

    def _settings_saved(self) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(theme.apply(self.assistant.settings.ui.theme))
        self.sidebar.set_provider(self.assistant.llm_label)

    @Slot(str, str, object)
    def _on_confirm(self, name: str, description: str, box: dict) -> None:
        parent = self.compact if self.compact.isVisible() else self
        parent.raise_()
        parent.activateWindow()
        answer = QMessageBox.question(
            parent, "Chiku needs your OK", f"{description}\n\n(action: {name})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        box["ok"] = answer == QMessageBox.StandardButton.Yes
        box["done"].set()

    def maybe_onboard(self) -> None:
        from chiku.ui.onboarding import OnboardingWizard, needs_onboarding

        if not needs_onboarding(self.assistant.settings, self.paths):
            if self.assistant.settings.ui.start_compact:
                self.show_compact()
            return
        wiz = OnboardingWizard(self.assistant.settings, self.paths, self)
        wiz.exec()
        if wiz.settings.onboarding_done:
            self.assistant.apply_settings(wiz.settings)
            self.assistant.reload()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self._timer.stop()
        self.compact.close()
        self.tray.hide()
        self.assistant.shutdown()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()


__all__ = ["MainWindow", "ToolActivity"]

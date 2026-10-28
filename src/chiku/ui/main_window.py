"""Main window: the three-pane workspace, minimised modes (notch, card) and the tray icon.

Left: pages and conversations. Centre: chat or the current page. Right: live voice, sources
and recent actions. Ctrl+K opens the command palette.

Composition only: engine events arrive through AssistantViewModel; widgets live in
chiku.ui.components; Memory/Activity/Settings are the existing dialogs embedded as pages.
"""

from __future__ import annotations

import contextlib
import threading
from functools import partial
from typing import Any

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from chiku.assistant import Message, State, ToolActivity
from chiku.bootstrap import build_assistant
from chiku.branding import ICON, asset
from chiku.cancel import CancelToken
from chiku.config import Settings, save_settings
from chiku.conversations import ConversationStore
from chiku.discord_presence import DiscordPresence
from chiku.models import WHISPER_SIZES_MB, whisper_present
from chiku.paths import AppPaths
from chiku.tools.base import ToolResult
from chiku.ui import theme
from chiku.ui.compact import CompactWindow
from chiku.ui.components import named
from chiku.ui.components.chat_page import ChatPage
from chiku.ui.components.context_panel import ContextPanel
from chiku.ui.components.palette import Command, CommandPalette
from chiku.ui.components.sidebar import PAGES, Sidebar
from chiku.ui.notch import NotchWindow
from chiku.ui.pages import DialogPage
from chiku.ui.viewmodel import AssistantViewModel
from chiku.ui.waveform import Mode
from chiku.wake import WAKE_MODEL

SETTINGS_TABS = ("Appearance", "Voice", "Audio", "Languages", "Background", "AI model",
                 "Persona", "Web", "Tools", "Privacy")
PANEL_MIN_WIDTH = 1060  # narrower windows hide the right panel (Ctrl+. shows it)


def _badged(icon_path: str) -> QIcon:
    """The tray icon with a green dot: the mic is open for the wake word."""
    pix = QPixmap(icon_path).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("#0b0f14"))
    p.setBrush(QColor("#2ecc71"))
    p.drawEllipse(40, 40, 22, 22)
    p.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, paths: AppPaths):
        super().__init__()
        self.paths = paths
        self.setWindowTitle("Chiku")
        self.resize(1240, 780)
        self.setMinimumSize(860, 560)

        self.vm = AssistantViewModel()
        self.assistant = build_assistant(settings, paths, on_event=self.vm.on_event)
        self.vm.attach(self.assistant)
        self._quitting = False
        self._told_tray = False
        self._woke_mini = False
        self._panel_hidden_by_user = True  # the side panel is opt-in (Ctrl+.)
        cs = lambda: self.assistant.settings.conversations  # noqa: E731
        self.conversations = ConversationStore(paths.conversations_file,
                                               keep=lambda: cs().keep_history,
                                               max_saved=lambda: cs().max_saved)
        self.command_palette = CommandPalette(self)

        self._build()
        self.compact = CompactWindow()
        self.compact.mic_toggled.connect(self.vm.set_mic)
        self.compact.stop.connect(lambda: self.vm.interrupt("stop button"))
        self.compact.expand.connect(self.show_full)
        self.notch = NotchWindow()
        self.notch.positions = dict(settings.ui.notch_positions)
        self.notch.screen_name = settings.ui.notch_screen
        self.notch.mic_toggled.connect(self.vm.set_mic)
        self.notch.stop.connect(lambda: self.vm.interrupt("stop button"))
        self.notch.open_full.connect(self.show_full)
        self.notch.open_card.connect(self.show_compact)
        self.notch.hide_requested.connect(self.hide_to_tray)
        self.notch.moved.connect(self._notch_moved)
        self._tray()
        self._wire()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.assistant.start()
        self._refresh_chats()
        self.discord: DiscordPresence | None = None
        self._sync_discord()
        catalog = self.assistant._catalog  # noqa: SLF001
        if catalog is not None:  # warm the app list for the command palette
            threading.Thread(target=catalog.entries, daemon=True, name="chiku-catalog").start()

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
        self.panel = ContextPanel(on_link=self.assistant.open_link)
        self.panel.hide()  # off until asked for (Ctrl+. or the button above the chat)
        self.voice = self.panel.voice
        row.addWidget(self.panel)

        self.chat_page = ChatPage(on_link=self.assistant.open_link)
        self.chat = self.chat_page.chat
        self.input = self.chat_page.input
        self.pages: dict[str, QWidget] = {"chat": self.chat_page}
        self.stack.addWidget(self.chat_page)
        self.setCentralWidget(central)
        self._show_user()

    def _page(self, key: str) -> QWidget:
        if key not in self.pages:
            if key == "memory":
                from chiku.ui.memory_dialog import MemoryDialog

                store = self.assistant.memory
                assert store is not None
                page: QWidget = DialogPage(
                    "Memory", lambda p: MemoryDialog(store, p),
                    subtitle="Only what you asked Chiku to remember, or approved. Stored on this "
                    "computer. Double-click a memory to edit it.")
            elif key == "activity":
                from chiku.ui.activity_dialog import ActivityDialog

                audit = self.assistant._executor.audit  # noqa: SLF001
                assert audit is not None
                page = DialogPage(
                    "Activity", lambda p: ActivityDialog(audit, p),
                    subtitle="Every action Chiku took on this computer, newest first, with who "
                    "approved it. Stored locally.")
            elif key == "profile":
                from chiku.ui.profile_page import ProfilePage

                profile = ProfilePage(self.assistant, self.paths, self.conversations)
                profile.open_page.connect(self.open_target)
                profile.saved.connect(self._profile_saved)
                page = profile
            elif key == "apps":
                from chiku.tools.packages import Winget
                from chiku.ui.apps_page import AppsPage

                page = AppsPage(Winget(), self._run_tool)
            else:
                from chiku.ui.settings_dialog import SettingsDialog

                page = DialogPage("",
                                  lambda p: SettingsDialog(self.assistant, self.paths, p,
                                                           self.conversations, self.set_theme),
                                  on_accept=self._settings_saved, margins=(0, 0, 0, 0))
            self.pages[key] = page
            self.stack.addWidget(page)
        return self.pages[key]

    def show_page(self, key: str) -> None:
        page = self._page(key)
        if key in ("memory", "activity") and isinstance(page, DialogPage):
            page.refresh()  # always show current data
        refresh = getattr(page, "refresh", None)
        if key == "profile" and refresh is not None:
            refresh()
        ensure_loaded = getattr(page, "ensure_loaded", None)
        if ensure_loaded is not None:
            ensure_loaded()
        self.stack.setCurrentWidget(page)
        self.sidebar.select(key)

    def open_target(self, target: str) -> None:
        """A page key, or "settings:<tab title>" to open one Settings tab."""
        key, _, tab = target.partition(":")
        self.show_full()
        self.show_page(key)
        page = self.pages.get(key)
        dialog = getattr(page, "dialog", None)
        tabs = getattr(dialog, "tabs", None)
        if tab and tabs is not None:
            for i in range(tabs.count()):
                if tabs.tabText(i) == tab:
                    tabs.setCurrentIndex(i)

    def _run_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Apps page: the same permissioned, audited path as a voice request (worker thread)."""
        return self.assistant._executor.execute(name, args, CancelToken())  # noqa: SLF001

    # -- conversations -------------------------------------------------------------------------
    def _refresh_chats(self) -> None:
        store = self.conversations
        self.sidebar.set_conversations([(c.id, c.title) for c in store.listed()],
                                       store.active.id)

    def new_chat(self) -> None:
        self.conversations.new()
        self.assistant.set_history([])
        self.chat_page.reset()
        self.panel.set_sources([])
        self._refresh_chats()
        self.show_page("chat")
        self.input.input.setFocus()

    def open_conversation(self, conv_id: str) -> None:
        if conv_id == self.conversations.active.id:
            self.show_page("chat")
            return
        try:
            conv = self.conversations.switch(conv_id)
        except KeyError:
            return
        self.assistant.set_history(conv.history())
        self.chat_page.reset()
        for e in conv.entries:
            self.chat.add_entry(e.role, e.text, e.source, e.sources, e.time)
        last = next((e for e in reversed(conv.entries) if e.role == "assistant"), None)
        self.panel.set_sources(last.sources if last else [])
        self._refresh_chats()
        self.show_page("chat")

    def delete_conversation(self, conv_id: str) -> None:
        was_current = conv_id == self.conversations.active.id
        self.conversations.delete(conv_id)
        if was_current:
            self.assistant.set_history([])
            self.chat_page.reset()
            self.panel.set_sources([])
        self._refresh_chats()

    # -- command palette -----------------------------------------------------------------------
    def palette_commands(self) -> list[Command]:
        cmds = [Command(f"Go to {text}", "Page", partial(self.open_target, key), key)
                for key, text in PAGES]
        cmds += [
            Command("New chat", "Action", self.new_chat, "conversation start"),
            Command("Microphone on/off", "Action", self.input.mic.click, "mic listen"),
            Command("Stop talking", "Action", lambda: self.vm.interrupt("palette"),
                    "cancel interrupt quiet"),
            Command("Minimise to the notch", "Action", self.show_notch, "compact small top"),
            Command("Floating card", "Action", self.show_compact, "compact small"),
            Command("Wake word on/off (“Hey Chiku”)", "Action", self.wake_action.trigger,
                    "background listen hey chiku"),
            Command("Show or hide the side panel", "Action", self.toggle_panel,
                    "context sources"),
            Command("Check for app updates", "Action", self._palette_updates,
                    "winget upgrade apps"),
            Command("Light theme", "Action", lambda: self.set_theme("light"), "appearance"),
            Command("Dark theme", "Action", lambda: self.set_theme("dark"), "appearance"),
            Command("Theme follows Windows", "Action", lambda: self.set_theme("system"),
                    "appearance"),
            Command("Quit Chiku", "Action", self.quit_app, "exit close"),
        ]
        cmds += [Command(f"Settings: {tab}", "Settings",
                         partial(self.open_target, f"settings:{tab}"), "preferences")
                 for tab in SETTINGS_TABS]
        cmds += [Command(c.title, "Chat", partial(self.open_conversation, c.id),
                         "conversation history")
                 for c in self.conversations.listed() if c.entries]
        catalog = self.assistant._catalog  # noqa: SLF001
        if catalog is not None:
            cmds += [Command(f"Open {e.name}", "App", partial(self.vm.send, f"open {e.name}"),
                             "launch start program", hidden_until_typed=True)
                     for e in catalog.cached()]
        return cmds

    def open_palette(self) -> None:
        if not self.isVisible():
            self.show_full()
        self.command_palette.open_with(self.palette_commands(), self)

    def _palette_updates(self) -> None:
        self.open_target("apps")
        page = self.pages["apps"]
        tabs = getattr(page, "tabs", None)
        if tabs is not None:
            tabs.setCurrentIndex(1)
        load = getattr(page, "load_updates", None)
        if load is not None:
            load()

    def set_theme(self, mode: str) -> None:
        new = self.assistant.settings.model_copy(deep=True)
        new.ui.theme = mode  # type: ignore[assignment]
        save_settings(new, self.paths.settings_file)
        self.assistant.apply_settings(new)
        self._settings_saved()

    def toggle_theme(self) -> None:
        """The sidebar's sun/moon button: switch between light and dark."""
        self.set_theme("light" if theme.current_mode == "dark" else "dark")

    def toggle_panel(self) -> None:
        self._panel_hidden_by_user = self.panel.isVisible()
        self.panel.setVisible(not self.panel.isVisible())

    def _show_user(self) -> None:
        name = self.assistant.settings.persona.user_name
        picture = self.paths.avatar_file
        self.sidebar.set_user(name, picture if picture.exists() else None)
        self.chat_page.set_user(name)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if not self._panel_hidden_by_user:
            self.panel.setVisible(self.width() >= PANEL_MIN_WIDTH)

    def _wire(self) -> None:
        vm = self.vm
        vm.state_changed.connect(self._on_state)
        vm.message.connect(self._on_message)
        vm.tool.connect(lambda ev: self.chat.show_tool(ev))
        vm.tool.connect(lambda ev: self.panel.add_tool(ev))
        vm.component.connect(self._on_component)
        vm.mic_changed.connect(self.input.set_mic)
        vm.confirm_requested.connect(self._on_confirm)
        vm.woke.connect(self._on_woke)
        self.input.mic_toggled.connect(vm.set_mic)
        self.input.send.connect(vm.send)
        self.input.stop.connect(lambda: vm.interrupt("stop button"))
        self.sidebar.page_selected.connect(self.show_page)
        self.sidebar.compact_requested.connect(self.show_mini)
        self.sidebar.new_chat.connect(self.new_chat)
        self.sidebar.conversation_selected.connect(self.open_conversation)
        self.sidebar.conversation_deleted.connect(self.delete_conversation)
        self.sidebar.theme_toggled.connect(self.toggle_theme)
        self.sidebar.search_requested.connect(self.open_palette)
        self.chat_page.suggestion.connect(vm.send)
        self.chat_page.strip.panel_toggled.connect(self.toggle_panel)
        for keys, action in (("Ctrl+K", self.open_palette),
                             ("Ctrl+N", self.new_chat),
                             ("Ctrl+.", self.toggle_panel),
                             ("Ctrl+M", self.input.mic.click),
                             ("Esc", lambda: vm.interrupt("esc")),
                             ("Ctrl+,", lambda: self.show_page("settings")),
                             ("Ctrl+Shift+Space", self.toggle_compact)):
            for window in (self, self.compact, self.notch):
                QShortcut(QKeySequence(keys), window).activated.connect(action)

    def _tray(self) -> None:
        self._icons = {False: QIcon(str(asset(ICON))), True: _badged(str(asset(ICON)))}
        self._tray_badge = False
        self.tray = QSystemTrayIcon(self._icons[False], self)
        self.tray.setToolTip("Chiku")
        menu = QMenu()
        for text, slot in (("Open Chiku", self.show_full), ("Notch", self.show_notch),
                           ("Compact card", self.show_compact),
                           ("Microphone on/off", self.input.mic.click)):
            act = QAction(text, menu)
            act.triggered.connect(slot)
            menu.addAction(act)
        self.wake_action = QAction("Listen for “Hey Chiku”", menu)
        self.wake_action.setCheckable(True)
        self.wake_action.setChecked(self.assistant.settings.wake.enabled)
        self.wake_action.toggled.connect(self._toggle_wake)
        menu.addAction(self.wake_action)
        menu.addSeparator()
        quit_action = QAction("Quit Chiku", menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.show_full() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self._tray_menu = menu
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _toggle_wake(self, on: bool) -> None:
        s = self.assistant.settings
        if on and not whisper_present(self.paths.models_dir, WAKE_MODEL):
            self.wake_action.setChecked(False)
            self.show_full()
            self.show_page("settings")
            QMessageBox.information(self, "Chiku", "The wake word needs a small speech model "
                                    f"(~{WHISPER_SIZES_MB[WAKE_MODEL]} MB). Download it on the "
                                    "Background tab.")
            return
        if s.wake.enabled == on:
            return
        new = s.model_copy(deep=True)
        new.wake.enabled = on
        save_settings(new, self.paths.settings_file)
        self.assistant.apply_settings(new)
        if isinstance(page := self.pages.get("settings"), DialogPage):
            page.refresh()  # its Background tab would otherwise save the old choice

    def _update_tray(self) -> None:
        badge = self.vm.wake_listening
        if badge != self._tray_badge:
            self._tray_badge = badge
            self.tray.setIcon(self._icons[badge])
            self.tray.setToolTip("Chiku · listening for “Hey Chiku”" if badge else "Chiku")
        wanted = self.assistant.settings.wake.enabled
        if self.wake_action.isChecked() != wanted:
            self.wake_action.blockSignals(True)
            self.wake_action.setChecked(wanted)
            self.wake_action.blockSignals(False)

    # -- modes ------------------------------------------------------------------------------
    def start_in_background(self) -> None:
        """Signed-in autostart: no window, just the tray (and the wake word, if on)."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.show()
            return
        text = ("Say “Hey Chiku” or “Wake up” when you need me." if
                self.assistant.settings.wake.enabled else "I'm in the tray when you need me.")
        self.tray.showMessage("Chiku is running", text, self._icons[False], 4000)

    @property
    def mini_visible(self) -> bool:
        return self.notch.isVisible() or self.compact.isVisible()

    def show_mini(self) -> None:
        """Minimise to the style chosen in Settings: the notch (default) or the card."""
        (self.show_notch if self.assistant.settings.ui.mini_mode == "notch"
         else self.show_compact)()

    def show_notch(self) -> None:
        self.compact.hide()
        if not self.notch.isVisible():
            self.notch.place()
        self.notch.set_state(self.vm.state, self.vm.detail)
        self.notch.show()
        self.notch.raise_()
        self.hide()

    def show_compact(self) -> None:
        self.notch.hide()
        if not self.compact.isVisible():
            self.compact.place_default()
        self.compact.set_state(self.vm.state, self.vm.detail)
        self.compact.show()
        self.compact.raise_()
        self.hide()

    def show_full(self) -> None:
        self._woke_mini = False
        self.compact.hide()
        self.notch.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.show_full()  # nowhere to come back from
            return
        self._woke_mini = False
        self.notch.hide()
        self.compact.hide()
        self.hide()

    def toggle_compact(self) -> None:
        (self.show_full if self.mini_visible else self.show_mini)()

    @Slot(str, float)
    def _notch_moved(self, screen: str, fraction: float) -> None:
        ui = self.assistant.settings.ui
        ui.notch_positions[screen] = fraction
        ui.notch_screen = screen
        with contextlib.suppress(OSError):  # only the remembered position would be lost
            save_settings(self.assistant.settings, self.paths.settings_file)

    # -- events ------------------------------------------------------------------------------
    @Slot(str)
    def _on_woke(self, _phrase: str) -> None:
        if not self.isVisible() and not self.mini_visible:
            self._woke_mini = True  # shown for this wake-up only; hidden again after
            self.show_mini()

    @Slot(object, str)
    def _on_state(self, state: State, detail: str) -> None:
        self.voice.set_state(state, detail)
        self.chat_page.strip.set_state(state, detail)
        self.compact.set_state(state, detail)
        self.notch.set_state(state, detail)
        if state is State.WAITING and self._woke_mini:
            self._woke_mini = False
            self.notch.hide()
            self.compact.hide()

    @Slot(object)
    def _on_message(self, ev: Message) -> None:
        self.chat.show_message(ev)
        if not ev.final or ev.role == "notice":
            return
        self.conversations.add(ev.role, ev.text, ev.source, ev.sources)
        if ev.role == "assistant":
            self.notch.set_reply(ev.text)
            self.panel.set_sources(ev.sources)
        else:
            self._refresh_chats()  # a new chat gets its title from the first message

    @Slot(str, bool, str)
    def _on_component(self, name: str, ok: bool, message: str) -> None:
        self.voice.set_component(name, ok, message)
        if name == "language model":
            self.sidebar.set_provider(self.assistant.llm_label)

    def _tick(self) -> None:
        self._update_tray()
        inp, out = self.vm.levels()
        state = self.assistant.state
        if self.isVisible():
            if self.stack.currentWidget() is self.chat_page:
                self.chat_page.strip.tick(state, inp, out, self.vm.mic_active)
            if self.panel.isVisible():
                self.voice.tick(state, inp, out, self.vm.mic_active)
            live = self.vm.mic_active
            if self.input.mic.isChecked() != live and not (
                    self.input.mic.isChecked() and self.vm.mic_wanted):
                self.input.mic.setChecked(live)
        if self.compact.isVisible():
            self.compact.tick(state, out if out > 0 else inp,
                              self.vm.mic_active or self.vm.mic_wanted)
        if self.notch.isVisible():
            mode: Mode = "output" if out > 0 else "input" if inp > 0 else "idle"
            self.notch.tick(state, out if out > 0 else inp,
                            self.vm.mic_active or self.vm.mic_wanted, mode)

    def _profile_saved(self) -> None:
        self._show_user()
        page = self.pages.get("settings")
        if isinstance(page, DialogPage):
            page.refresh()  # its Persona and Voice tabs would otherwise save the old values

    def _sync_discord(self) -> None:
        """Start or stop the opt-in Discord presence to match the settings."""
        d = self.assistant.settings.discord
        if self.discord is not None and (not d.enabled
                                         or self.discord.application_id != d.application_id):
            self.discord.stop()
            self.discord = None
        if d.enabled and self.discord is None:
            self.discord = DiscordPresence(d.application_id)
            self.discord.start()

    def _settings_saved(self) -> None:
        self._sync_discord()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(theme.apply(self.assistant.settings.ui.theme))
        self.sidebar.set_provider(self.assistant.llm_label)
        # Icons are drawn in theme colours: re-draw them after a theme change.
        self.sidebar.refresh_icons()
        self.chat_page.refresh_icons()
        self._show_user()

    @Slot(str, str, object)
    def _on_confirm(self, name: str, description: str, box: dict) -> None:
        parent: QWidget = self
        if self.notch.isVisible():
            parent = self.notch
        elif self.compact.isVisible():
            parent = self.compact
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
                self.show_mini()
            return
        wiz = OnboardingWizard(self.assistant.settings, self.paths, self)
        wiz.exec()
        if wiz.settings.onboarding_done:
            self.assistant.apply_settings(wiz.settings)
            self.assistant.reload()

    def quit_app(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        s = self.assistant.settings
        if (not self._quitting and (s.ui.close_to_tray or s.wake.enabled)
                and QSystemTrayIcon.isSystemTrayAvailable()):
            # Background mode: the window closes, Chiku keeps running in the tray (wake word,
            # Discord status). Quit from the tray icon's menu.
            event.ignore()
            self.hide()
            if not self._told_tray:
                self._told_tray = True
                hint = ("Say “Hey Chiku” or click the tray icon." if s.wake.enabled
                        else "Click the tray icon to open me again.")
                self.tray.showMessage("Chiku is still running", f"{hint} Quit from the icon's "
                                      "menu.", self._icons[False], 4000)
            return
        self._timer.stop()
        if self.discord is not None:
            self.discord.stop()
        apps = self.pages.get("apps")
        shutdown = getattr(apps, "shutdown", None)
        if shutdown is not None:
            shutdown()
        self.compact.close()
        self.notch.close()
        self.tray.hide()
        self.assistant.shutdown()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()


__all__ = ["MainWindow", "ToolActivity"]

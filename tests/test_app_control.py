"""App control and messaging: key combos, chat matching, the Discord and WhatsApp flows (fake
desktop), blocked apps, mid-run confirmation in the audit log, and the instant command path."""

import os
import sys
from pathlib import Path

import pytest

from chiku.audit import AuditLog
from chiku.automation.keyboard import KeyComboError, parse_combo
from chiku.automation.uia import Control, best_match
from chiku.intents import parse_command
from chiku.tools.app_control import (
    Desktop,
    app_control_tools,
    chat_name,
    is_blocked,
    title_matches,
)
from chiku.tools.apps import AppCatalog, AppEntry
from chiku.tools.registry import ToolExecutor, ToolRegistry
from chiku.tools.windows_ctl import AppWindow

from .conftest import Harness, wait_for

# -- small helpers -----------------------------------------------------------------------------

@pytest.mark.parametrize(("combo", "mods", "key"), [
    ("ctrl+k", [0x11], 0x4B), ("Ctrl + Shift + A", [0x11, 0x10], 0x41), ("enter", [], 0x0D),
    ("ctrl+alt+/", [0x11, 0x12], 0xBF), ("f5", [], 0x74),
])
def test_parse_combo(combo: str, mods: list[int], key: int) -> None:
    assert parse_combo(combo) == (mods, key)


@pytest.mark.parametrize("combo", ["win+r", "ctrl+", "hello world", "ctrl+banana", ""])
def test_bad_combos_are_refused(combo: str) -> None:
    with pytest.raises(KeyComboError):
        parse_combo(combo)


def test_chat_title_matching() -> None:
    assert title_matches("ICONIC シ, Manas - Discord", "Manas")
    assert title_matches("@aman_23 - Discord", "aman")
    assert not title_matches("Friends - Discord", "Manas")
    assert not title_matches("Manas - Discord", "")
    assert chat_name("ICONIC シ, Manas - Discord") == "ICONIC シ, Manas"


def test_blocked_apps() -> None:
    assert is_blocked(AppWindow(1, 1, "Windows PowerShell", "powershell.exe"))
    assert is_blocked(AppWindow(1, 1, "Terminal", "WindowsTerminal.exe"))
    assert is_blocked(AppWindow(1, 1, "Vault - Bitwarden", "Bitwarden.exe"))
    assert not is_blocked(AppWindow(1, 1, "Untitled - Notepad", "Notepad.exe"))


def test_best_control_match() -> None:
    items = [("Save as", 1), ("Save", 2), ("Don't save", 3)]
    assert best_match(items, "save") == 2  # exact beats prefix and contains
    assert best_match(items, "don't") == 3
    assert best_match(items, "print") is None


# -- fake desktop ------------------------------------------------------------------------------

class FakeWindows:
    def __init__(self, wins: list[AppWindow]) -> None:
        self.wins = list(wins)
        self.focused: list[int] = []

    def windows(self) -> list[AppWindow]:
        return list(self.wins)

    def focus(self, hwnd: int) -> bool:
        self.focused.append(hwnd)
        return any(w.hwnd == hwnd for w in self.windows())

    def close(self, hwnd: int) -> None:
        pass

    def exists(self, hwnd: int) -> bool:
        return any(w.hwnd == hwnd for w in self.windows())

    def press(self, vk: int) -> None:
        pass

    def retitle(self, hwnd: int, title: str) -> None:
        self.wins = [AppWindow(w.hwnd, w.pid, title, w.process) if w.hwnd == hwnd else w
                     for w in self.wins]


def edit(name: str) -> Control:
    return Control(name, "edit", True, False)


class FakeUIA:
    """Models where the keyboard focus is, like the real apps: Ctrl+K opens Discord's quick
    switcher, Ctrl+Alt+/ focuses WhatsApp's search, and Enter in either opens a chat with its
    message box focused. `cooperative = False` is a game or pop-up keeping the focus."""

    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.focus: Control | None = edit("Message #general")
        self.switcher = False
        self.cooperative = True
        self.on_tab = lambda name: False  # True when a browser tab with that name exists

    def controls(self, hwnd: int, limit: int = 120) -> list[Control]:
        found = [Control("Play", "button", True, True), edit("Search")]
        return found + ([Control("Quick Switcher", "dialog", True, False)] if self.switcher
                        else [])

    def press(self, hwnd: int, name: str,
              kinds: tuple[str, ...] | None = None) -> Control | None:
        self.pressed.append(name)
        if kinds == ("tab",):
            return Control(name, "tab", True, True) if self.on_tab(name) else None
        return Control("Play", "button", True, True) if "play" in name.lower() else None

    def focused(self) -> Control | None:
        return self.focus

    def key(self, combo: str, typed: str) -> None:
        if not self.cooperative:
            return
        if combo == "ctrl+k":
            self.switcher, self.focus = True, edit("Quick switcher")
        elif combo == "ctrl+alt+/":
            self.focus = edit("Search input textbox")
        elif combo == "escape":
            self.switcher = False
        elif combo == "enter" and self.switcher:
            self.switcher, self.focus = False, edit(f"Message @{typed.lstrip('@')}")
        elif combo == "enter" and self.focus is not None and "search" in self.focus.name.lower():
            self.focus = edit("Type a message")


class FakeKeyboard:
    """Records keys; `on_enter` lets a test change window titles when a chat opens."""

    def __init__(self, uia: FakeUIA) -> None:
        self.uia = uia
        self.events: list[str] = []
        self.typed = ""
        self.on_enter = lambda typed: None

    def press(self, combo: str) -> None:
        parse_combo(combo)
        self.events.append(combo)
        if combo == "enter":
            self.on_enter(self.typed)
        self.uia.key(combo, self.typed)

    def type(self, text: str) -> None:
        self.events.append(f"type:{text}")
        self.typed = text


DISCORD = AppWindow(10, 100, "Friends - Discord", "Discord.exe")
CHROME_WA = AppWindow(20, 200, "WhatsApp - Google Chrome", "chrome.exe")
NOTEPAD = AppWindow(30, 300, "notes.txt - Notepad", "Notepad.exe")
SHELL = AppWindow(40, 400, "Windows PowerShell", "powershell.exe")


class Rig:
    """A fake desktop with the app-control tools behind a real ToolExecutor."""

    def __init__(self, wins: list[AppWindow], answer: bool = True,
                 catalog: AppCatalog | None = None) -> None:
        self.windows = FakeWindows(wins)
        self.uia = FakeUIA()
        self.kb = FakeKeyboard(self.uia)
        self.answer = answer
        self.launched: list[str] = []
        self.opened: list[str] = []
        self.questions: list[str] = []
        desktop = Desktop(self.windows, self.kb, self.uia, catalog,
                          launch=lambda e: self.launched.append(e.name),
                          open_url=self.opened.append, sleep=lambda _s: None)
        reg = ToolRegistry()
        for tool in app_control_tools(desktop):
            reg.register(tool)

        def confirm(_name: str, description: str) -> bool:
            self.questions.append(description)
            return self.answer() if callable(self.answer) else self.answer

        self.audit = AuditLog(Path(os.environ["CHIKU_HOME"]) / "audit.jsonl")
        self.ex = ToolExecutor(reg, confirm=confirm, audit=self.audit)

    def run(self, name: str, **args: object):  # noqa: ANN201
        from chiku.cancel import CancelToken

        return self.ex.execute(name, args, CancelToken())


# -- Discord -----------------------------------------------------------------------------------

def test_discord_message_is_sent_after_confirmation() -> None:
    rig = Rig([DISCORD])
    rig.kb.on_enter = lambda typed: (rig.windows.retitle(10, "@Manas - Discord")
                                     if typed == "@Manas" else None)
    r = rig.run("send_message", app="discord", to="Manas", text="are we playing tonight?")
    assert r.ok, r.message
    # "@Manas": the quick switcher lists people only, so the direct message comes first
    assert rig.kb.events == ["ctrl+k", "type:@Manas", "enter", "type:are we playing tonight?",
                             "enter"]
    assert "@Manas" in rig.questions[0] and "are we playing tonight?" in rig.questions[0]
    assert rig.audit.tail()[-1]["approval"] == "user"  # the mid-run yes is on record


def test_discord_falls_back_to_groups_and_channels() -> None:
    rig = Rig([DISCORD])
    rig.kb.on_enter = lambda typed: (rig.windows.retitle(10, "ICONIC シ, Manas - Discord")
                                     if typed == "ICONIC" else None)
    r = rig.run("send_message", app="discord", to="ICONIC", text="gg")
    assert r.ok, r.message
    assert rig.kb.events[:6] == ["ctrl+k", "type:@ICONIC", "enter", "escape", "ctrl+k",
                                 "type:ICONIC"]


def test_discord_declined_message_is_cleared_not_sent() -> None:
    rig = Rig([DISCORD], answer=False)
    rig.kb.on_enter = lambda typed: rig.windows.retitle(10, "@Manas - Discord")
    r = rig.run("send_message", app="discord", to="Manas", text="hi")
    assert not r.ok and r.error_code == "not_sent"
    assert rig.kb.events[-2:] == ["ctrl+a", "backspace"] and rig.kb.events.count("enter") == 1
    assert rig.audit.tail()[-1]["approval"] == "denied"


def test_discord_wrong_chat_types_nothing() -> None:
    rig = Rig([DISCORD])  # the title never changes to Manas
    r = rig.run("send_message", app="discord", to="Manas", text="secret plans")
    assert not r.ok and r.error_code == "chat_not_found"
    assert not any("secret plans" in e for e in rig.kb.events)
    assert rig.kb.events[-1] == "escape" and rig.questions == []


def test_open_group_containing_the_name_is_not_taken_for_the_person() -> None:
    # Already looking at a group that includes Manas; the switch to Manas never happens.
    rig = Rig([AppWindow(10, 100, "ICONIC シ, Manas - Discord", "Discord.exe")])
    r = rig.run("send_message", app="discord", to="Manas", text="hi")
    assert not r.ok and r.error_code == "chat_not_found"
    assert "type:hi" not in rig.kb.events


def test_nothing_is_typed_when_a_game_has_the_focus() -> None:
    """The real case found in testing: a Discord Activity (game) held the keyboard focus, so
    Ctrl+K never opened the quick switcher. Typing anyway would put the name in a chat."""
    rig = Rig([DISCORD])
    rig.uia.cooperative = False
    rig.uia.focus = Control("Blazing 8s", "document", True, False)
    r = rig.run("send_message", app="discord", to="Manas", text="hi")
    assert not r.ok and r.error_code == "focus_check_failed"
    assert not any(e.startswith("type:") for e in rig.kb.events)
    assert "enter" not in rig.kb.events and rig.questions == []


def test_no_enter_if_the_message_box_lost_focus_during_the_question() -> None:
    rig = Rig([DISCORD])
    rig.kb.on_enter = lambda typed: (rig.windows.retitle(10, "@Manas - Discord")
                                     if typed == "@Manas" else None)

    def answer() -> bool:
        rig.uia.focus = Control("Blazing 8s", "document", True, False)  # user clicked a game
        return True

    rig.answer = answer
    r = rig.run("send_message", app="discord", to="Manas", text="hi")
    assert not r.ok and r.error_code == "focus_check_failed"
    assert rig.kb.events.count("enter") == 1  # only the quick switcher's, never "send"


def test_discord_is_started_when_closed() -> None:
    catalog = AppCatalog(loader=lambda: [AppEntry("Discord", "lnk", r"C:\Start\Discord.lnk",
                                                  r"C:\Discord\Discord.exe")])
    rig = Rig([], catalog=catalog)
    rig.windows.windows = lambda: [DISCORD] if rig.launched else []  # type: ignore[method-assign]
    rig.run("send_message", app="discord", to="Manas", text="hi")
    assert rig.launched == ["Discord"]


# -- WhatsApp Web ------------------------------------------------------------------------------

def test_whatsapp_web_message_flow() -> None:
    rig = Rig([CHROME_WA])
    r = rig.run("send_message", app="whatsapp", to="Aman", text="I'm running late")
    assert r.ok, r.message
    assert rig.kb.events == ["ctrl+alt+/", "ctrl+a", "type:Aman", "enter",
                             "type:I'm running late", "enter"]
    assert "Aman" in rig.questions[0] and "Check that WhatsApp" in rig.questions[0]


def test_whatsapp_stops_if_search_does_not_get_focus() -> None:
    rig = Rig([CHROME_WA])
    rig.uia.cooperative = False  # e.g. a WhatsApp pop-up has the focus
    r = rig.run("send_message", app="whatsapp", to="Aman", text="hi")
    assert not r.ok and r.error_code == "focus_check_failed"
    assert not any(e.startswith("type:") for e in rig.kb.events)


def test_whatsapp_background_tab_is_selected_without_typing() -> None:
    chrome = AppWindow(20, 200, "YouTube - Google Chrome", "chrome.exe")
    rig = Rig([chrome])

    def select_tab(name: str) -> bool:
        rig.windows.retitle(20, "WhatsApp - Google Chrome")
        return True

    rig.uia.on_tab = select_tab
    r = rig.run("send_message", app="whatsapp", to="Aman", text="hi")
    assert r.ok and rig.opened == [] and "WhatsApp" in rig.uia.pressed
    assert rig.kb.events[0] == "ctrl+alt+/"  # no typing before WhatsApp itself was in front


def test_whatsapp_web_is_opened_when_no_tab_is_found() -> None:
    rig = Rig([])
    rig.windows.windows = lambda: [CHROME_WA] if rig.opened else []  # type: ignore[method-assign]
    r = rig.run("send_message", app="whatsapp", to="Aman", text="hi")
    assert rig.opened == ["https://web.whatsapp.com/"] and r.ok


# -- app control -------------------------------------------------------------------------------

def test_terminals_are_never_driven() -> None:
    rig = Rig([SHELL])
    for tool, args in (("type_text", {"app_name": "powershell", "text": "del *"}),
                       ("press_keys", {"app_name": "powershell", "keys": "enter"}),
                       ("click_in_app", {"app_name": "powershell", "control": "OK"}),
                       ("read_app", {"app_name": "powershell"})):
        r = rig.run(tool, **args)
        assert not r.ok and r.error_code == "not_permitted", tool
    assert rig.kb.events == []


def test_type_press_click_and_read() -> None:
    rig = Rig([NOTEPAD])
    assert rig.run("type_text", app_name="notepad", text="hello").ok
    assert rig.run("press_keys", app_name="notepad", keys="ctrl+s").ok
    assert rig.kb.events == ["type:hello", "ctrl+s"] and len(rig.questions) == 2  # asked first
    assert rig.run("click_in_app", app_name="notepad", control="play").ok
    miss = rig.run("click_in_app", app_name="notepad", control="Print")
    assert not miss.ok and miss.error_code == "not_found"
    read = rig.run("read_app", app_name="notepad")
    assert read.ok and "button: Play" in read.data["controls"] and len(rig.questions) == 4
    assert not rig.run("press_keys", app_name="notepad", keys="win+r").ok


# -- instant command path ----------------------------------------------------------------------

def test_message_commands_are_understood() -> None:
    cases = {
        "text Aman on WhatsApp saying I'm running late": ("whatsapp", "Aman", "I'm running late"),
        "Chiku, message Manas on Discord: are we playing tonight?":
            ("discord", "Manas", "are we playing tonight?"),
        "send a WhatsApp message to Mom saying I reached home": ("whatsapp", "Mom",
                                                                 "I reached home"),
        "on discord, tell Rahul that the server is down": ("discord", "Rahul",
                                                           "the server is down"),
        "tell Priya on whatsapp I will call her later":
            ("whatsapp", "Priya", "I will call her later"),
        "text my sister Asha on WhatsApp good night": ("whatsapp", "Asha", "good night"),
    }
    for text, extra in cases.items():
        intent = parse_command(text)
        assert intent is not None and intent.kind == "message" and intent.extra == extra, text
    assert parse_command("tell me about discord") is None
    assert parse_command("open whatsapp").kind == "open_app"  # type: ignore[union-attr]


def test_assistant_texts_through_the_fast_path(harness: Harness) -> None:
    fw = FakeWindows([CHROME_WA])
    uia = FakeUIA()
    desktop = Desktop(fw, FakeKeyboard(uia), uia, sleep=lambda _s: None)
    for tool in app_control_tools(desktop):
        harness.registry.register(tool)
    a = harness.started()
    a.confirm_handler = lambda _n, _d: True
    a.submit_text("text Aman on WhatsApp saying I'm running late")
    assert wait_for(lambda: any(e.get("tool") == "send_message" for e in harness.audit.tail()))
    assert harness.llm.calls == []  # no model needed
    assert harness.audit.tail()[-1]["ok"] is True


# -- real desktop (opt-in) ---------------------------------------------------------------------

@pytest.mark.system
@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop")
def test_real_keyboard_and_uia_on_a_test_window() -> None:
    """Types Hindi and English with SendInput and presses a button with UI Automation, in a
    throwaway window this test creates (no other app is touched)."""
    import time

    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget

    from chiku.automation.keyboard import Win32Keyboard
    from chiku.automation.uia import UIAutomation
    from chiku.tools.windows_ctl import Win32Backend

    app = QApplication.instance() or QApplication([])

    def pump() -> None:
        for _ in range(25):
            app.processEvents()
            time.sleep(0.02)

    w = QWidget()
    w.setWindowTitle("Chiku automation test")
    box = QLineEdit()
    clicked: list[bool] = []
    button = QPushButton("Press me")
    button.clicked.connect(lambda: clicked.append(True))
    lay = QVBoxLayout(w)
    lay.addWidget(box)
    lay.addWidget(button)
    w.show()
    pump()
    hwnd = int(w.winId())
    assert Win32Backend().focus(hwnd)
    box.setFocus()
    pump()
    Win32Keyboard().type("नमस्ते hello")
    pump()
    assert box.text() == "नमस्ते hello"
    names = [c.name for c in UIAutomation().controls(hwnd)]
    assert "Press me" in names
    assert UIAutomation().press(hwnd, "press me") is not None
    pump()
    assert clicked == [True]
    w.close()

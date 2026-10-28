import pytest

from chiku.cancel import CancelToken
from chiku.config import PermissionSettings
from chiku.tools.apps import AppCatalog
from chiku.tools.base import Permission, ToolContext
from chiku.tools.windows_ctl import (
    AppWindow,
    CloseAppTool,
    ListOpenWindowsTool,
    MediaControlTool,
    SwitchToAppTool,
    Win32Backend,
    find_windows,
)


class FakeBackend:
    def __init__(self, windows: list[AppWindow], focus_ok: bool = True,
                 closes: bool = True) -> None:
        self._windows = windows
        self.focus_ok = focus_ok
        self.closes = closes
        self.focused: list[int] = []
        self.closed: list[int] = []
        self.pressed: list[int] = []

    def windows(self) -> list[AppWindow]:
        return [w for w in self._windows if w.hwnd not in self.closed or not self.closes]

    def focus(self, hwnd: int) -> bool:
        self.focused.append(hwnd)
        return self.focus_ok

    def close(self, hwnd: int) -> None:
        self.closed.append(hwnd)

    def exists(self, hwnd: int) -> bool:
        return not (self.closes and hwnd in self.closed)

    def press(self, vk: int) -> None:
        self.pressed.append(vk)


WINDOWS = [
    AppWindow(1, 10, "Inbox - Gmail - Google Chrome", "chrome.exe"),
    AppWindow(2, 10, "YouTube - Google Chrome", "chrome.exe"),
    AppWindow(3, 20, "notes.txt - Notepad", "Notepad.exe"),
    AppWindow(4, 30, "Spotify Premium", "Spotify.exe"),
]


def ctx() -> ToolContext:
    return ToolContext(CancelToken())


def test_find_windows_by_catalog_exe_process_and_title(catalog: AppCatalog) -> None:
    assert [w.hwnd for w in find_windows(WINDOWS, "chrome", catalog)] == [1, 2]
    assert [w.hwnd for w in find_windows(WINDOWS, "notepad", catalog)] == [3]
    assert [w.hwnd for w in find_windows(WINDOWS, "spotify", None)] == [4]
    assert [w.hwnd for w in find_windows(WINDOWS, "gmail", None)] == [1]
    assert find_windows(WINDOWS, "photoshop", catalog) == []


def test_list_open_windows_groups_by_app() -> None:
    res = ListOpenWindowsTool(FakeBackend(WINDOWS)).run(ListOpenWindowsTool.Input(), ctx())
    apps = {a["app"]: a["windows"] for a in res.data["apps"]}
    assert len(apps["chrome"]) == 2 and "Spotify" in apps


def test_switch_to_app(catalog: AppCatalog) -> None:
    b = FakeBackend(WINDOWS)
    t = SwitchToAppTool(b, catalog)
    assert t.has_match("notepad") and not t.has_match("excel")
    res = t.run(t.Input(app_name="notepad"), ctx())
    assert res.ok and b.focused == [3]
    assert t.run(t.Input(app_name="excel"), ctx()).error_code == "not_open"
    blocked = SwitchToAppTool(FakeBackend(WINDOWS, focus_ok=False), catalog)
    assert blocked.run(blocked.Input(app_name="notepad"), ctx()).error_code == "focus_blocked"


def test_close_app_confirms_and_verifies(catalog: AppCatalog) -> None:
    b = FakeBackend(WINDOWS)
    perms = PermissionSettings()
    t = CloseAppTool(b, catalog, lambda: perms)
    args = t.Input(app_name="chrome")
    assert t.permission_for(args) is Permission.CONFIRM
    assert "2 window(s)" in t.describe(args)
    res = t.run(args, ctx())
    assert res.ok and sorted(b.closed) == [1, 2]
    auto = CloseAppTool(b, catalog, lambda: PermissionSettings(confirm_close_apps=False))
    assert auto.permission_for(args) is Permission.AUTO


def test_close_app_reports_save_prompt(catalog: AppCatalog) -> None:
    t = CloseAppTool(FakeBackend(WINDOWS, closes=False), catalog, lambda: PermissionSettings())
    res = t.run(t.Input(app_name="notepad"), ctx())
    assert res.error_code == "still_open" and "save" in res.message


@pytest.mark.parametrize(("action", "vk", "n"), [("play_pause", 0xB3, 1), ("next_track", 0xB0, 1),
                                                 ("volume_up", 0xAF, 5), ("mute", 0xAD, 1)])
def test_media_keys(action: str, vk: int, n: int) -> None:
    b = FakeBackend([])
    t = MediaControlTool(b)
    res = t.run(t.Input(action=action), ctx())
    assert res.ok and b.pressed == [vk] * n


def test_real_window_listing_runs() -> None:
    wins = Win32Backend().windows()
    assert isinstance(wins, list)
    assert all(w.title and w.hwnd for w in wins)

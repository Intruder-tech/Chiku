"""Window and media control through documented Win32 APIs (no coordinates, no hooks).

* switch:  ShowWindow(SW_RESTORE) + SetForegroundWindow, verified with GetForegroundWindow
* close:   WM_CLOSE (the app's normal close path, so it can ask to save), verified
* media:   media/volume virtual keys (VK_MEDIA_PLAY_PAUSE, VK_VOLUME_UP, ...)
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import psutil
from pydantic import BaseModel, ConfigDict, Field

from chiku.config import PermissionSettings
from chiku.tools.apps import ALIASES, AppCatalog, normalise
from chiku.tools.base import Permission, Tool, ToolContext, ToolResult


@dataclass(frozen=True)
class AppWindow:
    hwnd: int
    pid: int
    title: str
    process: str  # e.g. "chrome.exe"


class WindowBackend(Protocol):
    def windows(self) -> list[AppWindow]: ...

    def focus(self, hwnd: int) -> bool: ...

    def close(self, hwnd: int) -> None: ...

    def exists(self, hwnd: int) -> bool: ...

    def press(self, vk: int) -> None: ...


_IGNORED_TITLES = {"program manager", "windows input experience", "microsoft text input "
                   "application", "settings", "nvidia geforce overlay"}


class Win32Backend:
    """Real implementation. Lists only normal app windows (no tool/owned/cloaked ones)."""

    def __init__(self) -> None:
        self._own_pid = os.getpid()

    def windows(self) -> list[AppWindow]:
        if sys.platform != "win32":
            return []
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
        out: list[AppWindow] = []
        names: dict[int, str] = {}
        proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        gw_owner, gwl_exstyle, ws_ex_toolwindow, dwmwa_cloaked = 4, -20, 0x80, 14

        def cb(hwnd: int, _l: int) -> bool:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, gw_owner):
                return True
            if user32.GetWindowLongW(hwnd, gwl_exstyle) & ws_ex_toolwindow:
                return True
            cloaked = ctypes.c_int(0)
            dwm.DwmGetWindowAttribute(hwnd, dwmwa_cloaked, ctypes.byref(cloaked), 4)
            if cloaked.value:
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n == 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self._own_pid or buf.value.lower() in _IGNORED_TITLES:
                return True
            if pid.value not in names:
                try:
                    names[pid.value] = psutil.Process(pid.value).name()
                except psutil.Error:
                    names[pid.value] = ""
            out.append(AppWindow(int(hwnd), int(pid.value), buf.value, names[pid.value]))
            return True

        user32.EnumWindows(proc_type(cb), 0)
        return out

    def focus(self, hwnd: int) -> bool:
        user32 = ctypes.windll.user32
        sw_restore, sw_minimize = 9, 6
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, sw_restore)
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() == hwnd:
            return True
        # Windows blocks focus stealing from background processes; minimising and restoring
        # is the documented-behaviour way to bring the window to the front anyway.
        user32.ShowWindow(hwnd, sw_minimize)
        user32.ShowWindow(hwnd, sw_restore)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        return bool(user32.GetForegroundWindow() == hwnd)

    def close(self, hwnd: int) -> None:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    def exists(self, hwnd: int) -> bool:
        u = ctypes.windll.user32
        return bool(u.IsWindow(hwnd) and u.IsWindowVisible(hwnd))

    def press(self, vk: int) -> None:
        keyup, extended = 0x2, 0x1
        u = ctypes.windll.user32
        u.keybd_event(vk, 0, extended, 0)
        u.keybd_event(vk, 0, extended | keyup, 0)


def find_windows(windows: list[AppWindow], name: str,
                 catalog: AppCatalog | None) -> list[AppWindow]:
    q = normalise(ALIASES.get(normalise(name), name))
    if not q:
        return []
    exe = ""
    if catalog is not None:
        hit = catalog.best(name)
        if hit:
            exe = hit[0].exe_name
    by_exe = [w for w in windows if exe and w.process.lower() == exe]
    if by_exe:
        return by_exe
    compact = q.replace(" ", "")
    by_proc = [w for w in windows if normalise(Path(w.process).stem).replace(" ", "") in
               (compact, normalise(name).replace(" ", ""))]
    if by_proc:
        return by_proc
    words = [w for w in q.split() if w not in {"microsoft", "google"}] or q.split()
    return [w for w in windows if all(t in normalise(w.title) for t in words)]


def _app_name(w: AppWindow) -> str:
    return Path(w.process).stem if w.process else w.title


class ListWindowsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListOpenWindowsTool(Tool):
    name = "list_open_windows"
    description = "List the apps that currently have open windows, with their window titles."
    Input = ListWindowsInput
    timeout_s = 10.0

    def __init__(self, backend: WindowBackend):
        self.backend = backend

    def run(self, args: ListWindowsInput, ctx: ToolContext) -> ToolResult:
        grouped: dict[str, list[str]] = defaultdict(list)
        for w in self.backend.windows():
            grouped[_app_name(w)].append(w.title[:80])
        apps = [{"app": k, "windows": v[:5]} for k, v in sorted(grouped.items())]
        return ToolResult.success(f"{len(apps)} app(s) have open windows.", apps=apps)


class AppNameInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_name: str = Field(..., min_length=1, max_length=80)


class SwitchToAppTool(Tool):
    name = "switch_to_app"
    description = "Bring an already open app's window to the front (restoring it if minimised)."
    Input = AppNameInput
    timeout_s = 10.0

    def __init__(self, backend: WindowBackend, catalog: AppCatalog | None):
        self.backend = backend
        self.catalog = catalog

    def has_match(self, name: str) -> bool:
        return bool(find_windows(self.backend.windows(), name, self.catalog))

    def run(self, args: AppNameInput, ctx: ToolContext) -> ToolResult:
        hits = find_windows(self.backend.windows(), args.app_name, self.catalog)
        if not hits:
            return ToolResult.failure("not_open", f"{args.app_name} doesn't have an open window.")
        w = hits[0]
        if not self.backend.focus(w.hwnd):
            return ToolResult.failure("focus_blocked", f"Windows didn't let me bring "
                                      f"{_app_name(w)} to the front.", window=w.title)
        return ToolResult.success(f"Switched to {w.title}.", window=w.title)


class CloseAppTool(Tool):
    name = "close_app"
    description = ("Close an app's windows the normal way (like clicking X), so it can ask to "
                   "save unsaved work. Usually needs the user's approval.")
    Input = AppNameInput
    timeout_s = 15.0

    def __init__(self, backend: WindowBackend, catalog: AppCatalog | None,
                 permissions: Callable[[], PermissionSettings]):
        self.backend = backend
        self.catalog = catalog
        self.permissions = permissions

    def has_match(self, name: str) -> bool:
        return bool(find_windows(self.backend.windows(), name, self.catalog))

    def permission_for(self, args: AppNameInput) -> Permission:
        return Permission.CONFIRM if self.permissions().confirm_close_apps else Permission.AUTO

    def describe(self, args: AppNameInput) -> str:
        hits = find_windows(self.backend.windows(), args.app_name, self.catalog)
        if not hits:
            return f"Close {args.app_name}"
        titles = "; ".join(w.title[:50] for w in hits[:3])
        return f"Close {_app_name(hits[0])} ({len(hits)} window(s): {titles})"

    def run(self, args: AppNameInput, ctx: ToolContext) -> ToolResult:
        hits = find_windows(self.backend.windows(), args.app_name, self.catalog)
        if not hits:
            return ToolResult.failure("not_open", f"{args.app_name} doesn't have an open window.")
        for w in hits:
            self.backend.close(w.hwnd)
        deadline = time.monotonic() + 5.0
        remaining = hits
        while time.monotonic() < deadline:
            remaining = [w for w in hits if self.backend.exists(w.hwnd)]
            if not remaining or ctx.cancel.wait(0.2):
                break
        name = _app_name(hits[0])
        if remaining:
            return ToolResult.failure(
                "still_open", f"{name} is still open. It may be asking to save your work.",
                still_open=[w.title for w in remaining])
        return ToolResult.success(f"Closed {name}.", closed_windows=len(hits))


MediaAction = Literal["play_pause", "next_track", "previous_track", "volume_up", "volume_down",
                      "mute"]
_VK = {"play_pause": 0xB3, "next_track": 0xB0, "previous_track": 0xB1, "volume_up": 0xAF,
       "volume_down": 0xAE, "mute": 0xAD}
_SAID = {"play_pause": "Toggled play/pause.", "next_track": "Skipped to the next track.",
         "previous_track": "Went back a track.", "volume_up": "Turned the volume up.",
         "volume_down": "Turned the volume down.", "mute": "Toggled mute."}


class MediaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: MediaAction
    steps: int = Field(5, ge=1, le=25, description="Volume steps (each is about 2%).")


class MediaControlTool(Tool):
    name = "media_control"
    description = ("Control media playback and system volume: play_pause, next_track, "
                   "previous_track, volume_up, volume_down, mute (toggle).")
    Input = MediaInput
    timeout_s = 5.0

    def __init__(self, backend: WindowBackend):
        self.backend = backend

    def run(self, args: MediaInput, ctx: ToolContext) -> ToolResult:
        presses = args.steps if args.action in ("volume_up", "volume_down") else 1
        for _ in range(presses):
            self.backend.press(_VK[args.action])
        # Media keys can't report whether a player reacted; say what was sent.
        return ToolResult.success(_SAID[args.action], action=args.action, key_presses=presses)


def window_tools(backend: WindowBackend, catalog: AppCatalog | None,
                 permissions: Callable[[], PermissionSettings]) -> list[Tool]:
    return [ListOpenWindowsTool(backend), SwitchToAppTool(backend, catalog),
            CloseAppTool(backend, catalog, permissions), MediaControlTool(backend)]

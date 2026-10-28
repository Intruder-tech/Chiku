"""Small, read-only Win32 helpers (window enumeration) via ctypes."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str


def list_windows() -> list[WindowInfo]:
    """Visible, titled top-level windows."""
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    out: list[WindowInfo] = []

    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append(WindowInfo(int(hwnd), int(pid.value), buf.value))
        return True

    user32.EnumWindows(enum_proc_type(_cb), 0)
    return out

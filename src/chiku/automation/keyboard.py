"""Keyboard input with SendInput: key combinations ("ctrl+k") and Unicode typing.

Text is typed as Unicode characters (KEYEVENTF_UNICODE), so any language works and the
clipboard is never touched. The keys go to whichever window has focus; callers focus the
target window first and check it is still in front.
"""

from __future__ import annotations

import ctypes
import re
import sys
import time
from typing import Protocol

_MODIFIERS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12}
_KEYS: dict[str, int] = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "/": 0xBF, ",": 0xBC, ".": 0xBE, "-": 0xBD, "=": 0xBB, ";": 0xBA, "[": 0xDB, "]": 0xDD,
    "'": 0xDE, "`": 0xC0, "\\": 0xDC,
}
_KEYS.update({f"f{i}": 0x6F + i for i in range(1, 13)})
_KEYS.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})
_EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27}
_COMBO = re.compile(r"^(?:(?:ctrl|control|shift|alt)\+){0,3}[^+\s]+$")


class KeyComboError(ValueError):
    pass


def parse_combo(combo: str) -> tuple[list[int], int]:
    """"ctrl+shift+a" -> ([VK_CONTROL, VK_SHIFT], VK_A). The Windows key is not supported:
    it reaches the operating system (Run dialog, settings), not the app."""
    text = combo.strip().lower().replace(" ", "")
    if not _COMBO.match(text):
        raise KeyComboError(f"'{combo}' isn't a key combination like ctrl+s or enter.")
    *mods, key = text.split("+")
    if key not in _KEYS:
        raise KeyComboError(f"Unknown key '{key}'.")
    return [_MODIFIERS[m] for m in mods], _KEYS[key]


class Keyboard(Protocol):
    def press(self, combo: str) -> None: ...

    def type(self, text: str) -> None: ...


# -- SendInput -------------------------------------------------------------------------------
_INPUT_KEYBOARD, _KEYUP, _UNICODE, _EXTENDEDKEY = 1, 0x2, 0x4, 0x1

if sys.platform == "win32":
    from ctypes import wintypes

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _UNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _key(vk: int, up: bool) -> _INPUT:
    flags = (_KEYUP if up else 0) | (_EXTENDEDKEY if vk in _EXTENDED else 0)
    return _INPUT(_INPUT_KEYBOARD, _UNION(ki=_KEYBDINPUT(vk, 0, flags, 0, 0)))


def _unicode(unit: int, up: bool) -> _INPUT:
    return _INPUT(_INPUT_KEYBOARD, _UNION(ki=_KEYBDINPUT(0, unit, _UNICODE | (_KEYUP if up else 0),
                                                         0, 0)))


def _send(events: list[_INPUT]) -> None:
    if not events:
        return
    arr = (_INPUT * len(events))(*events)
    sent = ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
    if sent != len(events):
        raise OSError("Windows blocked the key presses (is the target app running as admin?)")


class Win32Keyboard:
    """The real keyboard. Small pauses keep fast typing from overrunning web apps."""

    def __init__(self, char_delay_s: float = 0.004):
        self.char_delay_s = char_delay_s

    def press(self, combo: str) -> None:
        mods, key = parse_combo(combo)
        events = [_key(m, False) for m in mods] + [_key(key, False), _key(key, True)]
        events += [_key(m, True) for m in reversed(mods)]
        _send(events)
        time.sleep(0.05)

    def type(self, text: str) -> None:
        for line_no, line in enumerate(text.split("\n")):
            if line_no:
                self.press("shift+enter")  # a new line, not "send"
            data = line.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], "little")
                _send([_unicode(unit, False), _unicode(unit, True)])
                if self.char_delay_s:
                    time.sleep(self.char_delay_s)

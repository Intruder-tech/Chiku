"""Windows session state: is the screen locked?"""

from __future__ import annotations

import sys

_DESKTOP_SWITCHDESKTOP = 0x0100


def screen_locked() -> bool:
    """True while the lock screen (or another secure desktop, e.g. a UAC prompt) is showing.

    The input desktop can't be opened or switched to from a normal process then. Background
    wake-word listening pauses while this is true.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    desk = user32.OpenInputDesktop(0, False, _DESKTOP_SWITCHDESKTOP)
    if not desk:
        return True
    try:
        return not user32.SwitchDesktop(desk)
    finally:
        user32.CloseDesktop(desk)

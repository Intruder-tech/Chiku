"""Start Chiku in the background when the user signs in to Windows (opt-in).

A per-user `Run` registry value, so no administrator rights are needed. Windows' own
Task Manager → Startup apps can also switch it off. The uninstaller removes the value.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Chiku"


def command() -> str | None:
    """What Windows runs at sign-in, or None where autostart isn't supported."""
    if sys.platform != "win32":
        return None
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    # Development install: pythonw has no console window.
    exe = Path(sys.executable).with_name("pythonw.exe")
    return f'"{exe if exe.exists() else sys.executable}" -m chiku --background'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False
    return bool(value)


def set_enabled(on: bool) -> None:
    """Add or remove the Run value. Raises OSError if the registry refuses."""
    cmd = command()
    if cmd is None:
        return
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        if on:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
            log.info("autostart on: %s", cmd)
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
                log.info("autostart off")
            except FileNotFoundError:
                pass

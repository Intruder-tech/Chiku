"""Qt application bootstrap."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from chiku.branding import APP_USER_MODEL_ID, ICON, asset
from chiku.config import Settings
from chiku.paths import AppPaths
from chiku.ui import theme

# The installer waits on this mutex (Inno Setup AppMutex) so upgrades never replace files
# while Chiku is running.
APP_MUTEX = "ChikuDesktopAssistantMutex"


def _windows_identity() -> object | None:
    if sys.platform != "win32":
        return None
    # Own taskbar identity (Chiku's icon, not Python's) and the installer's mutex.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    return ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)


def run_gui(settings: Settings, paths: AppPaths) -> int:
    mutex = _windows_identity()
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setApplicationName("Chiku")
    app.setWindowIcon(QIcon(str(asset(ICON))))
    app.setStyleSheet(theme.apply(settings.ui.theme))
    app.setQuitOnLastWindowClosed(False)  # compact mode hides the main window

    # One instance only: two copies would fight over the microphone.
    lock = QLockFile(str(paths.data_dir / "chiku.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Chiku", "Chiku is already running.")
        return 0

    from chiku.ui.main_window import MainWindow

    win = MainWindow(settings, paths)
    win.show()
    QTimer.singleShot(400, win.maybe_onboard)
    code = app.exec()
    lock.unlock()
    if mutex:
        ctypes.windll.kernel32.CloseHandle(mutex)
    return code

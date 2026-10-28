"""Warm light and dark themes derived from the Chiku mascot (yellow chick, mint beanie).

Colours are module attributes so widgets that paint themselves (waveform, mascot) read the
current theme at paint time; `apply()` switches theme live and returns the Qt stylesheet.
"""

from __future__ import annotations

import sys
from typing import Literal

Mode = Literal["system", "light", "dark"]

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "BG": "#14120f", "SURFACE": "#1d1a16", "SURFACE_2": "#27231d", "BORDER": "#3a342b",
        "TEXT": "#f3eee4", "MUTED": "#a69c8c", "FAINT": "#6e665a", "ACCENT": "#f2c94c",
        "ACCENT_TEXT": "#1a1505", "MINT": "#9fd3c0", "USER_BUBBLE": "#2e2920",
        "WHITE": "#ffffff", "ERROR": "#e5676b", "SIDEBAR": "#100e0c",
    },
    "light": {
        "BG": "#fbf7ef", "SURFACE": "#ffffff", "SURFACE_2": "#f3ecdd", "BORDER": "#e4dac6",
        "TEXT": "#241f17", "MUTED": "#7a705f", "FAINT": "#a89e8c", "ACCENT": "#e8b923",
        "ACCENT_TEXT": "#241f17", "MINT": "#4f9a80", "USER_BUBBLE": "#f6e7b8",
        "WHITE": "#ffffff", "ERROR": "#c94b4f", "SIDEBAR": "#f3ecdd",
    },
}
FONT = "Segoe UI Variable Text, Segoe UI, sans-serif"

# Current colours (dark until apply() is called).
BG = SURFACE = SURFACE_2 = BORDER = TEXT = MUTED = FAINT = ACCENT = ""
ACCENT_TEXT = MINT = USER_BUBBLE = WHITE = ERROR = SIDEBAR = ""
current_mode = "dark"


def windows_prefers_light() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion"
                            r"\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return False


def resolve(mode: str) -> str:
    if mode == "system":
        return "light" if windows_prefers_light() else "dark"
    return mode if mode in PALETTES else "dark"


def _set(mode: str) -> None:
    global current_mode
    current_mode = mode
    globals().update(PALETTES[mode])


def stylesheet() -> str:
    c = PALETTES[current_mode]
    return f"""
* {{ font-family: {FONT}; font-size: 10.5pt; color: {c['TEXT']}; }}
QMainWindow, QDialog, QWizard, QWidget#central, QWidget#page {{ background: {c['BG']}; }}
QFrame#sidebar {{ background: {c['SIDEBAR']}; border-right: 1px solid {c['BORDER']}; }}
QPushButton#nav {{ background: transparent; border: none; border-radius: 10px; text-align: left;
                   padding: 9px 12px; color: {c['MUTED']}; font-weight: 600; }}
QPushButton#nav:hover {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; }}
QPushButton#nav:checked {{ background: {c['SURFACE_2']}; color: {c['TEXT']};
                           border-left: 3px solid {c['ACCENT']}; }}
QLabel#wordmark {{ font-size: 15pt; font-weight: 700; letter-spacing: 0.5px; }}
QLabel#badge {{ color: {c['MUTED']}; font-size: 8.5pt; padding: 2px 8px;
                border: 1px solid {c['BORDER']}; border-radius: 9px; }}
QLabel#state {{ color: {c['MUTED']}; font-size: 10pt; }}
QLabel#state[error="true"] {{ color: {c['ERROR']}; }}
QLabel#status {{ color: {c['FAINT']}; font-size: 8.5pt; }}
QFrame#userBubble {{ background: {c['USER_BUBBLE']}; border-radius: 14px; }}
QFrame#assistantBubble {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']};
                          border-radius: 14px; }}
QLabel#bubbleText {{ font-size: 10.5pt; }}
QLabel#meta, QLabel#time {{ color: {c['FAINT']}; font-size: 8pt; }}
QLabel#notice {{ color: {c['MUTED']}; font-size: 9pt; font-style: italic; }}
QLabel#tool {{ color: {c['MUTED']}; font-size: 9pt; }}
QLabel#sources {{ font-size: 9pt; padding: 4px 0 2px 0; }}
QLabel#pageTitle {{ font-size: 14pt; font-weight: 700; }}
QWidget#welcome {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['BG']}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['BORDER']}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget {{
    background: {c['SURFACE']}; border: 1px solid {c['BORDER']}; border-radius: 10px;
    padding: 7px 10px; selection-background-color: {c['ACCENT']};
    selection-color: {c['ACCENT_TEXT']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {c['ACCENT']}; }}
QHeaderView::section {{ background: {c['SURFACE_2']}; border: none; padding: 6px; }}
QComboBox QAbstractItemView {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']}; }}
QPushButton {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']}; border-radius: 10px;
               padding: 7px 14px; }}
QPushButton:hover {{ border-color: {c['ACCENT']}; }}
QPushButton:pressed {{ background: {c['SURFACE_2']}; }}
QPushButton:disabled {{ color: {c['FAINT']}; }}
QPushButton#primary {{ background: {c['ACCENT']}; color: {c['ACCENT_TEXT']};
                       border-color: {c['ACCENT']}; font-weight: 600; }}
QPushButton#mic {{ border-radius: 26px; min-width: 52px; max-width: 52px; min-height: 52px;
                   max-height: 52px; font-size: 9pt; font-weight: 700; padding: 0;
                   border: 2px solid {c['BORDER']}; }}
QPushButton#mic:checked {{ background: {c['ACCENT']}; color: {c['ACCENT_TEXT']};
                           border-color: {c['ACCENT']}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {c['MUTED']};
                     padding: 4px 8px; }}
QPushButton#ghost:hover {{ color: {c['TEXT']}; }}
QWidget#compact {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']};
                   border-radius: 18px; }}
QTabWidget::pane {{ border: 1px solid {c['BORDER']}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {c['MUTED']}; padding: 8px 12px; border: none; }}
QTabBar::tab:selected {{ color: {c['TEXT']}; border-bottom: 2px solid {c['ACCENT']}; }}
QCheckBox {{ spacing: 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['BORDER']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['ACCENT']}; width: 14px; margin: -5px 0;
                              border-radius: 7px; }}
QProgressBar {{ background: {c['SURFACE_2']}; border: none; border-radius: 4px; height: 8px;
                text-align: center; }}
QProgressBar::chunk {{ background: {c['ACCENT']}; border-radius: 4px; }}
QToolTip {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; border: 1px solid {c['BORDER']}; }}
"""


def apply(mode: str = "system") -> str:
    """Switch the current palette; returns the stylesheet for QApplication.setStyleSheet."""
    _set(resolve(mode))
    return stylesheet()


_set("dark")
QSS = stylesheet()  # backwards compatibility for code that imported the old constant

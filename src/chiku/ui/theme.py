"""Warm light and dark themes derived from the Chiku mascot (yellow chick, mint beanie).

Colours are module attributes so widgets that paint themselves (waveform, mascot) read the
current theme at paint time; `apply()` switches theme live and returns the Qt stylesheet.
"""

from __future__ import annotations

import sys
from typing import Literal

Mode = Literal["system", "light", "dark"]

PALETTES: dict[str, dict[str, str]] = {
    # Warm neutrals (calm, desktop-assistant feel) with the mascot's yellow as the one accent.
    "dark": {
        "BG": "#1d1c1a", "SURFACE": "#262523", "SURFACE_2": "#302f2c", "BORDER": "#3b3936",
        "TEXT": "#ece9e3", "MUTED": "#a39e94", "FAINT": "#6f6b63", "ACCENT": "#f2c94c",
        "ACCENT_TEXT": "#1a1505", "MINT": "#9fd3c0", "USER_BUBBLE": "#33312d",
        "WHITE": "#ffffff", "ERROR": "#e5676b", "SIDEBAR": "#171615",
    },
    "light": {
        "BG": "#faf9f6", "SURFACE": "#ffffff", "SURFACE_2": "#f0ede6", "BORDER": "#e3dfd5",
        "TEXT": "#26231e", "MUTED": "#736c61", "FAINT": "#a59e92", "ACCENT": "#e3b21c",
        "ACCENT_TEXT": "#241f17", "MINT": "#4f9a80", "USER_BUBBLE": "#efebe2",
        "WHITE": "#ffffff", "ERROR": "#c94b4f", "SIDEBAR": "#f3f1ec",
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
QToolTip {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; border: 1px solid {c['BORDER']};
            padding: 4px 6px; }}

/* -- sidebar -------------------------------------------------------------------------- */
QFrame#sidebar {{ background: {c['SIDEBAR']}; border-right: 1px solid {c['BORDER']}; }}
QLabel#wordmark {{ font-size: 13pt; font-weight: 700; letter-spacing: 0.3px; }}
QPushButton#newChat {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']};
                       border-radius: 10px; padding: 8px 12px; text-align: left;
                       font-weight: 600; }}
QPushButton#newChat:hover {{ border-color: {c['ACCENT']}; }}
QPushButton#nav {{ background: transparent; border: none; border-radius: 8px; text-align: left;
                   padding: 7px 10px; color: {c['MUTED']}; font-weight: 500; }}
QPushButton#nav:hover {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; }}
QPushButton#nav:checked {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; font-weight: 600; }}
QPushButton#userChip {{ background: transparent; border: none; border-radius: 10px;
                        text-align: left; padding: 6px 8px; font-weight: 600; }}
QPushButton#userChip:hover {{ background: {c['SURFACE_2']}; }}
QLabel#badge {{ color: {c['FAINT']}; font-size: 8pt; padding: 0 4px; }}
QListWidget#chats {{ background: transparent; border: none; outline: none; }}
QListWidget#chats::item {{ padding: 6px 8px; border-radius: 8px; color: {c['MUTED']}; }}
QListWidget#chats::item:hover {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; }}
QListWidget#chats::item:selected {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; }}

/* -- buttons ---------------------------------------------------------------------------- */
QPushButton {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']}; border-radius: 9px;
               padding: 7px 14px; }}
QPushButton:hover {{ background: {c['SURFACE_2']}; }}
QPushButton:pressed {{ background: {c['BORDER']}; }}
QPushButton:disabled {{ color: {c['FAINT']}; }}
QPushButton#primary {{ background: {c['ACCENT']}; color: {c['ACCENT_TEXT']};
                       border-color: {c['ACCENT']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['ACCENT']}; border-color: {c['TEXT']}; }}
QPushButton#danger {{ color: {c['ERROR']}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {c['MUTED']};
                     padding: 6px 10px; border-radius: 8px; }}
QPushButton#ghost:hover {{ color: {c['TEXT']}; background: {c['SURFACE_2']}; }}
QPushButton#iconButton {{ background: transparent; border: none; border-radius: 8px;
                          padding: 6px; min-width: 20px; min-height: 20px; }}
QPushButton#iconButton:hover {{ background: {c['SURFACE_2']}; }}
QPushButton#iconButton:checked {{ background: {c['ACCENT']}; }}
QWidget#segmented {{ background: {c['SURFACE_2']}; border-radius: 10px; }}
QPushButton#segment {{ background: transparent; border: none; border-radius: 8px;
                       padding: 6px 16px; color: {c['MUTED']}; }}
QPushButton#segment:checked {{ background: {c['SURFACE']}; color: {c['TEXT']};
                               font-weight: 600; }}

/* -- chat -------------------------------------------------------------------------------- */
QLabel#greeting {{ font-size: 22pt; font-weight: 600; }}
QLabel#greetingSub {{ color: {c['MUTED']}; font-size: 11pt; }}
QPushButton#chip {{ background: transparent; border: 1px solid {c['BORDER']};
                    border-radius: 16px; padding: 6px 14px; color: {c['MUTED']}; }}
QPushButton#chip:hover {{ color: {c['TEXT']}; border-color: {c['ACCENT']}; }}
QFrame#composer {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']};
                   border-radius: 22px; }}
QLineEdit#composerInput {{ background: transparent; border: none; padding: 6px 4px;
                           font-size: 11pt; }}
QPushButton#sendButton {{ background: {c['ACCENT']}; border: none; border-radius: 16px;
                          min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
                          padding: 0; }}
QPushButton#sendButton:hover {{ background: {c['ACCENT']}; border: 1px solid {c['TEXT']}; }}
QPushButton#mic {{ background: transparent; border: 1px solid {c['BORDER']};
                   border-radius: 16px; min-width: 32px; max-width: 32px; min-height: 32px;
                   max-height: 32px; padding: 0; }}
QPushButton#mic:hover {{ background: {c['SURFACE_2']}; }}
QPushButton#mic:checked {{ background: {c['ACCENT']}; border-color: {c['ACCENT']}; }}
QFrame#userBubble {{ background: {c['USER_BUBBLE']}; border-radius: 18px; }}
QFrame#assistantBubble {{ background: transparent; border: none; }}
QLabel#bubbleText {{ font-size: 11pt; }}
QLabel#meta, QLabel#time {{ color: {c['FAINT']}; font-size: 8pt; }}
QLabel#notice {{ color: {c['MUTED']}; font-size: 9pt; font-style: italic; }}
QLabel#tool {{ color: {c['MUTED']}; font-size: 9pt; }}
QLabel#sources {{ font-size: 9pt; padding: 4px 0 2px 0; }}
QWidget#statusStrip {{ background: {c['BG']}; border-bottom: 1px solid {c['BORDER']}; }}
QLabel#state {{ color: {c['MUTED']}; font-size: 9.5pt; }}
QLabel#state[error="true"] {{ color: {c['ERROR']}; }}
QLabel#status {{ color: {c['FAINT']}; font-size: 8.5pt; }}

/* -- pages ------------------------------------------------------------------------------- */
QLabel#pageTitle {{ font-size: 18pt; font-weight: 600; }}
QLabel#pageSubtitle {{ color: {c['MUTED']}; font-size: 10pt; }}
QFrame#card {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']};
               border-radius: 14px; }}
QLabel#cardTitle {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#cardDescription {{ color: {c['MUTED']}; font-size: 9.5pt; }}
QLabel#sectionTitle {{ color: {c['FAINT']}; font-size: 8.5pt; font-weight: 700;
                       letter-spacing: 0.6px; }}
QLabel#statNumber {{ font-size: 18pt; font-weight: 600; color: {c['TEXT']}; }}
QFrame#context {{ background: {c['SIDEBAR']}; border-left: 1px solid {c['BORDER']}; }}
QListWidget#settingsNav {{ background: transparent; border: none; outline: none; }}
QListWidget#settingsNav::item {{ padding: 8px 12px; border-radius: 8px; color: {c['MUTED']}; }}
QListWidget#settingsNav::item:hover {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; }}
QListWidget#settingsNav::item:selected {{ background: {c['SURFACE_2']}; color: {c['TEXT']};
                                          font-weight: 600; }}
QFrame#footerBar {{ background: {c['BG']}; border-top: 1px solid {c['BORDER']}; }}
QWidget#welcome {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['BG']}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['BORDER']}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c['FAINT']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['BORDER']}; border-radius: 4px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -- inputs -------------------------------------------------------------------------------- */
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {c['SURFACE']}; border: 1px solid {c['BORDER']}; border-radius: 9px;
    padding: 6px 10px; selection-background-color: {c['ACCENT']};
    selection-color: {c['ACCENT_TEXT']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {c['ACCENT']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']};
                              selection-background-color: {c['SURFACE_2']};
                              selection-color: {c['TEXT']}; outline: none; }}
QCheckBox {{ spacing: 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['BORDER']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['ACCENT']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['SURFACE']}; border: 2px solid {c['ACCENT']};
                              width: 14px; height: 14px; margin: -7px 0; border-radius: 9px; }}
QProgressBar {{ background: {c['SURFACE_2']}; border: none; border-radius: 4px; height: 8px;
                text-align: center; }}
QProgressBar::chunk {{ background: {c['ACCENT']}; border-radius: 4px; }}

/* -- tables and tabs ------------------------------------------------------------------------ */
QTableWidget, QTableWidget#listTable {{ background: {c['SURFACE']};
    border: 1px solid {c['BORDER']}; border-radius: 12px; gridline-color: transparent;
    selection-background-color: {c['SURFACE_2']}; selection-color: {c['TEXT']}; }}
QTableWidget::item {{ padding: 0 10px; border-bottom: 1px solid {c['SURFACE_2']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {c['SURFACE']}; color: {c['MUTED']}; border: none;
                        border-bottom: 1px solid {c['BORDER']}; padding: 8px 10px;
                        font-size: 9pt; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['SURFACE']}; border: none; }}
QTabWidget::pane {{ border: none; top: 0; }}
QTabBar::tab {{ background: transparent; color: {c['MUTED']}; padding: 8px 14px; border: none;
                margin-right: 4px; }}
QTabBar::tab:hover {{ color: {c['TEXT']}; }}
QTabBar::tab:selected {{ color: {c['TEXT']}; border-bottom: 2px solid {c['ACCENT']}; }}

/* -- minimised modes, palette ------------------------------------------------------------- */
QWidget#compact {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']};
                   border-radius: 18px; }}
QPushButton#notchMic {{ border-radius: 17px; min-width: 34px; max-width: 34px; min-height: 34px;
                        max-height: 34px; font-size: 8pt; font-weight: 700; padding: 0;
                        border: 2px solid {c['BORDER']}; }}
QPushButton#notchMic:checked {{ background: {c['ACCENT']}; color: {c['ACCENT_TEXT']};
                                border-color: {c['ACCENT']}; }}
QLabel#notchReply {{ font-size: 9.5pt; }}
QDialog#palette {{ background: {c['SURFACE']}; border: 1px solid {c['BORDER']};
                   border-radius: 14px; }}
QListWidget#paletteList {{ background: transparent; border: none; outline: none; }}
QListWidget#paletteList::item {{ padding: 7px 10px; border-radius: 8px; }}
QListWidget#paletteList::item:selected {{ background: {c['SURFACE_2']}; color: {c['TEXT']}; }}
"""


def apply(mode: str = "system") -> str:
    """Switch the current palette; returns the stylesheet for QApplication.setStyleSheet."""
    _set(resolve(mode))
    return stylesheet()


_set("dark")
QSS = stylesheet()  # backwards compatibility for code that imported the old constant

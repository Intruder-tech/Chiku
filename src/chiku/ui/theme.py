"""Dark, restrained palette: black, charcoal, white and neutral grays."""

from __future__ import annotations

BG = "#0d0d0f"
SURFACE = "#16161a"
SURFACE_2 = "#1e1e23"
BORDER = "#2a2a30"
TEXT = "#ececee"
MUTED = "#8b8b94"
FAINT = "#55555d"
WHITE = "#ffffff"
ERROR = "#e5676b"  # the only non-neutral color; used for errors only

FONT = "Segoe UI Variable Text, Segoe UI, sans-serif"

QSS = f"""
* {{ font-family: {FONT}; font-size: 10.5pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#central {{ background: {BG}; }}
QLabel#wordmark {{ font-size: 15pt; font-weight: 600; letter-spacing: 0.5px; }}
QLabel#badge {{ color: {MUTED}; font-size: 9pt; padding: 2px 8px; border: 1px solid {BORDER};
                border-radius: 9px; }}
QLabel#state {{ color: {MUTED}; font-size: 10pt; }}
QLabel#state[error="true"] {{ color: {ERROR}; }}
QLabel#status {{ color: {FAINT}; font-size: 8.5pt; }}
QLabel#userBubble {{ background: {SURFACE_2}; border-radius: 12px; padding: 8px 12px; }}
QLabel#assistantText {{ padding: 2px 2px; }}
QLabel#meta {{ color: {FAINT}; font-size: 8pt; }}
QLabel#notice {{ color: {MUTED}; font-size: 9pt; font-style: italic; }}
QLabel#tool {{ color: {MUTED}; font-size: 9pt; }}
QLabel#sources {{ font-size: 9pt; padding: 4px 0 2px 0; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    selection-background-color: #3a3a42; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {MUTED}; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER};
                               selection-background-color: {SURFACE_2}; }}
QPushButton {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
               padding: 7px 14px; }}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:pressed {{ background: {SURFACE_2}; }}
QPushButton:disabled {{ color: {FAINT}; }}
QPushButton#mic {{ border-radius: 26px; min-width: 52px; max-width: 52px; min-height: 52px;
                   max-height: 52px; font-size: 9pt; font-weight: 600; padding: 0; }}
QPushButton#mic:checked {{ background: {WHITE}; color: {BG}; border-color: {WHITE}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {MUTED}; padding: 4px 8px; }}
QPushButton#ghost:hover {{ color: {TEXT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 12px; border: none; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {TEXT}; }}
QCheckBox {{ spacing: 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; margin: -5px 0;
                              border-radius: 7px; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""

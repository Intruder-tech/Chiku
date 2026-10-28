"""Line icons from Windows' own icon font (Segoe Fluent Icons on Windows 11, Segoe MDL2 Assets
on Windows 10), drawn into QIcons in the current theme colour. No image files to ship."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from chiku.ui import theme

FAMILIES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]

# Code points shared by both fonts.
ADD = ""
CHAT = ""
PERSON = ""
APPS = ""
MEMORY = ""  # library
ACTIVITY = ""  # history
SETTINGS = ""
MIC = ""
SEND = ""
STOP = ""
SUN = ""
MOON = ""
MINIMISE = ""  # back to window
PANEL = ""  # dock right
SEARCH = ""


def icon_font(px: int) -> QFont:
    f = QFont()
    f.setFamilies(FAMILIES)
    f.setPixelSize(px)
    return f


def glyph_icon(glyph: str, colour: str | None = None, px: int = 18) -> QIcon:
    """A crisp icon (2x for high-DPI screens) in `colour` (default: the theme's text colour)."""
    scale = 2
    pix = QPixmap(px * scale, px * scale)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(icon_font(int(px * scale * 0.82)))
    p.setPen(QColor(colour or theme.TEXT))
    p.drawText(QRectF(0, 0, px * scale, px * scale), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    pix.setDevicePixelRatio(scale)
    return QIcon(pix)

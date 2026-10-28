"""Reusable UI components (chat view, sidebar, voice header, input bar)."""

from __future__ import annotations

from typing import TypeVar

from PySide6.QtWidgets import QLabel, QPushButton, QWidget

W = TypeVar("W", bound=QWidget)


def named(w: W, name: str) -> W:
    w.setObjectName(name)
    return w


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def button(text: str, name: str = "") -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    return b

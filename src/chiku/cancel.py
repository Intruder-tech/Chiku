"""Cooperative cancellation shared by the LLM, TTS, tools and playback."""

from __future__ import annotations

import threading


class Cancelled(Exception):
    """Raised when work is abandoned because its CancelToken fired."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = ""

    def cancel(self, reason: str = "cancelled") -> None:
        if not self._event.is_set():
            self.reason = reason
            self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled(self.reason)

    def wait(self, timeout: float) -> bool:
        """Sleep up to `timeout` seconds; returns True if cancelled meanwhile."""
        return self._event.wait(timeout)

"""Low-latency, interruptible speech playback."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

from chiku.audio.devices import resolve, stream_kwargs
from chiku.cancel import CancelToken

log = logging.getLogger(__name__)


class AudioOutputError(RuntimeError):
    pass


class AudioPlayer:
    """Keeps an output stream open and plays queued float32 chunks.

    `stop()` takes effect on the next audio block (~40 ms), which is what makes
    barge-in feel instant.
    """

    def __init__(self, device_name: str | None, samplerate: int = 24000, blocksize: int = 960):
        self.device_name = device_name
        self.samplerate = samplerate
        self.blocksize = blocksize
        self._stream: sd.OutputStream | None = None
        self._chunks: deque[np.ndarray] = deque()
        self._pos = 0
        self._lock = threading.Lock()
        self._drained = threading.Event()
        self._drained.set()
        self._last_audio = 0.0
        self.level = 0.0

    @property
    def is_playing(self) -> bool:
        return not self._drained.is_set()

    def _ensure_stream(self) -> None:
        if self._stream is not None and self._stream.active:
            return
        self.close()
        try:
            dev = resolve(self.device_name, "output")
            stream = sd.OutputStream(
                samplerate=self.samplerate, channels=1, dtype="float32",
                blocksize=self.blocksize, callback=self._callback, **stream_kwargs(dev),
            )
            stream.start()
        except (sd.PortAudioError, LookupError) as exc:
            raise AudioOutputError(f"Can't open the speaker: {exc}") from exc
        self._stream = stream

    def play(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        self._ensure_stream()
        with self._lock:
            self._chunks.append(audio.astype(np.float32, copy=False))
            self._drained.clear()

    def stop(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._pos = 0
            self._drained.set()
        self.level = 0.0

    def queued_seconds(self) -> float:
        with self._lock:
            n = sum(len(c) for c in self._chunks) - self._pos
        return max(0, n) / self.samplerate

    def wait_until_drained(self, cancel: CancelToken) -> bool:
        """Block until queued audio has played. False if cancelled.

        Raises AudioOutputError if the device stops consuming audio (unplugged).
        """
        stall_deadline = time.monotonic() + self.queued_seconds() + 3.0
        while not self._drained.wait(0.05):
            if cancel.cancelled:
                return False
            if time.monotonic() > stall_deadline:
                self.stop()
                self.close()
                raise AudioOutputError("The speaker stopped responding.")
            stall_deadline = max(stall_deadline, time.monotonic() + self.queued_seconds() + 1.0) \
                if self._stream is not None and self._stream.active else stall_deadline
        return not cancel.cancelled

    def close_if_idle(self, idle_s: float = 20.0) -> None:
        if self._stream is not None and self._drained.is_set() \
                and time.monotonic() - self._last_audio > idle_s:
            self.close()

    def close(self) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.abort()
                s.close()
            except sd.PortAudioError as exc:
                log.debug("speaker close: %s", exc)

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: ANN001
        out = outdata[:, 0]
        filled = 0
        with self._lock:
            while filled < frames and self._chunks:
                cur = self._chunks[0]
                take = min(frames - filled, len(cur) - self._pos)
                out[filled:filled + take] = cur[self._pos:self._pos + take]
                filled += take
                self._pos += take
                if self._pos >= len(cur):
                    self._chunks.popleft()
                    self._pos = 0
            if filled < frames:
                out[filled:] = 0.0
            if filled:
                self._last_audio = time.monotonic()
            elif not self._chunks:
                self._drained.set()
        self.level = float(np.sqrt(np.mean(out * out))) if filled else 0.0

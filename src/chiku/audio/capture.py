"""Microphone capture: 16 kHz mono float32 frames of 512 samples (32 ms).

Audio is held only in memory and only while the microphone is switched on by
the user. Nothing is written to disk.
"""

from __future__ import annotations

import logging
import queue
import threading

import numpy as np
import sounddevice as sd

from chiku.audio.devices import resolve, stream_kwargs

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 512


class AudioInputError(RuntimeError):
    pass


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Cheap anti-aliased resampler, good enough for speech recognition."""
    if src == dst or len(x) == 0:
        return x.astype(np.float32, copy=False)
    if src > dst:
        k = int(round(src / dst))
        if k > 1:
            x = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same")
    n_out = int(round(len(x) * dst / src))
    t_out = np.linspace(0, len(x) - 1, n_out, dtype=np.float64)
    return np.interp(t_out, np.arange(len(x)), x).astype(np.float32)


class MicCapture:
    def __init__(self, device_name: str | None, max_queue_s: float = 10.0):
        self.device_name = device_name
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=int(max_queue_s * RATE / FRAME))
        self._stream: sd.InputStream | None = None
        self._src_rate = RATE
        self._pending = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self.level = 0.0
        self.overflows = 0
        self.dropped = 0

    @property
    def active(self) -> bool:
        s = self._stream
        return bool(s is not None and s.active)

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            dev = resolve(self.device_name, "input")
        except LookupError as exc:
            raise AudioInputError(str(exc)) from exc
        kw = stream_kwargs(dev)
        try:
            stream = sd.InputStream(
                samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME,
                callback=self._callback, **kw,
            )
            self._src_rate = RATE
        except sd.PortAudioError:
            # Device refuses 16 kHz mono: capture natively and convert ourselves.
            info = sd.query_devices(kw["device"], "input")
            self._src_rate = int(info["default_samplerate"])
            try:
                stream = sd.InputStream(
                    samplerate=self._src_rate, channels=min(2, int(info["max_input_channels"])),
                    dtype="float32", callback=self._callback, device=kw["device"],
                )
            except sd.PortAudioError as exc:
                raise AudioInputError(f"Can't open the microphone: {exc}") from exc
        try:
            stream.start()
        except sd.PortAudioError as exc:
            stream.close()
            raise AudioInputError(f"Can't start the microphone: {exc}") from exc
        self._stream = stream
        log.info("mic started (rate %d)", self._src_rate)

    def stop(self) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.abort()
                s.close()
            except sd.PortAudioError as exc:
                log.debug("mic close: %s", exc)
        self.level = 0.0
        with self._lock:
            self._pending = np.zeros(0, dtype=np.float32)
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        log.info("mic stopped")

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: ANN001
        if status and status.input_overflow:
            self.overflows += 1
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
        if self._src_rate != RATE:
            mono = resample(mono, self._src_rate, RATE)
        self.level = float(np.sqrt(np.mean(mono * mono))) if len(mono) else 0.0
        with self._lock:
            buf = np.concatenate([self._pending, mono.astype(np.float32, copy=False)])
            n = len(buf) // FRAME
            for i in range(n):
                frame = buf[i * FRAME:(i + 1) * FRAME].copy()
                try:
                    self._q.put_nowait(frame)
                except queue.Full:
                    self.dropped += 1
            self._pending = buf[n * FRAME:]

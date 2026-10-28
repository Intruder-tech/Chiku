"""Audio device discovery.

Devices are remembered by name, because PortAudio indices change whenever a
headset is plugged in. WASAPI entries are preferred: they carry full names and
let Windows convert sample rate/channels for us (auto_convert).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import sounddevice as sd

log = logging.getLogger(__name__)

Kind = Literal["input", "output"]
_PREFERRED_HOSTAPIS = ("Windows WASAPI", "MME", "Windows DirectSound")


class DeviceNotFound(LookupError):
    pass


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    hostapi: str
    channels: int
    default_samplerate: float


def rescan() -> None:
    """Refresh PortAudio's device list so hot-plugged devices show up."""
    try:
        sd._terminate()  # noqa: SLF001 - documented workaround for rescanning devices
        sd._initialize()  # noqa: SLF001
    except Exception as exc:  # pragma: no cover - depends on driver state
        log.warning("audio rescan failed: %s", exc)


def list_devices(kind: Kind) -> list[AudioDevice]:
    hostapis = [h["name"] for h in sd.query_hostapis()]
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    found: dict[str, AudioDevice] = {}
    for api in _PREFERRED_HOSTAPIS:
        for i, d in enumerate(sd.query_devices()):
            if hostapis[d["hostapi"]] != api or d[key] <= 0:
                continue
            name = str(d["name"]).strip()
            if not name or name.endswith("()") or "Sound Mapper" in name or "Primary Sound" in name:
                continue
            # MME truncates names to 31 chars; skip if a WASAPI entry already covers it.
            if any(n.startswith(name) for n in found):
                continue
            found.setdefault(
                name, AudioDevice(i, name, api, int(d[key]), float(d["default_samplerate"]))
            )
    return list(found.values())


def default_device_name(kind: Kind) -> str:
    try:
        idx = sd.default.device[0 if kind == "input" else 1]
        if idx is None or idx < 0:
            return "none"
        return str(sd.query_devices(idx)["name"])
    except Exception:
        return "unknown"


def resolve(name: str | None, kind: Kind) -> AudioDevice | None:
    """Map a saved device name to a current device. None means 'system default'."""
    if not name:
        return None
    devices = list_devices(kind)
    for d in devices:
        if d.name == name:
            return d
    for d in devices:
        if d.name.startswith(name) or name.startswith(d.name):
            return d
    raise DeviceNotFound(f"{kind} device '{name}' is not connected")


def stream_kwargs(dev: AudioDevice | None) -> dict[str, Any]:
    if dev is None:
        return {"device": None}
    kw: dict[str, Any] = {"device": dev.index}
    if dev.hostapi == "Windows WASAPI":
        kw["extra_settings"] = sd.WasapiSettings(auto_convert=True)
    return kw

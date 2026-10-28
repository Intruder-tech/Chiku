"""Ollama model management: list, inspect, pull (with disk check), delete."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from chiku.cancel import Cancelled, CancelToken

_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-/]{0,120}(?::[a-zA-Z0-9._\-]{1,80})?$")
DISK_MARGIN_BYTES = 2 * 1024**3


class ModelError(RuntimeError):
    """Message is safe to show."""


@dataclass
class InstalledModel:
    name: str
    size_gb: float
    parameter_size: str
    quantization: str
    family: str
    modified: str


def validate_name(name: str) -> str:
    name = name.strip()
    if not _NAME.match(name):
        raise ModelError(f"'{name}' isn't a valid Ollama model name (e.g. qwen3:4b).")
    return name


def ollama_models_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    return Path(env) if env else Path.home() / ".ollama" / "models"


def free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


class OllamaManager:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None,
                 free_space: Callable[[], int] = lambda: free_bytes(ollama_models_dir())):
        self.base = base_url.rstrip("/")
        self._client = httpx.Client(transport=transport, timeout=httpx.Timeout(15.0, connect=3.0))
        self._free = free_space

    def _get(self, path: str) -> dict:
        try:
            r = self._client.get(self.base + path)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelError(f"Ollama isn't reachable at {self.base}.") from exc

    def installed(self) -> list[InstalledModel]:
        out = []
        for m in self._get("/api/tags").get("models", []):
            d = m.get("details") or {}
            out.append(InstalledModel(
                name=str(m.get("name", "")), size_gb=round(int(m.get("size", 0)) / 1e9, 2),
                parameter_size=str(d.get("parameter_size", "")),
                quantization=str(d.get("quantization_level", "")),
                family=str(d.get("family", "")), modified=str(m.get("modified_at", ""))[:10],
            ))
        return sorted(out, key=lambda m: m.name)

    def loaded(self) -> dict[str, float]:
        """Models currently in memory -> GB of VRAM they occupy."""
        return {str(m.get("name")): round(int(m.get("size_vram", 0)) / 1e9, 2)
                for m in self._get("/api/ps").get("models", [])}

    def capabilities(self, name: str) -> list[str]:
        try:
            r = self._client.post(self.base + "/api/show", json={"model": validate_name(name)})
            r.raise_for_status()
            return list(r.json().get("capabilities") or [])
        except (httpx.HTTPError, ValueError):
            return []

    def pull(self, name: str, progress: Callable[[str, int, int], None],
             cancel: CancelToken) -> None:
        name = validate_name(name)
        checked_total = 0
        try:
            with self._client.stream("POST", self.base + "/api/pull",
                                     json={"model": name, "stream": True},
                                     timeout=httpx.Timeout(60.0, connect=5.0, read=600.0)) as r:
                if r.status_code != 200:
                    r.read()
                    raise ModelError(f"Ollama couldn't pull {name}: {r.text[:200]}")
                for line in r.iter_lines():
                    if cancel.cancelled:
                        raise Cancelled(cancel.reason)
                    if not line.strip():
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "error" in ev:
                        raise ModelError(f"Download failed: {str(ev['error'])[:200]}")
                    total, done = int(ev.get("total") or 0), int(ev.get("completed") or 0)
                    if total > checked_total:
                        checked_total = total
                        if total - done > self._free() - DISK_MARGIN_BYTES:
                            raise ModelError(
                                f"Not enough disk space: {name} needs about "
                                f"{total / 1e9:.1f} GB and less than that (+2 GB margin) is free."
                            )
                    progress(str(ev.get("status", "")), done, total)
        except httpx.HTTPError as exc:
            raise ModelError(f"Download interrupted ({type(exc).__name__}).") from exc

    def delete(self, name: str) -> None:
        name = validate_name(name)
        try:
            r = self._client.request("DELETE", self.base + "/api/delete", json={"model": name})
        except httpx.HTTPError as exc:
            raise ModelError(f"Ollama isn't reachable at {self.base}.") from exc
        if r.status_code == 404:
            raise ModelError(f"{name} isn't installed.")
        if r.status_code != 200:
            raise ModelError(f"Ollama couldn't delete {name} (HTTP {r.status_code}).")

    def close(self) -> None:
        self._client.close()

"""Append-only, local action log (JSON lines) so every tool action is auditable."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chiku.tools.base import ToolResult


def _shorten(value: Any, limit: int = 200) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {str(k)[:60]: _shorten(v, limit) for k, v in list(value.items())[:20]}
    if isinstance(value, list | tuple):
        return [_shorten(v, limit) for v in value[:20]]
    return value


class AuditLog:
    def __init__(self, path: Path, max_bytes: int = 1_000_000):
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def record(
        self, tool: str, args: Any, result: ToolResult, duration_ms: int, approval: str
    ) -> None:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": tool,
            "args": _shorten(args),
            "approval": approval,
            "ok": result.ok,
            "error_code": result.error_code,
            "message": _shorten(result.message),
            "duration_ms": duration_ms,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    self.path.replace(self.path.with_suffix(".1.jsonl"))
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass  # auditing must never break the assistant

    def clear(self) -> None:
        """Delete the action history (current and rotated file). User-initiated only."""
        with self._lock:
            for p in (self.path, self.path.with_suffix(".1.jsonl")):
                p.unlink(missing_ok=True)

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out

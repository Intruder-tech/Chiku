"""Append-only, local action log (JSON lines) so every tool action is auditable."""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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


_FILE_TOOLS = frozenset({"find_files", "list_folder", "open_file", "read_text_file",
                         "create_folder", "move_or_rename", "move_to_recycle_bin"})


@dataclass
class UsageStats:
    actions: int = 0
    apps_opened: int = 0
    searches: int = 0
    pages_read: int = 0
    apps_installed: int = 0
    apps_updated: int = 0
    file_actions: int = 0
    memories_saved: int = 0
    last_7_days: int = 0
    since: str = ""  # date of the oldest recorded action
    top_apps: list[tuple[str, int]] = field(default_factory=list)


def usage_stats(entries: list[dict[str, Any]], now: datetime | None = None) -> UsageStats:
    """Counts of successful actions from the local action log (nothing else is tracked)."""
    now = now or datetime.now(UTC)
    s = UsageStats()
    apps: Counter[str] = Counter()
    for e in entries:
        if not e.get("ok"):
            continue
        s.actions += 1
        tool = e.get("tool", "")
        raw = e.get("args")
        args: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if tool == "open_app":
            s.apps_opened += 1
            apps[str(args.get("app_name", "")).strip().title()] += 1
        elif tool == "web_search":
            s.searches += 1
        elif tool == "read_web_page":
            s.pages_read += 1
        elif tool == "install_package":
            s.apps_installed += 1
        elif tool == "update_package":
            s.apps_updated += 1
        elif tool in _FILE_TOOLS:
            s.file_actions += 1
        elif tool == "remember":
            s.memories_saved += 1
        try:
            ts = datetime.fromisoformat(str(e.get("ts")))
        except ValueError:
            continue
        if now - ts <= timedelta(days=7):
            s.last_7_days += 1
        if not s.since or ts.date().isoformat() < s.since:
            s.since = ts.date().isoformat()
    s.top_apps = [(name, n) for name, n in apps.most_common(5) if name]
    return s


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

    def all(self) -> list[dict[str, Any]]:
        """Every recorded action, oldest first (the rotated file, then the current one)."""
        out: list[dict[str, Any]] = []
        for p in (self.path.with_suffix(".1.jsonl"), self.path):
            if not p.exists():
                continue
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        return out

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

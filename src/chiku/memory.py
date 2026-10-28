"""User-controlled long-term memory (local SQLite, never synced or sent anywhere by default).

Only things the user asked to remember, or approved, are stored. Obvious secrets are refused.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Category = Literal["preference", "fact", "person", "other"]
CATEGORIES: tuple[str, ...] = ("preference", "fact", "person", "other")
MAX_TEXT = 300


class MemoryRejected(ValueError):
    """Message is safe to show and speak."""


@dataclass
class MemoryItem:
    id: int
    text: str
    category: str
    created: str
    updated: str
    source: str  # "user asked" | "approved suggestion" | "added in settings"


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
        alt = not alt
    return total % 10 == 0


_SECRET_WORDS = re.compile(
    r"\b(password|passcode|passwd|pin\s*(?:code|number)?|otp|one[- ]time code|cvv|cvc|"
    r"secret\s*key|api\s*key|private\s*key|seed\s*phrase|recovery\s*phrase|security\s*answer)\b"
    r"\s*(?:is|:|=|was)?\s*\S+",
    re.I,
)
_TOKENISH = re.compile(r"\b(?:sk|pk|ghp|gho|xox[bap]|AKIA|AIza)[-_A-Za-z0-9]{12,}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_AADHAAR = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")


def sensitive_reason(text: str) -> str | None:
    """Why `text` looks like something that shouldn't be stored, or None."""
    if _SECRET_WORDS.search(text):
        return "it looks like a password, PIN or secret code"
    if _TOKENISH.search(text):
        return "it looks like an API key or access token"
    for m in re.finditer(r"(?:\d[ -]?){13,19}", text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return "it looks like a payment card number"
    if _PAN.search(text) or _SSN.search(text) or _AADHAAR.search(text):
        return "it looks like a government ID number"
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # re-entrant: add() returns via get() on duplicates
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "text TEXT NOT NULL, category TEXT NOT NULL, created TEXT NOT NULL, "
            "updated TEXT NOT NULL, source TEXT NOT NULL)"
        )
        self._db.commit()

    @staticmethod
    def _clean(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip().strip("\"'")
        if not text:
            raise MemoryRejected("There's nothing to remember.")
        if len(text) > MAX_TEXT:
            raise MemoryRejected(f"That's too long to remember (max {MAX_TEXT} characters).")
        reason = sensitive_reason(text)
        if reason:
            raise MemoryRejected(f"I won't store that because {reason}. Sensitive details "
                                 "are safer in a password manager.")
        return text

    def add(self, text: str, category: str = "other", source: str = "user asked") -> MemoryItem:
        text = self._clean(text)
        category = category if category in CATEGORIES else "other"
        with self._lock:
            dup = self._db.execute(
                "SELECT id FROM memories WHERE lower(text) = lower(?)", (text,)
            ).fetchone()
            if dup:
                return self.get(int(dup[0]))  # type: ignore[return-value]
            now = _now()
            cur = self._db.execute(
                "INSERT INTO memories (text, category, created, updated, source) "
                "VALUES (?, ?, ?, ?, ?)", (text, category, now, now, source))
            self._db.commit()
            new_id = int(cur.lastrowid or 0)
        item = self.get(new_id)
        assert item is not None
        return item

    def update(self, item_id: int, text: str, category: str | None = None) -> MemoryItem:
        text = self._clean(text)
        with self._lock:
            cur = self._db.execute(
                "UPDATE memories SET text = ?, category = COALESCE(?, category), updated = ? "
                "WHERE id = ?", (text, category if category in CATEGORIES else None, _now(),
                                 item_id))
            self._db.commit()
        if cur.rowcount == 0:
            raise MemoryRejected("That memory doesn't exist any more.")
        item = self.get(item_id)
        assert item is not None
        return item

    def delete(self, item_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM memories WHERE id = ?", (item_id,))
            self._db.commit()
        return cur.rowcount > 0

    def clear(self) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM memories")
            self._db.commit()
            # Reclaim the pages so deleted text doesn't linger in the file.
            self._db.execute("VACUUM")
        return cur.rowcount

    def get(self, item_id: int) -> MemoryItem | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, text, category, created, updated, source FROM memories WHERE id = ?",
                (item_id,)).fetchone()
        return MemoryItem(*row) if row else None

    def all(self) -> list[MemoryItem]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text, category, created, updated, source FROM memories "
                "ORDER BY updated DESC").fetchall()
        return [MemoryItem(*r) for r in rows]

    def search(self, query: str, limit: int = 10) -> list[MemoryItem]:
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
        items = self.all()
        if not words:
            return items[:limit]
        scored = [(sum(w in it.text.lower() for w in words), it) for it in items]
        return [it for s, it in sorted(scored, key=lambda p: -p[0]) if s > 0][:limit]

    def export_json(self) -> str:
        return json.dumps([asdict(i) for i in self.all()], indent=2, ensure_ascii=False)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def prompt_section(items: list[MemoryItem], limit: int) -> str:
    if not items or limit <= 0:
        return ""
    lines = [f"- [{it.id}] {it.text}" for it in items[:limit]]
    more = len(items) - limit
    tail = [f"- …and {more} more; use search_memory to look them up."] if more > 0 else []
    return "\n".join(["What the user has asked you to remember (use naturally, don't recite):",
                      *lines, *tail])

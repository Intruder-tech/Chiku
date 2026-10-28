"""Conversations: several chats you can switch between.

Kept in memory only, unless the user turns on "Keep conversation history"; then they are saved
to `conversations.json` in Chiku's data folder (never synced or sent anywhere). Turning the
option off deletes that file.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from chiku.llm.base import ChatMessage

log = logging.getLogger(__name__)

_TITLE_CHARS = 48


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Entry:
    role: str  # "user" | "assistant"
    text: str
    source: str = ""  # where an assistant reply was generated
    sources: list[dict[str, str]] = field(default_factory=list)  # web pages it used
    time: str = ""  # "HH:MM", shown on the bubble


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    title: str = "New chat"
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    entries: list[Entry] = field(default_factory=list)

    def history(self) -> list[ChatMessage]:
        """What the model gets back when the chat is reopened: the spoken text only (tool
        calls and results aren't kept)."""
        return [ChatMessage("user" if e.role == "user" else "assistant", e.text)
                for e in self.entries if e.text]


class ConversationStore:
    def __init__(self, path: Path, keep: Callable[[], bool], max_saved: Callable[[], int]):
        self.path = path
        self._keep = keep
        self._max = max_saved
        self.items: list[Conversation] = []  # most recently used first
        self.current: Conversation | None = None
        self.load()
        self.new()

    @property
    def active(self) -> Conversation:
        assert self.current is not None
        return self.current

    # -- queries ----------------------------------------------------------------------------
    def get(self, conv_id: str) -> Conversation | None:
        return next((c for c in self.items if c.id == conv_id), None)

    def listed(self) -> list[Conversation]:
        """Conversations worth showing: those with something in them, plus the current one."""
        return [c for c in self.items if c.entries or c is self.current]

    # -- changes ----------------------------------------------------------------------------
    def new(self) -> Conversation:
        if self.current is not None and not self.current.entries:
            return self.current  # already on an empty chat
        self.items = [c for c in self.items if c.entries]  # drop abandoned empty chats
        conv = Conversation()
        self.items.insert(0, conv)
        self.current = conv
        return conv

    def switch(self, conv_id: str) -> Conversation:
        conv = self.get(conv_id)
        if conv is None:
            raise KeyError(conv_id)
        if self.current is not None and not self.current.entries and self.current is not conv:
            self.items.remove(self.current)
        self.current = conv
        return conv

    def add(self, role: str, text: str, source: str = "",
            sources: list[dict[str, str]] | None = None) -> None:
        conv = self.active
        conv.entries.append(Entry(role, text, source, list(sources or []),
                                  datetime.now().strftime("%H:%M")))
        if role == "user" and conv.title == "New chat":
            title = " ".join(text.split())
            conv.title = title if len(title) <= _TITLE_CHARS \
                else title[:_TITLE_CHARS - 1].rstrip() + "…"
        conv.updated = _now()
        self.items.remove(conv)
        self.items.insert(0, conv)
        self.save()

    def delete(self, conv_id: str) -> None:
        self.items = [c for c in self.items if c.id != conv_id]
        if self.current is not None and self.current.id == conv_id:
            self.current = None
            self.new()
        self.save()

    def clear_all(self) -> None:
        self.items = []
        self.current = None
        self.new()
        self.forget_saved()

    # -- disk -------------------------------------------------------------------------------
    def load(self) -> None:
        if not self._keep() or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = [_conversation(c) for c in data.get("conversations", [])]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            log.warning("conversation history unreadable (%s); starting fresh",
                        type(exc).__name__)
            self.items = []

    def save(self) -> None:
        if not self._keep():
            return
        keep = [c for c in self.items if c.entries][: self._max()]
        payload = json.dumps({"version": 1, "conversations": [asdict(c) for c in keep]},
                             ensure_ascii=False, indent=1)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".conversations-", suffix=".json",
                                       dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("couldn't save conversation history: %s", exc)

    def forget_saved(self) -> None:
        """Delete the saved history file (chats in memory stay until Chiku closes)."""
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)

    def export(self) -> list[dict[str, Any]]:
        return [asdict(c) for c in self.items if c.entries]


def _conversation(d: dict[str, Any]) -> Conversation:
    entries = [Entry(str(e["role"]), str(e["text"]), str(e.get("source", "")),
                     list(e.get("sources", [])), str(e.get("time", "")))
               for e in d.get("entries", [])]
    return Conversation(str(d["id"]), str(d.get("title", "Chat")), str(d.get("created", "")),
                        str(d.get("updated", "")), entries)

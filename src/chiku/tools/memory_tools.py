"""Memory tools. Saving is automatic only when the user explicitly asked for it."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chiku.config import MemorySettings
from chiku.memory import MemoryRejected, MemoryStore
from chiku.tools.base import Permission, Tool, ToolContext, ToolResult

_ASKED_TO_REMEMBER = re.compile(
    r"\b(remember|don'?t forget|keep in mind|note (?:that|down)|make a note|save (?:that|this))\b",
    re.I,
)
_ASKED_TO_FORGET = re.compile(r"\b(forget|delete|remove|erase|drop)\b", re.I)

_STOP = {"the", "a", "an", "that", "user", "users", "i", "my", "their", "they", "is", "am",
          "are", "to", "of", "in", "and", "s"}


def _words(text: str) -> set[str]:
    words = {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words}


_SEVERAL_FACTS = re.compile(r"\b(?:and|but|also|plus)\b|[;.]\s", re.I)


def _close_match(query: str, memory_text: str) -> bool:
    """True if deleting this memory removes only what the query describes.

    A single-fact entry that contains every word of the request is a match. An entry that
    bundles several facts ("... and ...") only matches if it is nearly all request words.
    """
    q, m = _words(query), _words(memory_text)
    if not m or not q:
        return False
    if q <= m and not _SEVERAL_FACTS.search(memory_text):
        return True
    return len(m & q) / len(m) >= 0.8


UserText = Callable[[], str]
Settings = Callable[[], MemorySettings]


class RememberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=2, max_length=300,
                      description="The fact or preference, written as a short third-person "
                                  "note, e.g. 'Prefers metric units'.")
    category: Literal["preference", "fact", "person", "other"] = "other"


class RememberTool(Tool):
    name = "remember"
    description = ("Save a lasting fact or preference about the user to long-term memory. Use "
                   "when the user asks you to remember something, or (with their approval) for "
                   "clearly useful long-term preferences. Never for secrets or passwords.")
    Input = RememberInput
    timeout_s = 5.0

    def __init__(self, store: MemoryStore, settings: Settings, user_text: UserText):
        self.store = store
        self.settings = settings
        self.user_text = user_text

    def permission_for(self, args: RememberInput) -> Permission:
        s = self.settings()
        if not s.enabled:
            return Permission.DENY
        if _ASKED_TO_REMEMBER.search(self.user_text()) or not s.ask_before_model_saves:
            return Permission.AUTO
        return Permission.CONFIRM

    def describe(self, args: RememberInput) -> str:
        return f"Remember: “{args.text}”"

    def run(self, args: RememberInput, ctx: ToolContext) -> ToolResult:
        source = "user asked" if _ASKED_TO_REMEMBER.search(self.user_text()) \
            else "approved suggestion"
        saved = []
        for part in [p for p in args.text.split("\n") if p.strip()]:
            try:
                saved.append(self.store.add(part, args.category, source))
            except MemoryRejected as exc:
                return ToolResult.failure("rejected", str(exc),
                                          saved=[i.text for i in saved])
        texts = "; ".join(f"“{i.text}”" for i in saved)
        return ToolResult.success(f"Saved to memory: {texts}.", ids=[i.id for i in saved])


class ForgetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: int | None = Field(None, description="The [id] shown in your memory list.")
    query: str | None = Field(None, max_length=200,
                              description="Words from the memory, if you don't know the id.")

    @model_validator(mode="after")
    def _one(self) -> ForgetInput:
        if self.memory_id is None and not (self.query or "").strip():
            raise ValueError("give memory_id or query")
        return self


class ForgetTool(Tool):
    name = "forget_memory"
    description = "Delete one saved memory, by its [id] or by words it contains."
    Input = ForgetInput
    timeout_s = 5.0

    def __init__(self, store: MemoryStore, settings: Settings, user_text: UserText):
        self.store = store
        self.settings = settings
        self.user_text = user_text

    def _target(self, args: ForgetInput):  # noqa: ANN202
        if args.memory_id is not None:
            return self.store.get(args.memory_id)
        hits = self.store.search(args.query or "", limit=1)
        return hits[0] if hits else None

    def permission_for(self, args: ForgetInput) -> Permission:
        if not self.settings().enabled:
            return Permission.DENY
        if not _ASKED_TO_FORGET.search(self.user_text()):
            return Permission.CONFIRM
        item = self._target(args)
        if item is not None and args.memory_id is None and not _close_match(args.query or "",
                                                                             item.text):
            # The best match says more than what the user asked to forget; deleting it would
            # lose other facts, so show the whole entry and ask.
            return Permission.CONFIRM
        return Permission.AUTO

    def describe(self, args: ForgetInput) -> str:
        item = self._target(args)
        return f"Forget: “{item.text}”" if item else "Forget a memory"

    def run(self, args: ForgetInput, ctx: ToolContext) -> ToolResult:
        item = self._target(args)
        if item is None:
            return ToolResult.failure("not_found", "I don't have a memory like that.")
        self.store.delete(item.id)
        return ToolResult.success(f"Forgot: “{item.text}”.", id=item.id)


class SearchMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field("", max_length=200)


class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "Look up saved memories about the user (all of them if query is empty)."
    Input = SearchMemoryInput
    timeout_s = 5.0

    def __init__(self, store: MemoryStore, settings: Settings):
        self.store = store
        self.settings = settings

    def permission_for(self, args: SearchMemoryInput) -> Permission:
        return Permission.AUTO if self.settings().enabled else Permission.DENY

    def run(self, args: SearchMemoryInput, ctx: ToolContext) -> ToolResult:
        items = self.store.search(args.query, limit=20)
        return ToolResult.success(f"{len(items)} memory item(s).",
                                  memories=[{"id": i.id, "text": i.text,
                                             "category": i.category} for i in items])


def memory_tools(store: MemoryStore, settings: Settings, user_text: UserText) -> list[Tool]:
    return [RememberTool(store, settings, user_text), ForgetTool(store, settings, user_text),
            SearchMemoryTool(store, settings)]

"""Tool contract: every action Chiku can take on the computer implements this."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel

from chiku.cancel import CancelToken


class Permission(StrEnum):
    AUTO = "auto"  # runs without asking (low impact, e.g. open an app)
    CONFIRM = "confirm"  # user must approve each time
    DENY = "deny"  # never runs


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    @classmethod
    def success(cls, message: str, **data: Any) -> ToolResult:
        return cls(True, message, data)

    @classmethod
    def failure(cls, code: str, message: str, **data: Any) -> ToolResult:
        return cls(False, message, data, code)

    def for_model(self) -> str:
        """Serialized result handed back to the LLM (as untrusted data)."""
        payload: dict[str, Any] = {"ok": self.ok, "message": self.message}
        if self.data:
            payload["data"] = self.data
        if self.error_code:
            payload["error"] = self.error_code
        return json.dumps(payload, ensure_ascii=False, default=str)


@dataclass
class ToolContext:
    cancel: CancelToken


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[BaseModel]]
    permission: ClassVar[Permission] = Permission.AUTO
    timeout_s: ClassVar[float] = 10.0

    def permission_for(self, args: Any) -> Permission:
        """Permission for these specific arguments (may be stricter than the default)."""
        return self.permission

    def describe(self, args: Any) -> str:
        """Human-readable summary shown in confirmation prompts and the action log."""
        return f"{self.name}: {args.model_dump()}"

    @abstractmethod
    def run(self, args: Any, ctx: ToolContext) -> ToolResult: ...

    def schema(self) -> dict[str, Any]:
        params = self.Input.model_json_schema()
        params.pop("title", None)
        for prop in params.get("properties", {}).values():
            prop.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }

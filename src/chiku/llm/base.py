"""Provider-independent language model interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from chiku.cancel import CancelToken

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None


@dataclass
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # For role == "tool": which tool produced this result, and which call it answers.
    tool_name: str | None = None
    tool_call_id: str | None = None


@dataclass
class StreamChunk:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False


@dataclass
class ProviderStatus:
    available: bool
    message: str
    models: list[str] = field(default_factory=list)


class LLMError(Exception):
    """Generic provider failure; message is safe to show the user."""


class LLMUnavailable(LLMError):
    """Provider or model is not reachable / not installed."""


class LLMProvider(ABC):
    #: True only if requests never leave this machine.
    is_local: bool
    #: Short, honest label shown next to every reply, e.g. "local · qwen3:4b (Ollama)".
    label: str

    @abstractmethod
    def status(self) -> ProviderStatus: ...

    @abstractmethod
    def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        cancel: CancelToken,
    ) -> Iterator[StreamChunk]: ...

    def warm_up(self) -> None:  # noqa: B027 - optional hook
        """Load the model ahead of the first turn, if the provider supports it."""

    def close(self) -> None:  # noqa: B027 - optional hook
        pass

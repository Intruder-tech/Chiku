"""Ollama adapter (local inference over http://127.0.0.1:11434)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from chiku.cancel import Cancelled, CancelToken
from chiku.config import LLMSettings, is_loopback_url
from chiku.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMUnavailable,
    ProviderStatus,
    StreamChunk,
    ToolCall,
)

log = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    def __init__(self, settings: LLMSettings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.base = settings.ollama_url
        self.is_local = is_loopback_url(self.base)
        if not self.is_local and not settings.allow_remote_ollama:
            raise LLMError(
                f"Ollama URL {self.base} is not on this computer. Enable 'allow remote Ollama' "
                "in settings if you really want to send conversations there."
            )
        where = "local" if self.is_local else f"REMOTE {httpx.URL(self.base).host}"
        self.label = f"{where} · {settings.model} (Ollama)"
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(settings.request_timeout_s, connect=3.0),
        )
        self._think_supported = True
        self._caps: set[str] | None = None
        self._always_thinks: bool | None = None

    def capabilities(self) -> set[str]:
        """Model capabilities reported by Ollama (e.g. tools, thinking); cached."""
        if self._caps is None:
            try:
                r = self._client.post(
                    f"{self.base}/api/show", json={"model": self.settings.model}, timeout=5.0
                )
                r.raise_for_status()
                self._caps = set(r.json().get("capabilities") or [])
            except (httpx.HTTPError, ValueError):
                return set()
        return self._caps

    @property
    def reasons_first(self) -> bool:
        """True if the model always reasons before answering (think can't be turned off)."""
        if self._always_thinks is None:
            self._always_thinks = self._probe_always_thinks()
        return self._always_thinks

    def _options(self) -> dict[str, Any]:
        # Must be identical on every request: a different num_ctx makes Ollama reload the model.
        return {"temperature": self.settings.temperature, "num_ctx": self.settings.context_tokens}

    def _probe_always_thinks(self) -> bool:
        """Some builds (e.g. Qwen3 'thinking-2507') ignore think=false and emit raw reasoning.

        One tiny request tells them apart; it also loads the model.
        """
        if "thinking" not in self.capabilities():
            return False
        try:
            r = self._client.post(
                f"{self.base}/api/chat",
                json={
                    "model": self.settings.model,
                    "messages": [{"role": "user", "content": "Reply with just the word OK."}],
                    "stream": False,
                    "think": False,
                    "keep_alive": self.settings.keep_alive,
                    "options": {**self._options(), "num_predict": 48},
                },
                timeout=120.0,
            )
            r.raise_for_status()
            content = (r.json().get("message") or {}).get("content") or ""
        except (httpx.HTTPError, ValueError):
            return False
        thinks = "</think>" in content or len(content.strip()) > 20
        log.info("model %s always reasons first: %s", self.settings.model, thinks)
        return thinks

    # -- status ---------------------------------------------------------------
    def status(self) -> ProviderStatus:
        try:
            r = self._client.get(f"{self.base}/api/tags", timeout=3.0)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
        except httpx.HTTPError as exc:
            return ProviderStatus(
                False,
                f"Ollama isn't reachable at {self.base} ({type(exc).__name__}). Is it running?",
            )
        wanted = self.settings.model
        have = any(n == wanted or n == f"{wanted}:latest" for n in names)
        if not have:
            return ProviderStatus(
                False, f"Model '{wanted}' isn't installed. Run: ollama pull {wanted}", names
            )
        caps = self.capabilities()
        if caps and "tools" not in caps:
            return ProviderStatus(True, f"{self.label} ready, but it can't use tools", names)
        if self.reasons_first:
            return ProviderStatus(
                True,
                f"{self.label} ready. It reasons before answering, so replies start slower; "
                "an instruct model is faster for voice.",
                names,
            )
        return ProviderStatus(True, f"{self.label} ready", names)

    def warm_up(self) -> None:
        try:
            self._client.post(
                f"{self.base}/api/generate",
                json={"model": self.settings.model, "keep_alive": self.settings.keep_alive,
                      "options": self._options()},
                timeout=120.0,
            )
        except httpx.HTTPError as exc:
            log.info("ollama warm-up failed: %s", type(exc).__name__)

    def close(self) -> None:
        self._client.close()

    # -- chat -----------------------------------------------------------------
    def _payload(self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None) -> dict:
        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [_to_wire(m) for m in messages],
            "stream": True,
            "keep_alive": self.settings.keep_alive,
            "options": self._options(),
        }
        if tools:
            body["tools"] = tools
        if self._think_supported and "thinking" in self.capabilities():
            # Hybrid models: think=false skips hidden reasoning (seconds of silence before
            # speech). Always-thinking models ignore that and would leak raw reasoning into the
            # reply, so for them ask Ollama to separate it; the separate field is never shown
            # or spoken.
            body["think"] = self.reasons_first
        return body

    def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        cancel: CancelToken,
    ) -> Iterator[StreamChunk]:
        attempts = 0
        while True:
            attempts += 1
            produced = False
            try:
                for chunk in self._stream_once(messages, tools, cancel):
                    produced = True
                    yield chunk
                return
            except _RetryWithoutThink:
                self._think_supported = False
                continue
            except httpx.ConnectError as exc:
                if attempts < 2 and not produced and not cancel.cancelled:
                    log.info("ollama connect failed, retrying once")
                    if cancel.wait(0.5):
                        raise Cancelled(cancel.reason) from exc
                    continue
                raise LLMUnavailable(
                    f"I can't reach Ollama at {self.base}. Is it running?"
                ) from exc
            except httpx.TimeoutException as exc:
                raise LLMError("The language model took too long to respond.") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Language model request failed ({type(exc).__name__}).") from exc

    def _stream_once(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        cancel: CancelToken,
    ) -> Iterator[StreamChunk]:
        cancel.raise_if_cancelled()
        with self._client.stream(
            "POST", f"{self.base}/api/chat", json=self._payload(messages, tools)
        ) as resp:
            if resp.status_code != 200:
                detail = _error_text(resp)
                if resp.status_code == 400 and "think" in detail.lower() and self._think_supported:
                    raise _RetryWithoutThink()
                if resp.status_code == 404:
                    raise LLMUnavailable(
                        f"Model '{self.settings.model}' isn't installed in Ollama. "
                        f"Run: ollama pull {self.settings.model}"
                    )
                if resp.status_code == 400 and "does not support tools" in detail.lower():
                    raise LLMError(f"Model '{self.settings.model}' doesn't support tool calling.")
                raise LLMError(f"Ollama error {resp.status_code}: {detail[:200]}")
            for line in resp.iter_lines():
                if cancel.cancelled:
                    # Leaving the context manager closes the connection; Ollama stops generating.
                    raise Cancelled(cancel.reason)
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("skipping malformed stream line")
                    continue
                if "error" in data:
                    raise LLMError(f"Ollama error: {str(data['error'])[:200]}")
                msg = data.get("message") or {}
                calls = [_parse_call(c) for c in msg.get("tool_calls") or []]
                chunk = StreamChunk(
                    text=msg.get("content") or "",
                    tool_calls=[c for c in calls if c is not None],
                    done=bool(data.get("done")),
                )
                if chunk.text or chunk.tool_calls or chunk.done:
                    yield chunk
                if chunk.done:
                    return


class _RetryWithoutThink(Exception):
    pass


def _error_text(resp: httpx.Response) -> str:
    try:
        resp.read()
        data = resp.json()
        return str(data.get("error", resp.text))
    except (ValueError, httpx.HTTPError):
        return resp.text if resp.is_stream_consumed else ""


def _parse_call(raw: dict[str, Any]) -> ToolCall | None:
    fn = raw.get("function") or {}
    name = fn.get("name")
    if not isinstance(name, str) or not name:
        return None
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    if not isinstance(args, dict):
        args = {}
    return ToolCall(name=name, arguments=args, id=raw.get("id"))


def _to_wire(m: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls
        ]
    if m.role == "tool" and m.tool_name:
        out["tool_name"] = m.tool_name
    return out

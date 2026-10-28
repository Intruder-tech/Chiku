"""OpenAI-compatible chat adapter (/v1/chat/completions with SSE streaming and tools).

Works with local servers (LM Studio, llama.cpp `llama-server`, vLLM, Jan) and, only when the
user explicitly allows it, with remote/cloud APIs. Replies from a remote endpoint are always
labelled CLOUD.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
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

OPENAI_KEY_NAME = "openai_compat_api_key"


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        settings: LLMSettings,
        key_getter: Callable[[], str | None] = lambda: None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self.base = settings.openai_base_url.rstrip("/")
        self.model = settings.openai_model.strip()
        self.is_local = is_loopback_url(self.base)
        url = httpx.URL(self.base)
        if not self.is_local and not settings.allow_cloud:
            raise LLMError(
                f"{url.host} is not on this computer. Turn on 'Allow a cloud provider' in "
                "Settings → AI model if you want to send conversations there."
            )
        where = f"local · {self.model or '?'} ({url.host}:{url.port})" if self.is_local \
            else f"CLOUD · {self.model or '?'} ({url.host})"
        self.label = where
        self._key = key_getter
        self._client = httpx.Client(
            transport=transport, timeout=httpx.Timeout(settings.request_timeout_s, connect=5.0)
        )

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "text/event-stream"}
        try:
            key = self._key()
        except Exception:
            key = None
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def status(self) -> ProviderStatus:
        try:
            r = self._client.get(f"{self.base}/models", headers=self._headers(), timeout=5.0)
        except httpx.HTTPError as exc:
            return ProviderStatus(False, f"The model server at {self.base} isn't reachable "
                                         f"({type(exc).__name__}).")
        if r.status_code in (401, 403):
            return ProviderStatus(False, "The model server rejected the API key.")
        if r.status_code != 200:
            return ProviderStatus(False, f"The model server returned HTTP {r.status_code}.")
        try:
            ids = [str(m.get("id")) for m in r.json().get("data", [])]
        except (ValueError, AttributeError):
            ids = []
        if not self.model:
            return ProviderStatus(False, "Choose a model for this server in Settings.", ids)
        if ids and self.model not in ids:
            return ProviderStatus(False, f"The server doesn't list model '{self.model}'.", ids)
        return ProviderStatus(True, f"{self.label} ready", ids)

    def close(self) -> None:
        self._client.close()

    def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        cancel: CancelToken,
    ) -> Iterator[StreamChunk]:
        cancel.raise_if_cancelled()
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "stream": True,
            "temperature": self.settings.temperature,
        }
        if tools:
            body["tools"] = tools
        partial: dict[int, dict[str, str]] = {}
        try:
            with self._client.stream("POST", f"{self.base}/chat/completions", json=body,
                                     headers=self._headers()) as resp:
                if resp.status_code != 200:
                    resp.read()
                    raise _http_error(resp.status_code, resp.text, self.model)
                for line in resp.iter_lines():
                    if cancel.cancelled:
                        raise Cancelled(cancel.reason)
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if "error" in obj:
                        raise LLMError(f"Model server error: {str(obj['error'])[:200]}")
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield StreamChunk(text=str(delta["content"]))
                        for tc in delta.get("tool_calls") or []:
                            slot = partial.setdefault(int(tc.get("index", 0)),
                                                      {"id": "", "name": "", "args": ""})
                            fn = tc.get("function") or {}
                            slot["id"] = tc.get("id") or slot["id"]
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
        except httpx.ConnectError as exc:
            raise LLMUnavailable(f"I can't reach the model server at {self.base}.") from exc
        except httpx.TimeoutException as exc:
            raise LLMError("The language model took too long to respond.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Language model request failed ({type(exc).__name__}).") from exc
        calls = []
        for i in sorted(partial):
            slot = partial[i]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"]) if slot["args"].strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["args"]}
            calls.append(ToolCall(slot["name"], args if isinstance(args, dict) else {},
                                  slot["id"] or None))
        yield StreamChunk(tool_calls=calls, done=True)


def _http_error(status: int, text: str, model: str) -> LLMError:
    if status in (401, 403):
        return LLMError("The model server rejected the API key.")
    if status == 404:
        return LLMUnavailable(f"The model server doesn't have model '{model}'.")
    if status == 429:
        return LLMError("The model server's rate limit was reached.")
    return LLMError(f"Model server error {status}: {text[:200]}")


def _to_wire(m: ChatMessage) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id or "call_0", "content": m.content}
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["content"] = m.content or None
        out["tool_calls"] = [
            {"id": c.id or f"call_{i}", "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for i, c in enumerate(m.tool_calls)
        ]
    return out

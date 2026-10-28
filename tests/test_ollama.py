import json

import httpx
import pytest

from chiku.cancel import Cancelled, CancelToken
from chiku.config import LLMSettings
from chiku.llm.base import ChatMessage, LLMError, LLMUnavailable, ToolCall
from chiku.llm.ollama import OllamaProvider


def ndjson(*objs: dict) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


class Server:
    """Minimal fake of the Ollama HTTP API."""

    def __init__(self, caps: list[str] | None = None, chat: bytes | None = None,
                 chat_status: int = 200, probe_reply: str = "OK",
                 models: tuple[str, ...] = ("qwen3:4b-instruct-2507-q4_K_M",)):
        self.caps = caps if caps is not None else ["completion", "tools"]
        self.chat = chat or ndjson({"message": {"content": "Hi"}, "done": False},
                                   {"message": {"content": " there."}, "done": True})
        self.chat_status = chat_status
        self.probe_reply = probe_reply
        self.models = models
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        self.requests.append((req.url.path, body))
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
        if req.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": self.caps})
        if req.url.path == "/api/chat" and body.get("stream") is False:
            return httpx.Response(200, json={"message": {"content": self.probe_reply}})
        if req.url.path == "/api/chat":
            if self.chat_status != 200:
                return httpx.Response(self.chat_status, json={"error": "model not found"})
            return httpx.Response(200, content=self.chat)
        return httpx.Response(404)

    def chat_bodies(self) -> list[dict]:
        return [b for p, b in self.requests if p == "/api/chat" and b.get("stream")]


def provider(server: Server, **kw) -> OllamaProvider:
    return OllamaProvider(LLMSettings(**kw), transport=httpx.MockTransport(server))


MSGS = [ChatMessage("system", "be nice"), ChatMessage("user", "hello")]


def test_streams_text_and_sends_stable_options() -> None:
    s = Server()
    p = provider(s)
    chunks = list(p.stream_chat(MSGS, None, CancelToken()))
    assert "".join(c.text for c in chunks) == "Hi there."
    body = s.chat_bodies()[0]
    assert body["options"]["num_ctx"] == 8192
    assert "think" not in body  # model without the thinking capability
    assert body["messages"][1] == {"role": "user", "content": "hello"}
    assert p.is_local and p.label.startswith("local")


def test_parses_tool_calls_with_dict_or_string_arguments() -> None:
    s = Server(chat=ndjson(
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "open_app", "arguments": {"app_name": "Chrome"}}},
            {"function": {"name": "get_system_info", "arguments": '{"topic": "battery"}'}},
            {"function": {"arguments": {}}},
        ]}, "done": True}))
    chunks = list(provider(s).stream_chat(MSGS, [{"type": "function"}], CancelToken()))
    calls = [c for ch in chunks for c in ch.tool_calls]
    assert [(c.name, c.arguments) for c in calls] == [
        ("open_app", {"app_name": "Chrome"}), ("get_system_info", {"topic": "battery"})]
    assert s.chat_bodies()[0]["tools"] == [{"type": "function"}]


def test_tool_messages_serialised_for_ollama() -> None:
    s = Server()
    msgs = [*MSGS,
            ChatMessage("assistant", "", tool_calls=[ToolCall("open_app", {"app_name": "x"})]),
            ChatMessage("tool", '{"ok": true}', tool_name="open_app")]
    list(provider(s).stream_chat(msgs, None, CancelToken()))
    wire = s.chat_bodies()[0]["messages"]
    assert wire[2]["tool_calls"][0]["function"]["name"] == "open_app"
    assert wire[3] == {"role": "tool", "content": '{"ok": true}', "tool_name": "open_app"}


def test_missing_model_is_unavailable() -> None:
    with pytest.raises(LLMUnavailable, match="ollama pull"):
        list(provider(Server(chat_status=404)).stream_chat(MSGS, None, CancelToken()))


def test_status_reports_missing_model() -> None:
    st = provider(Server(models=("llama3.2:3b",))).status()
    assert not st.available and "ollama pull" in st.message


def test_connection_refused_is_unavailable_after_retry() -> None:
    attempts = []

    def refuse(req: httpx.Request) -> httpx.Response:
        attempts.append(req.url.path)
        raise httpx.ConnectError("refused", request=req)

    p = OllamaProvider(LLMSettings(), transport=httpx.MockTransport(refuse))
    with pytest.raises(LLMUnavailable, match="Is it running"):
        list(p.stream_chat(MSGS, None, CancelToken()))
    assert attempts.count("/api/chat") == 2
    assert not p.status().available


def test_cancel_mid_stream() -> None:
    s = Server(chat=ndjson(*[{"message": {"content": f"w{i} "}, "done": False}
                             for i in range(50)]))
    tok = CancelToken()
    got = []
    with pytest.raises(Cancelled):
        for c in provider(s).stream_chat(MSGS, None, tok):
            got.append(c.text)
            if len(got) == 3:
                tok.cancel("barge-in")
    assert len(got) == 3


def test_server_error_message() -> None:
    s = Server(chat=ndjson({"error": "out of memory"}))
    with pytest.raises(LLMError, match="out of memory"):
        list(provider(s).stream_chat(MSGS, None, CancelToken()))


def test_remote_url_refused_unless_allowed() -> None:
    with pytest.raises(LLMError, match="not on this computer"):
        OllamaProvider(LLMSettings(ollama_url="http://192.168.1.20:11434"))
    p = OllamaProvider(LLMSettings(ollama_url="http://192.168.1.20:11434",
                                   allow_remote_ollama=True))
    assert not p.is_local and p.label.startswith("REMOTE")


def test_hybrid_thinking_model_gets_think_false() -> None:
    s = Server(caps=["completion", "tools", "thinking"], probe_reply="OK")
    list(provider(s).stream_chat(MSGS, None, CancelToken()))
    assert s.chat_bodies()[0]["think"] is False


def test_always_thinking_model_gets_reasoning_separated() -> None:
    s = Server(caps=["completion", "tools", "thinking"],
               probe_reply="We need to reply OK. Let me think...\n</think>\nOK",
               chat=ndjson({"message": {"thinking": "hmm", "content": ""}, "done": False},
                           {"message": {"content": "Answer."}, "done": True}))
    p = provider(s)
    text = "".join(c.text for c in p.stream_chat(MSGS, None, CancelToken()))
    assert s.chat_bodies()[0]["think"] is True
    assert text == "Answer."  # reasoning field is never surfaced
    assert "slower" in p.status().message

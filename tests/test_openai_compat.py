import json

import httpx
import pytest

from chiku.bootstrap import UnavailableProvider, make_llm
from chiku.cancel import Cancelled, CancelToken
from chiku.config import LLMSettings
from chiku.llm.base import ChatMessage, LLMError, LLMUnavailable, ToolCall
from chiku.llm.openai_compat import OpenAICompatProvider


def sse(*events: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + \
        b"data: [DONE]\n\n"


def settings(**kw) -> LLMSettings:
    base = {"provider": "openai_compat", "openai_model": "local-model"}
    base.update(kw)
    return LLMSettings(**base)


def provider(handler, key: str | None = None, **kw) -> OpenAICompatProvider:
    return OpenAICompatProvider(settings(**kw), lambda: key, transport=httpx.MockTransport(handler))


MSGS = [ChatMessage("system", "s"), ChatMessage("user", "hi")]


def test_streams_text_and_assembles_tool_calls() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo."}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "open_app", "arguments": '{"app'}}
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '_name": "Chrome"}'}}]}}]},
        ))

    p = provider(handler, key="sk-test")
    chunks = list(p.stream_chat(MSGS, [{"type": "function"}], CancelToken()))
    assert "".join(c.text for c in chunks) == "Hello."
    calls = chunks[-1].tool_calls
    assert [(c.name, c.arguments, c.id) for c in calls] == [("open_app", {"app_name": "Chrome"},
                                                             "c1")]
    assert seen["body"]["model"] == "local-model" and seen["body"]["stream"] is True
    assert seen["auth"] == "Bearer sk-test"
    assert p.is_local and p.label.startswith("local")


def test_wire_format_for_tool_round_trip() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    msgs = [*MSGS, ChatMessage("assistant", "", tool_calls=[ToolCall("f", {"a": 1}, "id9")]),
            ChatMessage("tool", '{"ok": true}', tool_name="f", tool_call_id="id9")]
    list(provider(handler).stream_chat(msgs, None, CancelToken()))
    wire = seen["body"]["messages"]
    assert wire[2]["tool_calls"][0] == {"id": "id9", "type": "function",
                                        "function": {"name": "f", "arguments": '{"a": 1}'}}
    assert wire[3] == {"role": "tool", "tool_call_id": "id9", "content": '{"ok": true}'}
    assert "tools" not in seen["body"]


@pytest.mark.parametrize(("status", "exc", "match"), [
    (401, LLMError, "API key"), (404, LLMUnavailable, "doesn't have model"),
    (429, LLMError, "rate limit"), (500, LLMError, "500"),
])
def test_http_errors(status: int, exc: type, match: str) -> None:
    with pytest.raises(exc, match=match):
        list(provider(lambda r: httpx.Response(status, text="x")).stream_chat(
            MSGS, None, CancelToken()))


def test_unreachable_server() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(LLMUnavailable, match="can't reach"):
        list(provider(boom).stream_chat(MSGS, None, CancelToken()))
    assert not provider(boom).status().available


def test_cancel() -> None:
    body = sse(*[{"choices": [{"delta": {"content": f"w{i} "}}]} for i in range(30)])
    tok = CancelToken()
    got = 0
    with pytest.raises(Cancelled):
        for _c in provider(lambda r: httpx.Response(200, content=body)).stream_chat(
                MSGS, None, tok):
            got += 1
            if got == 2:
                tok.cancel("barge-in")


def test_status_checks_model_list() -> None:
    models = httpx.Response(200, json={"data": [{"id": "local-model"}, {"id": "other"}]})
    assert provider(lambda r: models).status().available
    assert not provider(lambda r: models, openai_model="missing").status().available


def test_cloud_requires_explicit_opt_in() -> None:
    with pytest.raises(LLMError, match="not on this computer"):
        OpenAICompatProvider(settings(openai_base_url="https://api.example.com/v1"))
    p = OpenAICompatProvider(settings(openai_base_url="https://api.example.com/v1",
                                      allow_cloud=True))
    assert not p.is_local and p.label.startswith("CLOUD")
    refused = make_llm(settings(openai_base_url="https://api.example.com/v1"))
    assert isinstance(refused, UnavailableProvider) and not refused.status().available

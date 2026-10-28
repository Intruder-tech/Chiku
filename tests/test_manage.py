import json

import httpx
import pytest

from chiku.cancel import Cancelled, CancelToken
from chiku.llm.manage import ModelError, OllamaManager, validate_name


def ndjson(*objs: dict) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


def mgr(handler, free: int = 100 * 1024**3) -> OllamaManager:
    return OllamaManager("http://127.0.0.1:11434", transport=httpx.MockTransport(handler),
                         free_space=lambda: free)


def test_list_loaded_capabilities() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen3:4b", "size": 2_500_000_000, "modified_at": "2026-10-28T03:00:00",
                 "details": {"parameter_size": "4.0B", "quantization_level": "Q4_K_M",
                             "family": "qwen3"}}]})
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b",
                                                         "size_vram": 3_900_000_000}]})
        return httpx.Response(200, json={"capabilities": ["completion", "tools"]})

    m = mgr(handler)
    [model] = m.installed()
    assert (model.name, model.size_gb, model.quantization) == ("qwen3:4b", 2.5, "Q4_K_M")
    assert m.loaded() == {"qwen3:4b": 3.9}
    assert m.capabilities("qwen3:4b") == ["completion", "tools"]


def test_pull_reports_progress() -> None:
    body = ndjson({"status": "pulling manifest"},
                  {"status": "downloading", "total": 1000, "completed": 500},
                  {"status": "downloading", "total": 1000, "completed": 1000},
                  {"status": "success"})
    seen = []
    mgr(lambda r: httpx.Response(200, content=body)).pull(
        "llama3.2:3b", lambda s, d, t: seen.append((s, d, t)), CancelToken())
    assert seen[1] == ("downloading", 500, 1000) and seen[-1][0] == "success"


def test_pull_refuses_when_disk_too_small() -> None:
    body = ndjson({"status": "downloading", "total": 5 * 1024**3, "completed": 0})
    with pytest.raises(ModelError, match="disk space"):
        mgr(lambda r: httpx.Response(200, content=body), free=3 * 1024**3).pull(
            "big:70b", lambda *a: None, CancelToken())


def test_pull_errors_and_cancel() -> None:
    with pytest.raises(ModelError, match="file does not exist"):
        mgr(lambda r: httpx.Response(200, content=ndjson({"error": "file does not exist"}))
            ).pull("nope:1b", lambda *a: None, CancelToken())
    tok = CancelToken()
    tok.cancel("user")
    body = ndjson({"status": "downloading", "total": 10, "completed": 1})
    with pytest.raises(Cancelled):
        mgr(lambda r: httpx.Response(200, content=body)).pull("x:1b", lambda *a: None, tok)


def test_delete() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, json.loads(req.content)))
        return httpx.Response(200 if calls[-1][1]["model"] == "a:1b" else 404)

    m = mgr(handler)
    m.delete("a:1b")
    assert calls[0] == ("DELETE", {"model": "a:1b"})
    with pytest.raises(ModelError, match="isn't installed"):
        m.delete("b:1b")


@pytest.mark.parametrize("name", ["qwen3:4b", "llama3.2:3b", "hf.co/user/model:Q4_K_M",
                                  "gemma3"])
def test_valid_names(name: str) -> None:
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "../x", "a b", "x;rm", "-flag", "a:b:c"])
def test_invalid_names(name: str) -> None:
    with pytest.raises(ModelError):
        validate_name(name)


def test_unreachable() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=req)

    with pytest.raises(ModelError, match="reachable"):
        mgr(boom).installed()

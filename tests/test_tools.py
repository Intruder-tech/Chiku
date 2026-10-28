import json
import time
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from chiku.audit import AuditLog
from chiku.cancel import CancelToken
from chiku.tools.base import Permission, Tool, ToolContext, ToolResult
from chiku.tools.registry import ToolExecutor, ToolRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    level: str = "auto"


class EchoTool(Tool):
    name = "echo"
    description = "Echo text."
    Input = EchoInput
    timeout_s = 1.0
    ran: ClassVar[list[str]] = []

    def permission_for(self, args: EchoInput) -> Permission:
        return Permission(args.level)

    def run(self, args: EchoInput, ctx: ToolContext) -> ToolResult:
        if args.text == "boom":
            raise RuntimeError("kaboom")
        if args.text == "slow":
            ctx.cancel.wait(5)
            return ToolResult.success("finished late")
        EchoTool.ran.append(args.text)
        return ToolResult.success(args.text)


@pytest.fixture
def executor(tmp_path: Path) -> tuple[ToolExecutor, AuditLog, list[str]]:
    reg = ToolRegistry()
    reg.register(EchoTool())
    asked: list[str] = []
    audit = AuditLog(tmp_path / "a.jsonl")

    def confirm(name: str, desc: str) -> bool:
        asked.append(desc)
        return "yes" in desc

    EchoTool.ran.clear()
    return ToolExecutor(reg, confirm=confirm, audit=audit), audit, asked


def test_registry_rejects_bad_and_duplicate_names() -> None:
    reg = ToolRegistry()
    reg.register(EchoTool())
    with pytest.raises(ValueError):
        reg.register(EchoTool())

    class Bad(EchoTool):
        name = "Bad Name!"

    with pytest.raises(ValueError):
        reg.register(Bad())


def test_schema_is_function_shaped() -> None:
    s = EchoTool().schema()
    assert s["type"] == "function"
    assert s["function"]["name"] == "echo"
    assert s["function"]["parameters"]["properties"]["text"]["type"] == "string"
    assert "title" not in s["function"]["parameters"]


def test_runs_auto_tool_and_audits(executor) -> None:
    ex, audit, asked = executor
    res = ex.execute("echo", {"text": "hi"}, CancelToken())
    assert res.ok and res.message == "hi" and asked == []
    rows = audit.tail()
    assert rows[-1]["tool"] == "echo" and rows[-1]["ok"] is True
    assert rows[-1]["approval"] == "auto" and "ts" in rows[-1]


def test_unknown_tool(executor) -> None:
    ex, _, _ = executor
    res = ex.execute("rm_rf", {}, CancelToken())
    assert not res.ok and res.error_code == "unknown_tool"


@pytest.mark.parametrize("args", [{}, {"text": "x", "shell": "del *"}, "not a dict"])
def test_invalid_input_never_runs(executor, args) -> None:
    ex, _, _ = executor
    res = ex.execute("echo", args, CancelToken())
    assert not res.ok and res.error_code == "invalid_input"
    assert EchoTool.ran == []


def test_confirm_required_and_denied(executor) -> None:
    ex, audit, asked = executor
    res = ex.execute("echo", {"text": "no thanks", "level": "confirm"}, CancelToken())
    assert not res.ok and res.error_code == "denied_by_user"
    assert len(asked) == 1 and EchoTool.ran == []
    assert audit.tail()[-1]["approval"] == "denied"


def test_confirm_required_and_approved(executor) -> None:
    ex, audit, _ = executor
    res = ex.execute("echo", {"text": "yes please", "level": "confirm"}, CancelToken())
    assert res.ok
    assert audit.tail()[-1]["approval"] == "user"


def test_deny_level_never_runs(executor) -> None:
    ex, _, asked = executor
    res = ex.execute("echo", {"text": "x", "level": "deny"}, CancelToken())
    assert res.error_code == "not_permitted" and asked == [] and EchoTool.ran == []


def test_tool_exception_becomes_structured_failure(executor) -> None:
    ex, _, _ = executor
    res = ex.execute("echo", {"text": "boom"}, CancelToken())
    assert not res.ok and res.error_code == "tool_error"


def test_timeout(executor) -> None:
    ex, _, _ = executor
    t0 = time.monotonic()
    res = ex.execute("echo", {"text": "slow"}, CancelToken())
    assert res.error_code == "timeout"
    assert time.monotonic() - t0 < 2.5


def test_cancellation_stops_waiting(executor) -> None:
    ex, _, _ = executor
    tok = CancelToken()
    tok.cancel("user")
    res = ex.execute("echo", {"text": "slow"}, tok)
    assert res.error_code == "cancelled"


def test_result_for_model_is_json() -> None:
    r = ToolResult.failure("x", "msg", detail=1)
    assert json.loads(r.for_model()) == {"ok": False, "message": "msg", "data": {"detail": 1},
                                         "error": "x"}


def test_audit_truncates_long_values(tmp_path: Path) -> None:
    a = AuditLog(tmp_path / "a.jsonl")
    a.record("t", {"text": "x" * 1000}, ToolResult.success("ok"), 1, "auto")
    assert len(a.tail()[0]["args"]["text"]) < 300

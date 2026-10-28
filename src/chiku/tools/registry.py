"""Tool registry and the single, audited path through which tools execute."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from pydantic import ValidationError

from chiku.audit import AuditLog
from chiku.cancel import CancelToken
from chiku.tools.base import Permission, Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

#: (tool name, human description of the action) -> approved?
ConfirmFn = Callable[[str, str], bool]

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not _NAME_RE.match(tool.name):
            raise ValueError(f"invalid tool name {tool.name!r}")
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[n] for n in sorted(self._tools)]

    def schemas(self, exclude: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values() if t.name not in exclude]


def deny_all(_name: str, _desc: str) -> bool:
    return False


class ToolExecutor:
    """Validates, authorizes, time-limits and audits every tool call."""

    def __init__(
        self,
        registry: ToolRegistry,
        confirm: ConfirmFn = deny_all,
        audit: AuditLog | None = None,
        disabled: Callable[[], Collection[str]] = lambda: (),
    ):
        self.registry = registry
        self.confirm = confirm
        self.audit = audit
        self.disabled = disabled
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chiku-tool")

    def execute(self, name: str, raw_args: Any, cancel: CancelToken) -> ToolResult:
        t0 = time.perf_counter()
        approval = "auto"
        args_for_log: Any = raw_args
        tool = self.registry.get(name)
        if tool is None:
            result = ToolResult.failure("unknown_tool", f"There is no tool called '{name}'.")
            return self._finish(name, args_for_log, result, approval, t0)
        if name in self.disabled():
            result = ToolResult.failure("tool_disabled",
                                        f"The {name} tool is switched off in settings.")
            return self._finish(name, args_for_log, result, "denied", t0)
        if not isinstance(raw_args, dict):
            raw_args = {}
        try:
            args = tool.Input.model_validate(raw_args)
            args_for_log = args.model_dump()
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            result = ToolResult.failure("invalid_input", f"Invalid input for {name}: {problems}")
            return self._finish(name, args_for_log, result, approval, t0)

        level = tool.permission_for(args)
        if level is Permission.DENY:
            result = ToolResult.failure("not_permitted", "That action isn't permitted.")
            return self._finish(name, args_for_log, result, "denied", t0)
        if level is Permission.CONFIRM:
            try:
                approved = bool(self.confirm(name, tool.describe(args)))
            except Exception:
                log.exception("confirmation prompt failed")
                approved = False
            if not approved:
                result = ToolResult.failure(
                    "denied_by_user", "The user did not approve this action."
                )
                return self._finish(name, args_for_log, result, "denied", t0)
            approval = "user"

        if cancel.cancelled:
            result = ToolResult.failure("cancelled", "Cancelled before it started.")
            return self._finish(name, args_for_log, result, approval, t0)

        tool_cancel = CancelToken()
        future = self._pool.submit(tool.run, args, ToolContext(cancel=tool_cancel))
        deadline = time.monotonic() + tool.timeout_s
        while True:
            try:
                result = future.result(timeout=0.1)
                break
            except FutureTimeout:
                if cancel.cancelled:
                    tool_cancel.cancel("cancelled")
                    result = ToolResult.failure("cancelled", "Cancelled.")
                    break
                if time.monotonic() > deadline:
                    tool_cancel.cancel("timeout")
                    result = ToolResult.failure(
                        "timeout", f"{name} didn't finish within {tool.timeout_s:.0f} seconds."
                    )
                    break
            except Exception as exc:
                log.exception("tool %s crashed", name)
                result = ToolResult.failure("tool_error", f"{name} failed: {type(exc).__name__}")
                break
        if not isinstance(result, ToolResult):
            result = ToolResult.failure("tool_error", f"{name} returned an invalid result.")
        return self._finish(name, args_for_log, result, approval, t0)

    def _finish(
        self, name: str, args: Any, result: ToolResult, approval: str, t0: float
    ) -> ToolResult:
        ms = int((time.perf_counter() - t0) * 1000)
        log.info("tool %s ok=%s code=%s %dms", name, result.ok, result.error_code, ms)
        if self.audit is not None:
            self.audit.record(name, args, result, ms, approval)
        return result

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

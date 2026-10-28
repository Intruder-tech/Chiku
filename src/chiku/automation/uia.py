"""Read and press controls in other apps through Windows UI Automation, the accessibility API
screen readers use. It sees named controls (buttons, menus, tabs, links, fields); the values
of password fields are never read."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

# UIA control type ids that can be pressed (invoked, toggled, selected or expanded)
_PRESSABLE = {50000: "button", 50002: "checkbox", 50005: "link", 50007: "list item",
              50011: "menu item", 50013: "radio button", 50019: "tab", 50024: "tree item",
              50031: "split button"}
_INVOKE, _TOGGLE, _SELECT, _EXPAND = 10000, 10015, 10010, 10005
_DESCENDANTS = 4
_SCAN_LIMIT = 2500  # elements looked at per window; browsers and chat apps have thousands


@dataclass(frozen=True)
class Control:
    name: str
    kind: str  # "button", "menu item", "edit", …
    enabled: bool
    pressable: bool


class UIAutomationLike(Protocol):
    def controls(self, hwnd: int, limit: int = 120) -> list[Control]: ...

    def press(self, hwnd: int, name: str,
              kinds: tuple[str, ...] | None = None) -> Control | None: ...

    def focused(self) -> Control | None: ...


_KINDS = {50004: "edit", 50003: "combo box", 50030: "document", 50025: "custom", 50026: "group"}


def best_match(items: list[tuple[str, Any]], name: str) -> Any | None:
    """Exact name (ignoring case) first, then a name that starts with it, then contains it."""
    q = " ".join(name.casefold().split())
    if not q:
        return None
    for test in (lambda n: n == q, lambda n: n.startswith(q), lambda n: q in n):
        for n, item in items:
            if test(" ".join(n.casefold().split())):
                return item
    return None


class UIAutomation:
    """The real implementation (comtypes over UIAutomationCore.dll)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @contextmanager
    def _client(self) -> Iterator[Any]:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()  # tools run on worker threads
        try:
            with self._lock:
                comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uiac

            yield comtypes.client.CreateObject(uiac.CUIAutomation, interface=uiac.IUIAutomation)
        finally:
            comtypes.CoUninitialize()

    def _elements(self, uia: Any, hwnd: int) -> Iterator[Any]:
        root = uia.ElementFromHandle(hwnd)
        found = root.FindAll(_DESCENDANTS, uia.CreateTrueCondition())
        for i in range(min(found.Length, _SCAN_LIMIT)):
            yield found.GetElement(i)

    def controls(self, hwnd: int, limit: int = 120) -> list[Control]:
        out: list[Control] = []
        seen: set[tuple[str, str]] = set()
        with self._client() as uia:
            for el in self._elements(uia, hwnd):
                try:
                    name = " ".join((el.CurrentName or "").split())
                    if not name or el.CurrentIsOffscreen:
                        continue
                    ctype = int(el.CurrentControlType)
                    kind = _PRESSABLE.get(ctype) or (el.CurrentLocalizedControlType or "")
                    if (name, kind) in seen:
                        continue
                    seen.add((name, kind))
                    out.append(Control(name[:120], kind, bool(el.CurrentIsEnabled),
                                       ctype in _PRESSABLE))
                except Exception:  # noqa: S112 - elements vanish while we read them
                    continue
                if len(out) >= limit:
                    break
        return out

    def focused(self) -> Control | None:
        """The control that has keyboard focus right now (where typed keys will go)."""
        with self._client() as uia:
            try:
                el = uia.GetFocusedElement()
                ctype = int(el.CurrentControlType)
                kind = _PRESSABLE.get(ctype) or _KINDS.get(ctype) or (
                    el.CurrentLocalizedControlType or "")
                return Control(" ".join((el.CurrentName or "").split())[:120], kind,
                               bool(el.CurrentIsEnabled), ctype in _PRESSABLE)
            except Exception:  # nothing focused, or it vanished
                return None

    def press(self, hwnd: int, name: str,
              kinds: tuple[str, ...] | None = None) -> Control | None:
        """Press the best-matching named control; `kinds` limits it, e.g. to ("tab",)."""
        from comtypes.gen import UIAutomationClient as uiac

        with self._client() as uia:
            items: list[tuple[str, Any]] = []
            for el in self._elements(uia, hwnd):
                try:
                    ctype = int(el.CurrentControlType)
                    label = " ".join((el.CurrentName or "").split())
                    if ctype in _PRESSABLE and label and el.CurrentIsEnabled \
                            and not el.CurrentIsOffscreen \
                            and (kinds is None or _PRESSABLE[ctype] in kinds):
                        items.append((label, (el, ctype, label)))
                except Exception:  # noqa: S112 - element vanished
                    continue
            hit = best_match(items, name)
            if hit is None:
                return None
            el, ctype, label = hit
            for pattern_id, iface, method in (
                    (_INVOKE, uiac.IUIAutomationInvokePattern, "Invoke"),
                    (_TOGGLE, uiac.IUIAutomationTogglePattern, "Toggle"),
                    (_SELECT, uiac.IUIAutomationSelectionItemPattern, "Select"),
                    (_EXPAND, uiac.IUIAutomationExpandCollapsePattern, "Expand")):
                try:
                    pattern = el.GetCurrentPattern(pattern_id)
                except Exception:  # noqa: S112 - pattern not supported
                    continue
                if pattern:
                    getattr(pattern.QueryInterface(iface), method)()
                    return Control(label, _PRESSABLE[ctype], True, True)
            return None

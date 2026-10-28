"""Installed application discovery, launching and launch verification.

Discovery uses documented sources only: Start Menu shortcuts (.lnk) and the
Start apps list (Get-StartApps, which includes Store/UWP apps). Launching uses
ShellExecute on the shortcut, or the shell:AppsFolder\\<AppUserModelID> path.
The language model can only name an app; the executable always comes from this
catalog, never from model output.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field

from chiku.config import PermissionSettings
from chiku.tools.base import Permission, Tool, ToolContext, ToolResult
from chiku.tools.winutil import list_windows

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppEntry:
    name: str
    kind: Literal["lnk", "appx"]
    launch: str  # .lnk path or AppUserModelID
    target: str = ""  # resolved executable path when known

    @property
    def exe_name(self) -> str:
        t = self.target or (self.launch if self.kind == "appx" else "")
        base = Path(t.replace("\\", "/")).name.lower()
        return base if base.endswith(".exe") else ""


_JUNK = re.compile(
    r"\b(uninstall|uninstaller|readme|read me|help|documentation|manual|release notes|website|"
    r"license|support|faq|what'?s new|changelog|setup|installer|repair)\b",
    re.I,
)
_SCRIPT_TARGETS = (".bat", ".cmd", ".ps1", ".vbs", ".js", ".wsf", ".url", ".html", ".htm",
                   ".txt", ".pdf", ".chm", ".rtf")
_SENSITIVE = re.compile(
    r"(cmd\.exe|powershell|pwsh|\bwt\.exe|regedit|registry editor|\.msc\b|\bmmc\b|"
    r"command prompt|windows terminal|^terminal$|task scheduler|computer management|"
    r"group policy|services|windows tools|disk management|local security policy)",
    re.I,
)
ALIASES = {
    "chrome": "google chrome",
    "google": "google chrome",
    "edge": "microsoft edge",
    "firefox": "mozilla firefox",
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "code": "visual studio code",
    "explorer": "file explorer",
    "files": "file explorer",
    "my files": "file explorer",
    "calc": "calculator",
    "cmd": "command prompt",
    "store": "microsoft store",
    "teams": "microsoft teams",
    "outlook": "outlook",
    "paint": "paint",
    "whatsapp": "whatsapp",
    "task manager": "task manager",
    "word": "word",
    "excel": "excel",
    "powerpoint": "powerpoint",
}
_STOPWORDS = {"the", "app", "application", "program", "my", "a", "an"}


def normalise(name: str) -> str:
    name = name.lower().replace("(x86)", "").replace("&", " and ")
    name = re.sub(r"[^a-z0-9+#. ]", " ", name)
    words = [w for w in name.split() if w not in _STOPWORDS]
    return " ".join(words).strip(" .")


def _score(query: str, name: str) -> float:
    q, n = normalise(query), normalise(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    q_words, n_words = q.split(), n.split()
    if all(w in n_words for w in q_words):
        return max(0.6, 0.92 - 0.04 * (len(n_words) - len(q_words)))
    if n.startswith(q):
        return 0.85
    return 0.8 * difflib.SequenceMatcher(None, q, n).ratio()


_DISCOVERY_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$sh = New-Object -ComObject WScript.Shell
$roots = @([Environment]::GetFolderPath('CommonStartMenu'),
           [Environment]::GetFolderPath('StartMenu'))
$out = New-Object System.Collections.ArrayList
foreach ($r in $roots) {
  $lnks = Get-ChildItem -LiteralPath (Join-Path $r 'Programs') -Filter *.lnk -Recurse -File
  $lnks | ForEach-Object {
    $t = ''
    try { $t = $sh.CreateShortcut($_.FullName).TargetPath } catch {}
    [void]$out.Add([pscustomobject]@{ k = 'lnk'; n = $_.BaseName; l = $_.FullName; t = $t })
  }
}
Get-StartApps | ForEach-Object {
  [void]$out.Add([pscustomobject]@{ k = 'appx'; n = $_.Name; l = $_.AppID; t = '' })
}
$out | ConvertTo-Json -Compress
"""


def discover_apps(timeout_s: float = 30.0) -> list[AppEntry]:
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _DISCOVERY_PS],
        capture_output=True, timeout=timeout_s, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    raw = proc.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        raise RuntimeError("app discovery returned nothing")
    items = json.loads(raw)
    if isinstance(items, dict):
        items = [items]
    return parse_discovery(items)


def parse_discovery(items: list[dict]) -> list[AppEntry]:
    entries: dict[str, AppEntry] = {}
    for it in items:
        name = str(it.get("n") or "").strip()
        launch = str(it.get("l") or "").strip()
        target = str(it.get("t") or "").strip()
        kind = it.get("k")
        if not name or not launch or kind not in ("lnk", "appx") or _JUNK.search(name):
            continue
        if any(ch in launch for ch in '"\r\n\t\0'):
            continue
        if kind == "lnk" and target.lower().endswith(_SCRIPT_TARGETS):
            continue
        key = normalise(name)
        if key in entries:
            continue  # first source wins: shortcuts carry the resolved target path
        entries[key] = AppEntry(name=name, kind=kind, launch=launch, target=target)
    return list(entries.values())


class AppCatalog:
    def __init__(
        self,
        loader: Callable[[], list[AppEntry]] = discover_apps,
        ttl_s: float = 600.0,
    ):
        self._loader = loader
        self._ttl = ttl_s
        self._entries: list[AppEntry] = []
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def entries(self) -> list[AppEntry]:
        with self._lock:
            if not self._entries or time.monotonic() - self._loaded_at > self._ttl:
                try:
                    self._entries = self._loader()
                    self._loaded_at = time.monotonic()
                    log.info("app catalog: %d entries", len(self._entries))
                except Exception as exc:
                    log.warning("app discovery failed: %s", exc)
            return list(self._entries)

    def invalidate(self) -> None:
        """Forget the cached list (e.g. after an app was installed or uninstalled)."""
        with self._lock:
            self._loaded_at = 0.0
            self._entries = []

    def search(self, query: str, limit: int = 5) -> list[tuple[AppEntry, float]]:
        q = normalise(query)
        q = normalise(ALIASES.get(q, q))
        scored = [(e, _score(q, e.name)) for e in self.entries()]
        scored.sort(key=lambda p: (-p[1], len(p[0].name)))
        return [p for p in scored[:limit] if p[1] > 0.3]

    def best(self, query: str, min_score: float = 0.62) -> tuple[AppEntry, float] | None:
        hits = self.search(query, limit=1)
        if hits and hits[0][1] >= min_score:
            return hits[0]
        return None


def is_sensitive(entry: AppEntry) -> bool:
    return bool(_SENSITIVE.search(entry.name) or _SENSITIVE.search(entry.target)
                or _SENSITIVE.search(entry.launch))


# -- launching & verification ---------------------------------------------------

@dataclass
class Snapshot:
    pids: set[int]
    hwnds: set[int]


def take_snapshot() -> Snapshot:
    return Snapshot(set(psutil.pids()), {w.hwnd for w in list_windows()})


def launch(entry: AppEntry) -> None:
    if entry.kind == "lnk":
        os.startfile(entry.launch)  # noqa: S606 - ShellExecute on a Start Menu shortcut
    else:
        subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{entry.launch}"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def _title_matches(title: str, entry: AppEntry) -> bool:
    t = normalise(title)
    words = [w for w in normalise(entry.name).split() if len(w) > 2 and w not in
             {"microsoft", "windows", "google", "the"}]
    if not words:
        words = normalise(entry.name).split()
    return bool(words) and all(w in t for w in words[:2])


def verify_launch(entry: AppEntry, before: Snapshot, ctx: ToolContext,
                  timeout_s: float = 8.0) -> tuple[bool, str]:
    """Return (verified, how). Checks for a new matching process or window."""
    exe = entry.exe_name
    already_running = False
    if exe:
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info.get("name") or "").lower() == exe and p.info["pid"] in before.pids:
                already_running = True
                break
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if ctx.cancel.wait(0.3):
            return False, "cancelled"
        if exe:
            for p in psutil.process_iter(["pid", "name"]):
                if p.info["pid"] not in before.pids and (p.info.get("name") or "").lower() == exe:
                    return True, "process started"
        windows = list_windows()
        exe_pids: set[int] = set()
        if exe:
            exe_pids = {p.info["pid"] for p in psutil.process_iter(["pid", "name"])
                        if (p.info.get("name") or "").lower() == exe}
        for w in windows:
            if w.hwnd in before.hwnds:
                continue
            if w.pid in exe_pids or _title_matches(w.title, entry):
                return True, "window opened"
    if already_running:
        return True, "already running"
    return False, "no new process or window appeared"


# -- tools ----------------------------------------------------------------------

class OpenAppInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_name: str = Field(
        ..., min_length=1, max_length=80,
        description="Name of the installed application, e.g. 'Chrome', 'Notepad', 'Spotify'.",
    )


class OpenAppTool(Tool):
    name = "open_app"
    description = (
        "Open an installed Windows application by name. The launch is verified and the result "
        "says whether it actually opened."
    )
    Input = OpenAppInput
    timeout_s = 15.0

    def __init__(
        self,
        catalog: AppCatalog,
        permissions: Callable[[], PermissionSettings],
        launcher: Callable[[AppEntry], None] = launch,
        verifier: Callable[[AppEntry, Snapshot, ToolContext], tuple[bool, str]] = verify_launch,
        snapshot: Callable[[], Snapshot] = take_snapshot,
    ):
        self.catalog = catalog
        self.permissions = permissions
        self._launch = launcher
        self._verify = verifier
        self._snapshot = snapshot

    def permission_for(self, args: OpenAppInput) -> Permission:
        hit = self.catalog.best(args.app_name)
        if hit and self.permissions().confirm_sensitive_apps and is_sensitive(hit[0]):
            return Permission.CONFIRM
        return Permission.AUTO if self.permissions().auto_allow_open_apps else Permission.CONFIRM

    def describe(self, args: OpenAppInput) -> str:
        hit = self.catalog.best(args.app_name)
        return f"Open {hit[0].name}" if hit else f"Open '{args.app_name}'"

    def run(self, args: OpenAppInput, ctx: ToolContext) -> ToolResult:
        hit = self.catalog.best(args.app_name)
        if hit is None:
            suggestions = [e.name for e, _ in self.catalog.search(args.app_name, limit=3)]
            msg = f"I couldn't find an installed app called '{args.app_name}'."
            return ToolResult.failure("app_not_found", msg, suggestions=suggestions)
        entry, score = hit
        before = self._snapshot()
        try:
            self._launch(entry)
        except OSError as exc:
            return ToolResult.failure(
                "launch_failed", f"Windows couldn't start {entry.name} ({exc.strerror or exc}).",
                app=entry.name,
            )
        verified, how = self._verify(entry, before, ctx)
        if not verified:
            return ToolResult.failure(
                "not_verified", f"I tried to open {entry.name}, but couldn't confirm it started.",
                app=entry.name, detail=how,
            )
        if how == "already running":
            return ToolResult.success(f"{entry.name} was already running.", app=entry.name,
                                      verified=how)
        return ToolResult.success(f"{entry.name} is open.", app=entry.name, verified=how,
                                  match_score=round(score, 2))


class FindAppInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, max_length=80, description="App name to look for.")


class FindAppTool(Tool):
    name = "find_app"
    description = "Check whether an application is installed; returns the closest matching names."
    Input = FindAppInput
    timeout_s = 30.0

    def __init__(self, catalog: AppCatalog):
        self.catalog = catalog

    def run(self, args: FindAppInput, ctx: ToolContext) -> ToolResult:
        hits = self.catalog.search(args.query, limit=5)
        names = [e.name for e, s in hits if s >= 0.5]
        if not names:
            return ToolResult.success(f"No installed app matches '{args.query}'.", matches=[])
        return ToolResult.success(f"Found {len(names)} matching app(s).", matches=names)

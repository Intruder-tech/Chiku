"""App management through winget (Windows Package Manager).

Search and info are automatic; install, uninstall and update always need the user's
confirmation, and the dialog shows exactly which package (name, id, publisher, version, source)
will be changed. Only the official `winget` and `msstore` sources are used. Chiku never
downloads installers from arbitrary URLs. Machine-wide installers may trigger Windows' own
UAC prompt; Chiku never elevates by itself. Every change is verified with `winget list`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chiku.cancel import CancelToken
from chiku.tools.base import Permission, Tool, ToolContext, ToolResult

Source = Literal["winget", "msstore"]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}$")
COMMON = ["--accept-source-agreements", "--disable-interactivity"]
NO_UPGRADE = 0x8A15002B  # APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE
NOT_FOUND = 0x8A150014  # APPINSTALLER_CLI_ERROR_NO_APPLICATIONS_FOUND


class WingetError(RuntimeError):
    """Message is safe to show and speak."""


@dataclass
class Package:
    name: str
    id: str
    version: str = ""
    available: str = ""
    source: str = ""
    details: dict[str, str] = field(default_factory=dict)

    def label(self) -> str:
        pub = self.details.get("Publisher", "")
        ver = self.version or self.details.get("Version", "")
        bits = [self.name, f"({self.id})"]
        if ver:
            bits.append(f"version {ver}")
        if pub:
            bits.append(f"by {pub}")
        if self.source:
            bits.append(f"from {self.source}")
        return " ".join(bits)


def clean_lines(text: str) -> list[str]:
    # winget redraws progress spinners with carriage returns; keep only the final state
    return [ln.split("\r")[-1].rstrip() for ln in text.replace("\r\n", "\n").split("\n")]


def parse_table(text: str) -> list[dict[str, str]]:
    """Parse winget's fixed-width tables (search, list, upgrade)."""
    lines = clean_lines(text)
    for i in range(len(lines) - 1):
        head, sep = lines[i], lines[i + 1].strip()
        if "Name" in head and "Id" in head and sep and set(sep) == {"-"}:
            cols = [(m.start(), m.group()) for m in re.finditer(r"\S+", head)]
            rows = []
            for row in lines[i + 2:]:
                if not row.strip() or re.match(r"^\d+ (upgrades?|packages?) ", row.strip()):
                    break
                rec = {}
                for j, (start, name) in enumerate(cols):
                    end = cols[j + 1][0] if j + 1 < len(cols) else None
                    rec[name] = row[start:end].strip()
                if rec.get("Id"):
                    rows.append(rec)
            return rows
    return []


def parse_show(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in clean_lines(text):
        m = re.match(r"^([A-Z][A-Za-z ]{1,30}):\s*(.*)$", ln.strip())
        if m and m.group(2):
            out.setdefault(m.group(1).strip(), m.group(2).strip())
    found = re.search(r"Found (.+?) \[([^\]]+)\]", text)
    if found:
        out["Name"], out["Id"] = found.group(1).strip(), found.group(2).strip()
    return out


Runner = Callable[[list[str], float, CancelToken], tuple[int, str]]


def run_winget(args: list[str], timeout: float, cancel: CancelToken) -> tuple[int, str]:
    exe = shutil.which("winget")
    if not exe:
        raise WingetError("winget (App Installer) isn't available on this PC.")
    proc = subprocess.Popen(
        [exe, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + timeout
    while True:
        # Keep reading while waiting: a large listing (e.g. `winget list`) fills the pipe and
        # winget would block on it forever. Retrying communicate() loses no output.
        try:
            raw, _ = proc.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel.cancelled or time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise WingetError("Cancelled." if cancel.cancelled
                                  else "winget took too long.") from None
    raw = raw or b""
    text = raw.decode("utf-8", errors="replace")
    return proc.returncode & 0xFFFFFFFF, text


class WingetLike(Protocol):
    def search(self, query: str, cancel: CancelToken) -> list[Package]: ...


class Winget:
    def __init__(self, runner: Runner = run_winget):
        self._run = runner

    @staticmethod
    def _check_id(pkg_id: str) -> str:
        if not _ID.match(pkg_id or ""):
            raise WingetError(f"'{pkg_id}' isn't a valid package id.")
        return pkg_id

    def search(self, query: str, cancel: CancelToken, limit: int = 8) -> list[Package]:
        code, out = self._run(["search", "--query", query[:100], "--count", "25", *COMMON],
                              90, cancel)
        if code not in (0, NOT_FOUND):
            raise WingetError("winget search failed. Is the internet connected?")
        pkgs = [Package(r.get("Name", ""), r["Id"], r.get("Version", ""),
                        source=r.get("Source", "winget") or "winget")
                for r in parse_table(out)]
        pkgs = [p for p in pkgs if p.source in ("winget", "msstore") and _ID.match(p.id)]
        q = query.lower().strip()

        def rank(p: Package) -> tuple[int, int, int]:
            name, pid = p.name.lower(), p.id.lower()
            exact = 0 if name == q or pid == q or pid.endswith("." + q.replace(" ", "")) else 1
            return (exact, 0 if p.source == "winget" else 1, len(p.name))

        return sorted(pkgs, key=rank)[:limit]

    def show(self, pkg_id: str, source: str, cancel: CancelToken) -> dict[str, str]:
        code, out = self._run(["show", "--id", self._check_id(pkg_id), "-e", "--source",
                               source, *COMMON], 90, cancel)
        if code != 0:
            raise WingetError(f"winget couldn't find package {pkg_id}.")
        return parse_show(out)

    def installed(self, query: str, cancel: CancelToken, exact_id: bool = False) -> list[Package]:
        args = ["list", "--id", self._check_id(query), "-e"] if exact_id \
            else ["list", "--query", query[:100]]
        code, out = self._run([*args, *COMMON], 90, cancel)
        if code not in (0, NOT_FOUND):
            raise WingetError("winget list failed.")
        return [Package(r.get("Name", ""), r["Id"], r.get("Version", ""),
                        r.get("Available", ""), r.get("Source", ""))
                for r in parse_table(out)]

    def installed_all(self, cancel: CancelToken) -> list[Package]:
        """Every app winget can see on this PC (the Apps page). Read-only."""
        code, out = self._run(["list", *COMMON], 120, cancel)
        if code not in (0, NOT_FOUND):
            raise WingetError("winget list failed.")
        return [Package(r.get("Name", ""), r["Id"], r.get("Version", ""),
                        r.get("Available", ""), r.get("Source", ""))
                for r in parse_table(out)]

    def is_installed(self, pkg_id: str, cancel: CancelToken) -> bool:
        return any(p.id.lower() == pkg_id.lower()
                   for p in self.installed(pkg_id, cancel, exact_id=True))

    def upgrades(self, cancel: CancelToken) -> list[Package]:
        code, out = self._run(["upgrade", "--include-unknown", *COMMON], 120, cancel)
        if code not in (0, NOT_FOUND):
            raise WingetError("winget couldn't check for updates.")
        return [Package(r.get("Name", ""), r["Id"], r.get("Version", ""),
                        r.get("Available", ""), r.get("Source", ""))
                for r in parse_table(out) if r.get("Available")]

    def install(self, pkg_id: str, source: str, cancel: CancelToken) -> tuple[int, str]:
        base = ["install", "--id", self._check_id(pkg_id), "-e", "--source", source, "--silent",
                "--accept-package-agreements", *COMMON]
        code, out = self._run([*base, "--scope", "user"], 1800, cancel)
        if code != 0 and re.search(r"scope|No applicable installer", out, re.I):
            # No per-user installer: use the default (may show Windows' own UAC prompt).
            code, out = self._run(base, 1800, cancel)
        return code, out

    def uninstall(self, pkg_id: str, cancel: CancelToken) -> tuple[int, str]:
        return self._run(["uninstall", "--id", self._check_id(pkg_id), "-e", "--silent",
                          *COMMON], 1200, cancel)

    def upgrade(self, pkg_id: str, cancel: CancelToken) -> tuple[int, str]:
        return self._run(["upgrade", "--id", self._check_id(pkg_id), "-e", "--silent",
                          "--accept-package-agreements", *COMMON], 1800, cancel)


def _tail(out: str, n: int = 2) -> str:
    useful = [ln.strip() for ln in clean_lines(out)
              if ln.strip() and not set(ln.strip()) <= set("-\\|/█▒ ") and "%" not in ln]
    return " ".join(useful[-n:])[:240]


# -- tools -----------------------------------------------------------------------------------

class FindPackageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=2, max_length=100, description="App name to look for.")


class FindPackageTool(Tool):
    name = "find_package"
    description = ("Search the Windows Package Manager (winget) for apps that can be installed. "
                   "Returns names, package ids, versions and sources. Installs nothing.")
    Input = FindPackageInput
    timeout_s = 120.0

    def __init__(self, winget: Winget):
        self.winget = winget

    def run(self, args: FindPackageInput, ctx: ToolContext) -> ToolResult:
        try:
            pkgs = self.winget.search(args.query, ctx.cancel)
        except WingetError as exc:
            return ToolResult.failure("winget_failed", str(exc))
        if not pkgs:
            return ToolResult.success(f"No installable app found for '{args.query}'.",
                                      packages=[])
        return ToolResult.success(
            f"Found {len(pkgs)} package(s) for '{args.query}'.",
            packages=[{"name": p.name, "id": p.id, "version": p.version, "source": p.source}
                      for p in pkgs])


class PackageRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(None, max_length=128, description="Exact winget package id.")
    query: str | None = Field(None, max_length=100, description="App name, if no id is known.")
    source: Source = "winget"

    @model_validator(mode="after")
    def _one(self) -> PackageRef:
        if not (self.id or (self.query or "").strip()):
            raise ValueError("give id or query")
        if self.id and not _ID.match(self.id):
            raise ValueError("invalid package id")
        return self


class _ChangeTool(Tool):
    """Shared resolution + confirmation text for install/uninstall/update."""

    permission = Permission.CONFIRM
    verb = ""

    def __init__(self, winget: Winget, on_change: Callable[[], None] = lambda: None):
        self.winget = winget
        self.on_change = on_change
        self._resolved: dict[str, Package | WingetError] = {}

    def _key(self, args: PackageRef) -> str:
        return f"{args.id}|{args.query}|{args.source}"

    def _find(self, args: PackageRef, cancel: CancelToken) -> Package:
        raise NotImplementedError

    def resolve(self, args: PackageRef, cancel: CancelToken) -> Package:
        key = self._key(args)
        if key not in self._resolved:
            try:
                self._resolved[key] = self._find(args, cancel)
            except WingetError as exc:
                self._resolved[key] = exc
        got = self._resolved[key]
        if isinstance(got, WingetError):
            raise got
        return got

    def describe(self, args: PackageRef) -> str:
        try:
            pkg = self.resolve(args, CancelToken())
        except WingetError as exc:
            return f"{self.verb} '{args.id or args.query}' with winget ({exc})"
        extra = ("\n\nThe installer may ask Windows for administrator permission."
                 if self.verb != "Uninstall" else "")
        return f"{self.verb} {pkg.label()} using winget?{extra}"


class InstallPackageTool(_ChangeTool):
    name = "install_package"
    description = ("Install an app with winget. Always asks the user to confirm first, showing "
                   "the exact package. Use find_package first if you don't know the id.")
    Input = PackageRef
    timeout_s = 1900.0
    verb = "Install"

    def _find(self, args: PackageRef, cancel: CancelToken) -> Package:
        if args.id:
            pkg = Package(args.id, args.id, source=args.source)
        else:
            hits = self.winget.search(args.query or "", cancel)
            if not hits:
                raise WingetError(f"I couldn't find an app called '{args.query}' in winget.")
            pkg = hits[0]
        pkg.details = self.winget.show(pkg.id, pkg.source or args.source, cancel)
        pkg.name = pkg.details.get("Name", pkg.name)
        pkg.version = pkg.version or pkg.details.get("Version", "")
        return pkg

    def run(self, args: PackageRef, ctx: ToolContext) -> ToolResult:
        try:
            pkg = self.resolve(args, ctx.cancel)
            if self.winget.is_installed(pkg.id, ctx.cancel):
                return ToolResult.success(f"{pkg.name} is already installed.", id=pkg.id,
                                          app=pkg.name, already_installed=True)
            code, out = self.winget.install(pkg.id, pkg.source or args.source, ctx.cancel)
            ok = self.winget.is_installed(pkg.id, ctx.cancel)
        except WingetError as exc:
            return ToolResult.failure("winget_failed", str(exc))
        finally:
            self._resolved.pop(self._key(args), None)
        if not ok:
            return ToolResult.failure("install_failed", f"Installing {pkg.name} didn't finish: "
                                      f"{_tail(out) or f'winget exit code {code:#x}'}.",
                                      id=pkg.id)
        self.on_change()
        return ToolResult.success(f"{pkg.name} is installed.", id=pkg.id, app=pkg.name,
                                  version=pkg.version, verified=True)


class UninstallPackageTool(_ChangeTool):
    name = "uninstall_package"
    description = "Uninstall an installed app with winget. Always asks the user to confirm first."
    Input = PackageRef
    timeout_s = 1300.0
    verb = "Uninstall"

    def _find(self, args: PackageRef, cancel: CancelToken) -> Package:
        hits = self.winget.installed(args.id or args.query or "", cancel,
                                     exact_id=bool(args.id))
        if not hits:
            raise WingetError(f"'{args.id or args.query}' doesn't appear to be installed.")
        return hits[0]

    def run(self, args: PackageRef, ctx: ToolContext) -> ToolResult:
        try:
            pkg = self.resolve(args, ctx.cancel)
            code, out = self.winget.uninstall(pkg.id, ctx.cancel)
            still = _ID.match(pkg.id) is not None and self.winget.is_installed(pkg.id, ctx.cancel)
        except WingetError as exc:
            return ToolResult.failure("winget_failed", str(exc))
        finally:
            self._resolved.pop(self._key(args), None)
        if still:
            return ToolResult.failure("uninstall_failed", f"{pkg.name} is still installed: "
                                      f"{_tail(out) or f'winget exit code {code:#x}'}.")
        self.on_change()
        return ToolResult.success(f"{pkg.name} was uninstalled.", id=pkg.id)


class ListUpdatesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListUpdatesTool(Tool):
    name = "list_updates"
    description = "List installed apps that have updates available (winget). Changes nothing."
    Input = ListUpdatesInput
    timeout_s = 150.0

    def __init__(self, winget: Winget):
        self.winget = winget

    def run(self, args: ListUpdatesInput, ctx: ToolContext) -> ToolResult:
        try:
            ups = self.winget.upgrades(ctx.cancel)
        except WingetError as exc:
            return ToolResult.failure("winget_failed", str(exc))
        if not ups:
            return ToolResult.success("Everything is up to date.", updates=[])
        return ToolResult.success(
            f"{len(ups)} app(s) can be updated.",
            updates=[{"name": p.name, "id": p.id, "installed": p.version,
                      "available": p.available} for p in ups[:30]])


class UpdatePackageTool(_ChangeTool):
    name = "update_package"
    description = "Update one installed app to its latest version with winget (asks first)."
    Input = PackageRef
    timeout_s = 1900.0
    verb = "Update"

    def _find(self, args: PackageRef, cancel: CancelToken) -> Package:
        ups = self.winget.upgrades(cancel)
        want = (args.id or args.query or "").lower()
        for p in ups:
            if p.id.lower() == want or want in p.name.lower():
                return p
        raise WingetError(f"No update is available for '{args.id or args.query}'.")

    def describe(self, args: PackageRef) -> str:
        try:
            p = self.resolve(args, CancelToken())
        except WingetError as exc:
            return f"Update '{args.id or args.query}' ({exc})"
        return (f"Update {p.name} ({p.id}) from {p.version} to {p.available} using winget?"
                "\n\nThe installer may ask Windows for administrator permission.")

    def run(self, args: PackageRef, ctx: ToolContext) -> ToolResult:
        try:
            pkg = self.resolve(args, ctx.cancel)
            code, out = self.winget.upgrade(pkg.id, ctx.cancel)
        except WingetError as exc:
            return ToolResult.failure("winget_failed", str(exc))
        finally:
            self._resolved.pop(self._key(args), None)
        if code not in (0, NO_UPGRADE):
            return ToolResult.failure("update_failed", f"Updating {pkg.name} failed: "
                                      f"{_tail(out) or f'winget exit code {code:#x}'}.")
        self.on_change()
        return ToolResult.success(f"{pkg.name} is updated to {pkg.available}.", id=pkg.id)


def package_tools(winget: Winget, on_change: Callable[[], None]) -> list[Tool]:
    return [FindPackageTool(winget), InstallPackageTool(winget, on_change),
            UninstallPackageTool(winget, on_change), ListUpdatesTool(winget),
            UpdatePackageTool(winget, on_change)]

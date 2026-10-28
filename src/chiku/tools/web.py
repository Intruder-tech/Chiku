"""Open web pages in the default browser, with URL validation and domain approval."""

from __future__ import annotations

import ipaddress
import re
import webbrowser
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

from chiku.config import PermissionSettings
from chiku.tools.base import Permission, Tool, ToolContext, ToolResult

_DOMAINISH = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?(/.*)?$", re.I)


class UrlRejected(ValueError):
    pass


def validate_url(raw: str) -> str:
    s = raw.strip()
    if not s or len(s) > 2048:
        raise UrlRejected("URL is empty or too long")
    if any(ord(c) < 32 or c.isspace() for c in s):
        raise UrlRejected("URL contains spaces or control characters")
    if "://" not in s:
        if not _DOMAINISH.match(s):
            raise UrlRejected("that doesn't look like a web address")
        s = "https://" + s
    parts = urlsplit(s)
    if parts.scheme.lower() not in ("http", "https"):
        raise UrlRejected("only http and https links can be opened")
    if parts.username or parts.password:
        raise UrlRejected("links with embedded credentials are not allowed")
    host = parts.hostname
    if not host:
        raise UrlRejected("URL has no host")
    try:
        host_ascii = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlRejected("invalid host name") from exc
    netloc = host_ascii + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def domain_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for d in allowed:
        d = d.lower().strip().lstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


class OpenUrlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(
        ..., min_length=3, max_length=2048, description="http(s) URL or domain to open."
    )


class OpenUrlTool(Tool):
    name = "open_url"
    description = (
        "Show a web page to the user by opening it in their browser. This does NOT give you the "
        "page's text; to read or summarise a page yourself, use read_web_page."
    )
    Input = OpenUrlInput
    timeout_s = 10.0

    def __init__(self, permissions: Callable[[], PermissionSettings],
                 opener: Callable[[str], bool] = webbrowser.open):
        self.permissions = permissions
        self._open = opener

    def permission_for(self, args: OpenUrlInput) -> Permission:
        try:
            url = validate_url(args.url)
        except UrlRejected:
            return Permission.AUTO  # run() rejects it with a clear message
        host = host_of(url)
        if (url.startswith("https://") and not is_ip_literal(host)
                and domain_allowed(host, self.permissions().allowed_url_domains)):
            return Permission.AUTO
        return Permission.CONFIRM

    def describe(self, args: OpenUrlInput) -> str:
        try:
            return f"Open {validate_url(args.url)} in your browser"
        except UrlRejected:
            return f"Open '{args.url}'"

    def run(self, args: OpenUrlInput, ctx: ToolContext) -> ToolResult:
        try:
            url = validate_url(args.url)
        except UrlRejected as exc:
            return ToolResult.failure("invalid_url", f"I can't open that link: {exc}.")
        if not self._open(url):
            return ToolResult.failure("browser_failed",
                                      "Windows couldn't open the default browser.")
        return ToolResult.success(f"Opened {host_of(url)} in your browser.", url=url)

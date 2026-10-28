"""Fetch a public web page and extract readable text, defensively.

Guards: http(s) only, no private/loopback/link-local destinations (checked on every
redirect hop), at most 5 redirects, 2 MB body cap, HTML/plain text only, short timeouts.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from chiku.tools.web import UrlRejected, validate_url

log = logging.getLogger(__name__)

# Honest identification; Chiku fetches single pages on the user's request, never crawls.
USER_AGENT = "Chiku/0.3 (personal desktop voice assistant; single user)"
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class FetchError(RuntimeError):
    """Message is safe to show and speak."""


@dataclass
class Page:
    url: str
    title: str
    text: str
    truncated: bool


def check_public_host(host: str) -> None:
    """Refuse hosts that resolve to non-public addresses (SSRF protection)."""
    try:
        addrs = {str(ai[4][0]) for ai in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(f"I couldn't find the site {host}.") from exc
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global:
            raise FetchError("That link points to a private or local network address, "
                             "so I won't open it.")


_SKIP = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "aside",
         "form", "iframe", "button", "select", "canvas"}
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
          "section", "article", "main", "blockquote", "pre", "table", "dd", "dt"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_title = False
        self.title = ""
        self.all: list[str] = []
        self.main: list[str] = []
        self.main_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in _VOID:
            if tag == "br":
                self._add("\n")
            return
        if tag in _SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in ("main", "article"):
            self.main_depth += 1
        if tag in _BLOCK:
            self._add("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if tag in _SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in ("main", "article") and self.main_depth:
            self.main_depth -= 1
        if tag in _BLOCK:
            self._add("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        elif not self.skip:
            self._add(data)

    def _add(self, s: str) -> None:
        self.all.append(s)
        if self.main_depth:
            self.main.append(s)


def _tidy(parts: list[str]) -> str:
    text = "".join(parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    lines = [ln.strip() for ln in text.split("\n")]
    # drop very short menu-like fragments
    lines = [ln for ln in lines if len(ln) > 2]
    return re.sub(r"\n{2,}", "\n", "\n".join(lines)).strip()


def extract_text(html_text: str) -> tuple[str, str]:
    p = _Extractor()
    try:
        p.feed(html_text)
        p.close()
    except Exception as exc:  # malformed HTML: use what was parsed
        log.debug("html parse stopped early: %s", exc)
    main = _tidy(p.main)
    body = main if len(main) >= 400 else _tidy(p.all)
    return re.sub(r"\s+", " ", p.title).strip(), body


def fetch_page(
    url: str,
    *,
    max_bytes: int = 2_000_000,
    max_chars: int = 6000,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
    host_check: Callable[[str], None] = check_public_host,
) -> Page:
    try:
        current = validate_url(url)
    except UrlRejected as exc:
        raise FetchError(f"I can't open that link: {exc}.") from exc
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"}
    with httpx.Client(transport=transport, timeout=httpx.Timeout(timeout, connect=5.0),
                      follow_redirects=False, headers=headers) as client:
        for _hop in range(6):
            host = urlsplit(current).hostname or ""
            host_check(host)
            try:
                with client.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = r.headers.get("location")
                        if not loc:
                            raise FetchError("The page redirected without a destination.")
                        try:
                            current = validate_url(urljoin(current, loc))
                        except UrlRejected as exc:
                            raise FetchError(f"The page redirected somewhere unsafe: {exc}.") \
                                from exc
                        continue
                    if r.status_code in (401, 403, 429, 451):
                        # Respect it: no retries with disguised clients.
                        raise FetchError("That site doesn't allow automated reading "
                                         f"(HTTP {r.status_code}), so I only have the search "
                                         "snippet for it.")
                    if r.status_code != 200:
                        raise FetchError(f"The page returned an error (HTTP {r.status_code}).")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype and ctype not in _TEXT_TYPES:
                        raise FetchError(f"That link isn't a readable web page ({ctype}).")
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            break
                    encoding = r.encoding or "utf-8"
            except httpx.TimeoutException as exc:
                raise FetchError("The page took too long to load.") from exc
            except httpx.HTTPError as exc:
                raise FetchError("I couldn't load that page.") from exc
            raw = bytes(body[:max_bytes]).decode(encoding, errors="replace")
            if ctype == "text/plain":
                title, text = current, re.sub(r"\s+\n", "\n", raw).strip()
            else:
                title, text = extract_text(raw)
            truncated = len(text) > max_chars
            return Page(url=current, title=title or host, text=text[:max_chars],
                        truncated=truncated)
    raise FetchError("The page redirected too many times.")

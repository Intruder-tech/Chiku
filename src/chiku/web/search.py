"""Web search providers. Only the query string is sent; the API key travels in a header only."""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from chiku.config import WebSettings

log = logging.getLogger(__name__)

BRAVE_KEY_NAME = "brave_search_api_key"


class SearchError(RuntimeError):
    """Message is safe to show and speak."""


class SearchNotConfigured(SearchError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    age: str = ""


class SearchProvider(Protocol):
    name: str

    def configured(self) -> bool: ...

    def search(self, query: str, count: int) -> list[SearchResult]: ...


_TAG = re.compile(r"<[^>]+>")


def plain(text: str, limit: int) -> str:
    text = html.unescape(_TAG.sub("", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class BraveSearch:
    name = "Brave Search"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(
        self,
        key_getter: Callable[[], str | None],
        settings: Callable[[], WebSettings],
        transport: httpx.BaseTransport | None = None,
    ):
        self._key = key_getter
        self._settings = settings
        self._client = httpx.Client(transport=transport, timeout=httpx.Timeout(10.0, connect=5.0))

    def configured(self) -> bool:
        try:
            return bool(self._key())
        except Exception:
            return False

    def search(self, query: str, count: int) -> list[SearchResult]:
        try:
            key = self._key()
        except Exception as exc:
            raise SearchNotConfigured(f"Can't read the search API key: {exc}") from exc
        if not key:
            raise SearchNotConfigured(
                "Web search isn't set up yet: add a Brave Search API key in Settings → Web."
            )
        s = self._settings()
        params: dict[str, str | int] = {
            "q": query.strip()[:400],
            "count": max(1, min(count, 10)),
            "safesearch": s.safesearch,
            "text_decorations": "false",
        }
        if s.country:
            params["country"] = s.country.upper()
        try:
            r = self._client.get(
                self.ENDPOINT, params=params,
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise SearchError("The web search timed out.") from exc
        except httpx.HTTPError as exc:
            raise SearchError("I couldn't reach Brave Search. Is the internet connected?") from exc
        if r.status_code in (401, 403):
            raise SearchError("Brave Search rejected the API key. Check it in Settings → Web.")
        if r.status_code == 429:
            raise SearchError("The Brave Search rate limit was reached. Try again in a bit.")
        if r.status_code != 200:
            raise SearchError(f"Brave Search returned an error ({r.status_code}).")
        try:
            items = (r.json().get("web") or {}).get("results") or []
        except ValueError as exc:
            raise SearchError("Brave Search sent an unreadable response.") from exc
        out: list[SearchResult] = []
        for it in items:
            url = str(it.get("url") or "")
            if not url.startswith(("https://", "http://")):
                continue
            out.append(SearchResult(
                title=plain(str(it.get("title") or url), 150),
                url=url[:2048],
                snippet=plain(str(it.get("description") or ""), 320),
                age=plain(str(it.get("age") or ""), 40),
            ))
        log.info("web search: %d results", len(out))
        return out[:count]

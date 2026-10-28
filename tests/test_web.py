import pytest

from chiku.cancel import CancelToken
from chiku.config import PermissionSettings
from chiku.tools.base import Permission, ToolContext
from chiku.tools.web import OpenUrlTool, UrlRejected, domain_allowed, validate_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("youtube.com", "https://youtube.com"),
        ("https://en.wikipedia.org/wiki/GPU", "https://en.wikipedia.org/wiki/GPU"),
        ("HTTP://Example.COM/a?b=1", "http://example.com/a?b=1"),
        ("https://bücher.de", "https://xn--bcher-kva.de"),
    ],
)
def test_validate_url_accepts(raw: str, expected: str) -> None:
    assert validate_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["file:///C:/Windows/System32", "javascript:alert(1)", "https://user:pw@evil.com",
     "ms-settings:privacy", "not a url", "https://exa mple.com", "", "http://" + "a" * 3000],
)
def test_validate_url_rejects(raw: str) -> None:
    with pytest.raises(UrlRejected):
        validate_url(raw)


def test_domain_allowed_matches_subdomains_only() -> None:
    allowed = ["google.com", "wikipedia.org"]
    assert domain_allowed("www.google.com", allowed)
    assert domain_allowed("en.wikipedia.org", allowed)
    assert not domain_allowed("google.com.evil.io", allowed)
    assert not domain_allowed("notgoogle.com", allowed)


def make(perms: PermissionSettings | None = None) -> tuple[OpenUrlTool, list[str]]:
    opened: list[str] = []
    p = perms or PermissionSettings()
    return OpenUrlTool(lambda: p, opener=lambda u: opened.append(u) or True), opened


def test_permission_levels() -> None:
    t, _ = make()
    assert t.permission_for(t.Input(url="https://www.youtube.com/watch")) is Permission.AUTO
    assert t.permission_for(t.Input(url="http://youtube.com")) is Permission.CONFIRM
    assert t.permission_for(t.Input(url="https://random-site.example")) is Permission.CONFIRM
    assert t.permission_for(t.Input(url="https://127.0.0.1:8080")) is Permission.CONFIRM


def test_run_opens_validated_url() -> None:
    t, opened = make()
    res = t.run(t.Input(url="github.com"), ToolContext(CancelToken()))
    assert res.ok and opened == ["https://github.com"]


def test_run_refuses_dangerous_scheme() -> None:
    t, opened = make()
    res = t.run(t.Input(url="file:///C:/secret.txt"), ToolContext(CancelToken()))
    assert not res.ok and res.error_code == "invalid_url" and opened == []

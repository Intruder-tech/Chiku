import pytest

from chiku.cancel import CancelToken
from chiku.intents import Intent, parse_command
from chiku.tools.base import Permission, ToolContext
from chiku.tools.packages import (
    NOT_FOUND,
    FindPackageTool,
    InstallPackageTool,
    ListUpdatesTool,
    UninstallPackageTool,
    UpdatePackageTool,
    Winget,
    parse_show,
    parse_table,
)

SEARCH = """   - \r   \\ \rName             Id                         Version  Source
----------------------------------------------------------------------
VLC media player VideoLAN.VLC               3.0.21   winget
VLC UWP          9NBLGGH4VVNH               Unknown  msstore
Evil             ../../bad                  1.0      winget
"""
SHOW = """Found VLC media player [VideoLAN.VLC]
Version: 3.0.21
Publisher: VideoLAN
Description: Free multimedia player.
License: GPL-2.0
"""
UPGRADES = """Name        Id              Version Available Source
------------------------------------------------------
Zoom        Zoom.Zoom       6.1     6.2.5     winget
Git         Git.Git         2.45.0  2.47.1    winget
2 upgrades available.
"""


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.installed: set[str] = set()
        self.fail_user_scope = False

    def __call__(self, args: list[str], timeout: float, cancel: CancelToken) -> tuple[int, str]:
        assert "--disable-interactivity" in args  # never prompts in a hidden console
        self.calls.append(args)
        cmd = args[0]
        if cmd == "search":
            return 0, SEARCH
        if cmd == "show":
            return 0, SHOW
        if cmd == "list":
            key = "--id" if "--id" in args else "--query"
            pid = args[args.index(key) + 1].lower()
            hits = [i for i in self.installed if i.lower() == pid or pid in i.lower()]
            if not hits:
                return NOT_FOUND, "No installed package found matching input criteria."
            rows = "\n".join(f"{h:<20}{h:<20}1.0" for h in hits)
            return 0, f"Name                Id                  Version\n{'-' * 50}\n{rows}\n"
        if cmd == "install":
            if self.fail_user_scope and "--scope" in args:
                return 0x8A150010, "No applicable installer found; see logs for more details."
            self.installed.add(args[args.index("--id") + 1])
            return 0, "Successfully installed"
        if cmd == "uninstall":
            self.installed.discard(args[args.index("--id") + 1])
            return 0, "Successfully uninstalled"
        if cmd == "upgrade":
            return (0, "Successfully installed") if "--id" in args else (0, UPGRADES)
        raise AssertionError(args)


def ctx() -> ToolContext:
    return ToolContext(CancelToken())


def test_parse_table_handles_spinner_and_footer() -> None:
    rows = parse_table(SEARCH)
    assert [r["Id"] for r in rows] == ["VideoLAN.VLC", "9NBLGGH4VVNH", "../../bad"]
    assert rows[0]["Name"] == "VLC media player" and rows[0]["Source"] == "winget"
    assert [r["Available"] for r in parse_table(UPGRADES)] == ["6.2.5", "2.47.1"]


def test_parse_show() -> None:
    d = parse_show(SHOW)
    assert d["Publisher"] == "VideoLAN" and d["Id"] == "VideoLAN.VLC" and d["Version"] == "3.0.21"


def test_search_ranks_and_rejects_bad_ids() -> None:
    hits = Winget(FakeRunner()).search("vlc", CancelToken())
    assert [p.id for p in hits] == ["VideoLAN.VLC", "9NBLGGH4VVNH"]  # '../../bad' dropped


def test_find_tool_installs_nothing() -> None:
    r = FakeRunner()
    res = FindPackageTool(Winget(r)).run(FindPackageTool.Input(query="vlc"), ctx())
    assert res.ok and res.data["packages"][0]["id"] == "VideoLAN.VLC"
    assert all(c[0] == "search" for c in r.calls)


def test_install_requires_confirmation_and_shows_exact_package() -> None:
    changed = []
    t = InstallPackageTool(Winget(FakeRunner()), lambda: changed.append(1))
    args = t.Input(query="vlc")
    assert t.permission_for(args) is Permission.CONFIRM
    desc = t.describe(args)
    assert "VideoLAN.VLC" in desc and "VideoLAN" in desc and "3.0.21" in desc


def test_install_verifies_and_refreshes_catalog() -> None:
    r, changed = FakeRunner(), []
    t = InstallPackageTool(Winget(r), lambda: changed.append(1))
    res = t.run(t.Input(query="vlc"), ctx())
    assert res.ok and res.data["verified"] and changed == [1]
    install = next(c for c in r.calls if c[0] == "install")
    assert install[:4] == ["install", "--id", "VideoLAN.VLC", "-e"]
    assert "--silent" in install and "--scope" in install  # per-user first
    again = t.run(t.Input(query="vlc"), ctx())
    assert again.ok and again.data["already_installed"]


def test_install_falls_back_to_default_scope() -> None:
    r = FakeRunner()
    r.fail_user_scope = True
    t = InstallPackageTool(Winget(r), lambda: None)
    assert t.run(t.Input(id="VideoLAN.VLC"), ctx()).ok
    installs = [c for c in r.calls if c[0] == "install"]
    assert len(installs) == 2 and "--scope" not in installs[1]


def test_install_failure_is_reported_not_claimed() -> None:
    class Broken(FakeRunner):
        def __call__(self, args, timeout, cancel):  # noqa: ANN001
            if args[0] == "install":
                self.calls.append(args)
                return 0x8A150011, "Installer failed with exit code: 1603"
            return super().__call__(args, timeout, cancel)

    t = InstallPackageTool(Winget(Broken()), lambda: None)
    res = t.run(t.Input(id="VideoLAN.VLC"), ctx())
    assert not res.ok and res.error_code == "install_failed" and "1603" in res.message


def test_uninstall_confirms_and_verifies() -> None:
    r = FakeRunner()
    r.installed.add("Zoom.Zoom")
    t = UninstallPackageTool(Winget(r), lambda: None)
    args = t.Input(query="zoom")
    assert t.permission_for(args) is Permission.CONFIRM
    assert "Uninstall" in t.describe(args)
    assert t.run(args, ctx()).ok and "Zoom.Zoom" not in r.installed


def test_updates_list_and_update_one() -> None:
    r = FakeRunner()
    lst = ListUpdatesTool(Winget(r)).run(ListUpdatesTool.Input(), ctx())
    assert [u["id"] for u in lst.data["updates"]] == ["Zoom.Zoom", "Git.Git"]
    up = UpdatePackageTool(Winget(r), lambda: None)
    args = up.Input(query="git")
    assert up.permission_for(args) is Permission.CONFIRM and "2.47.1" in up.describe(args)
    assert up.run(args, ctx()).ok


@pytest.mark.parametrize("bad", ["../x", "a b", "x;calc", "-flag"])
def test_invalid_ids_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        InstallPackageTool.Input(id=bad)


@pytest.mark.parametrize(("text", "intent"), [
    ("install VLC", Intent("install", "VLC")),
    ("Chiku, please install Spotify for me", Intent("install", "Spotify")),
    ("uninstall Zoom", Intent("uninstall", "Zoom")),
    ("update VS Code", Intent("update", "VS Code")),
    ("check for updates", Intent("check_updates", "")),
    ("are there any updates", Intent("check_updates", "")),
    ("install the latest updates please", Intent("check_updates", "")),
])
def test_package_intents(text: str, intent: Intent) -> None:
    assert parse_command(text) == intent

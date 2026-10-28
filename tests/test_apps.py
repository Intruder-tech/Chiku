from chiku.cancel import CancelToken
from chiku.config import PermissionSettings
from chiku.tools.apps import (
    AppCatalog,
    AppEntry,
    OpenAppTool,
    Snapshot,
    is_sensitive,
    normalise,
    parse_discovery,
)
from chiku.tools.base import Permission, ToolContext

from .conftest import FAKE_APPS, LaunchRecorder


def tool(catalog: AppCatalog, rec: LaunchRecorder,
         perms: PermissionSettings | None = None) -> OpenAppTool:
    p = perms or PermissionSettings()
    return OpenAppTool(catalog, lambda: p, launcher=rec.launch, verifier=rec.verify,
                       snapshot=lambda: Snapshot(set(), set()))


def ctx() -> ToolContext:
    return ToolContext(cancel=CancelToken())


def test_parse_discovery_filters_junk_scripts_and_duplicates() -> None:
    items = [
        {"k": "lnk", "n": "Google Chrome", "l": "C:/a.lnk", "t": "C:/chrome.exe"},
        {"k": "appx", "n": "Google Chrome", "l": "Chrome", "t": ""},
        {"k": "lnk", "n": "Uninstall Foo", "l": "C:/u.lnk", "t": "C:/unins.exe"},
        {"k": "lnk", "n": "Run backup", "l": "C:/b.lnk", "t": "C:/backup.bat"},
        {"k": "lnk", "n": "Evil", "l": 'C:/x.lnk" & calc', "t": ""},
        {"k": "appx", "n": "Calculator", "l": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
        {"k": "zip", "n": "Weird", "l": "x"},
    ]
    names = [e.name for e in parse_discovery(items)]
    assert names == ["Google Chrome", "Calculator"]
    assert parse_discovery(items)[0].kind == "lnk"  # shortcut wins: it has the exe path


def test_search_uses_aliases_and_ranks(catalog: AppCatalog) -> None:
    assert catalog.best("chrome")[0].name == "Google Chrome"
    assert catalog.best("VS Code")[0].name == "Visual Studio Code"
    assert catalog.best("the notepad app")[0].name == "Notepad"
    assert catalog.best("photoshop") is None


def test_normalise() -> None:
    assert normalise("The Notepad App!") == "notepad"
    assert normalise("Microsoft Edge (x86)") == "microsoft edge"


def test_exe_name() -> None:
    assert FAKE_APPS[0].exe_name == "chrome.exe"
    assert FAKE_APPS[1].exe_name == ""


def test_sensitive_apps_need_confirmation(catalog: AppCatalog) -> None:
    rec = LaunchRecorder()
    t = tool(catalog, rec)
    assert is_sensitive(FAKE_APPS[2])
    assert t.permission_for(t.Input(app_name="command prompt")) is Permission.CONFIRM
    assert t.permission_for(t.Input(app_name="chrome")) is Permission.AUTO


def test_auto_open_can_be_disabled(catalog: AppCatalog) -> None:
    t = tool(catalog, LaunchRecorder(), PermissionSettings(auto_allow_open_apps=False))
    assert t.permission_for(t.Input(app_name="chrome")) is Permission.CONFIRM


def test_open_success_is_verified(catalog: AppCatalog) -> None:
    rec = LaunchRecorder()
    res = tool(catalog, rec).run(OpenAppTool.Input(app_name="Chrome"), ctx())
    assert res.ok and res.data["verified"] == "process started"
    assert rec.launched[0].name == "Google Chrome"


def test_open_unknown_app_suggests_alternatives(catalog: AppCatalog) -> None:
    rec = LaunchRecorder()
    res = tool(catalog, rec).run(OpenAppTool.Input(app_name="Photoshop"), ctx())
    assert not res.ok and res.error_code == "app_not_found"
    assert rec.launched == []


def test_open_unverified_reports_failure(catalog: AppCatalog) -> None:
    rec = LaunchRecorder(verified=(False, "no new process or window appeared"))
    res = tool(catalog, rec).run(OpenAppTool.Input(app_name="notepad"), ctx())
    assert not res.ok and res.error_code == "not_verified"
    assert "couldn't confirm" in res.message


def test_open_already_running(catalog: AppCatalog) -> None:
    rec = LaunchRecorder(verified=(True, "already running"))
    res = tool(catalog, rec).run(OpenAppTool.Input(app_name="chrome"), ctx())
    assert res.ok and "already running" in res.message


def test_launch_os_error(catalog: AppCatalog) -> None:
    rec = LaunchRecorder(error=OSError(2, "The system cannot find the file specified"))
    res = tool(catalog, rec).run(OpenAppTool.Input(app_name="chrome"), ctx())
    assert not res.ok and res.error_code == "launch_failed"


def test_catalog_survives_loader_failure() -> None:
    def boom() -> list[AppEntry]:
        raise RuntimeError("powershell missing")

    cat = AppCatalog(loader=boom)
    assert cat.entries() == [] and cat.best("chrome") is None

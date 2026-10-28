"""Pro workspace: conversations, command palette, usage stats, data controls, and the Profile,
Apps and context panel widgets (offscreen)."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from chiku.audit import usage_stats  # noqa: E402
from chiku.cancel import CancelToken  # noqa: E402
from chiku.conversations import ConversationStore  # noqa: E402
from chiku.llm.base import StreamChunk  # noqa: E402
from chiku.tools.base import ToolResult  # noqa: E402
from chiku.tools.packages import Package  # noqa: E402
from chiku.ui.components.palette import Command, rank  # noqa: E402

from .conftest import Harness, wait_for  # noqa: E402
from .test_ui import app, pump  # noqa: E402, F401  (the shared, cleanly torn down QApplication)

# -- conversations ------------------------------------------------------------------------------

def store(tmp_path: Path, keep: bool) -> ConversationStore:
    return ConversationStore(tmp_path / "conversations.json", keep=lambda: keep,
                             max_saved=lambda: 50)


def test_chats_stay_in_memory_unless_kept(tmp_path: Path) -> None:
    s = store(tmp_path, keep=False)
    s.add("user", "What's the capital of France?")
    s.add("assistant", "Paris.", "local · test")
    assert not (tmp_path / "conversations.json").exists()  # the privacy default
    assert s.active.title == "What's the capital of France?"


def test_kept_history_survives_a_restart(tmp_path: Path) -> None:
    s = store(tmp_path, keep=True)
    s.add("user", "Remind me what a haiku is " * 5)  # long: the title is shortened
    s.add("assistant", "A three-line poem.", "local · test",
          [{"title": "Haiku", "url": "https://example.com/haiku"}])
    first = s.active.id
    s.new()
    s.add("user", "hello")
    again = store(tmp_path, keep=True)
    assert [c.title for c in again.listed() if c.entries][1].endswith("…")
    conv = again.switch(first)
    assert [m.role for m in conv.history()] == ["user", "assistant"]
    assert conv.entries[1].sources[0]["url"] == "https://example.com/haiku"


def test_new_reuses_an_empty_chat_and_delete_moves_on(tmp_path: Path) -> None:
    s = store(tmp_path, keep=False)
    empty = s.new()
    assert s.new() is empty  # no pile of empty chats
    s.add("user", "one")
    one = s.active.id
    s.new()
    s.add("user", "two")
    s.delete(s.active.id)
    assert s.get(one) is not None and not s.active.entries


def test_forgetting_deletes_the_file_and_corrupt_files_start_fresh(tmp_path: Path) -> None:
    s = store(tmp_path, keep=True)
    s.add("user", "secret plans")
    s.forget_saved()
    assert not (tmp_path / "conversations.json").exists()
    (tmp_path / "conversations.json").write_text("{not json", encoding="utf-8")
    assert store(tmp_path, keep=True).listed()[0].entries == []


def test_switching_conversation_replaces_model_history(harness: Harness) -> None:
    from chiku.llm.base import ChatMessage

    harness.llm.scripts = [[StreamChunk("Still Paris.", done=True)]]
    a = harness.started()
    a.set_history([ChatMessage("user", "Capital of France?"), ChatMessage("assistant", "Paris.")])
    a.submit_text("Are you sure?")
    assert wait_for(lambda: harness.llm.calls)
    contents = [m.content for m in harness.llm.calls[0]]
    assert "Paris." in contents and any("Are you sure?" in c for c in contents)


# -- command palette ---------------------------------------------------------------------------

def _cmds() -> list[Command]:
    noop = lambda: None  # noqa: E731
    return [Command("Go to Settings", "Page", noop, "settings"),
            Command("Settings: Languages", "Settings", noop),
            Command("New chat", "Action", noop, "conversation"),
            Command("Open Google Chrome", "App", noop, "launch", hidden_until_typed=True),
            Command("Open Notepad", "App", noop, "launch", hidden_until_typed=True)]


def test_palette_ranks_prefix_first_and_hides_apps_until_typed() -> None:
    assert [c.title for c in rank(_cmds(), "")] == ["Go to Settings", "Settings: Languages",
                                                    "New chat"]
    assert rank(_cmds(), "sett")[0].title == "Settings: Languages"  # title starts with it
    assert [c.title for c in rank(_cmds(), "open chr")] == ["Open Google Chrome"]
    assert [c.title for c in rank(_cmds(), "conversation")] == ["New chat"]  # keywords count
    assert rank(_cmds(), "zzz") == []


# -- usage stats ----------------------------------------------------------------------------------

def test_usage_stats_count_only_successes() -> None:
    now = datetime(2026, 10, 28, tzinfo=UTC)
    old = (now - timedelta(days=30)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    entries = [
        {"ts": old, "tool": "open_app", "args": {"app_name": "chrome"}, "ok": True},
        {"ts": recent, "tool": "open_app", "args": {"app_name": "Chrome"}, "ok": True},
        {"ts": recent, "tool": "open_app", "args": {"app_name": "notepad"}, "ok": True},
        {"ts": recent, "tool": "open_app", "args": {"app_name": "Paint"}, "ok": False},
        {"ts": recent, "tool": "web_search", "args": {"query": "x"}, "ok": True},
        {"ts": recent, "tool": "install_package", "args": {"id": "VideoLAN.VLC"}, "ok": True},
        {"ts": recent, "tool": "find_files", "args": {}, "ok": True},
        {"ts": recent, "tool": "remember", "args": {}, "ok": True},
    ]
    s = usage_stats(entries, now)
    assert (s.actions, s.apps_opened, s.searches, s.apps_installed) == (7, 3, 1, 1)
    assert (s.file_actions, s.memories_saved, s.last_7_days) == (1, 1, 6)
    assert s.top_apps[0] == ("Chrome", 2) and s.since == "2026-09-28"


# -- data controls -------------------------------------------------------------------------

def test_export_and_wipe(harness: Harness, tmp_path: Path) -> None:
    from chiku import data_controls
    from chiku.paths import AppPaths

    a = harness.build()
    harness.memory.add("I like jazz", "preference")
    harness.audit.record("open_app", {"app_name": "Chrome"}, ToolResult.success("ok"), 5, "auto")
    paths = AppPaths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    paths.ensure()
    convs = ConversationStore(paths.conversations_file, keep=lambda: True, max_saved=lambda: 9)
    convs.add("user", "hi")
    paths.avatar_file.write_bytes(b"png")
    data = data_controls.export_all(a, convs)
    assert data["memories"][0]["text"] == "I like jazz"
    assert data["conversations"][0]["entries"][0]["text"] == "hi"
    assert data["usage"]["apps_opened"] == 1 and "llm" in data["settings"]
    data_controls.wipe(a, paths, convs)
    assert harness.memory.all() == [] and harness.audit.all() == []
    assert not paths.conversations_file.exists() and not paths.avatar_file.exists()


# -- widgets -------------------------------------------------------------------------------------

@pytest.mark.usefixtures("app")
def test_profile_page_saves_and_shows_data(harness: Harness, tmp_path: Path) -> None:
    from PySide6.QtGui import QColor, QImage

    from chiku.config import load_settings
    from chiku.paths import AppPaths
    from chiku.ui.profile_page import ProfilePage

    harness.settings.audio.barge_in_min_ms = 350  # the harness's 96 ms is below the minimum
    a = harness.build()
    harness.memory.add("I like jazz", "preference")
    harness.memory.add("My sister is Asha", "person")
    harness.audit.record("web_search", {"query": "x"}, ToolResult.success("ok"), 5, "auto")
    paths = AppPaths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    page = ProfilePage(a, paths, ask=lambda _w, _t: True)
    assert "Preferences (1)" in page.memories.text() and "People (1)" in page.memories.text()
    assert page.stats.searches == 1
    page.name.setText("Jalaj")
    page.say_as.setText("Juh-luj")
    page.interests.setText("cricket, cooking , ")
    page.length.setCurrentIndex(page.length.findData("brief"))
    assert page.save()
    saved = load_settings(paths.settings_file).persona
    assert saved.user_name == "Jalaj" and saved.pronunciations["Jalaj"] == "Juh-luj"
    assert saved.interests == ["cricket", "cooking"] and saved.response_length == "brief"
    img = QImage(300, 200, QImage.Format.Format_RGB32)
    img.fill(QColor("red"))
    src = tmp_path / "me.png"
    img.save(str(src))
    assert page.set_avatar(src) and QImage(str(paths.avatar_file)).width() == 256
    out = tmp_path / "export.json"
    page.export_to(out)
    assert json.loads(out.read_text(encoding="utf-8"))["settings"]["persona"]["user_name"] \
        == "Jalaj"
    page._delete()
    assert harness.memory.all() == [] and not paths.avatar_file.exists()
    page.deleteLater()


class FakeWinget:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def installed_all(self, cancel: CancelToken) -> list[Package]:
        self.calls.append("list")
        return [Package("Zoom", "Zoom.Zoom", "5.0", "", "winget"),
                Package("7-Zip", "7zip.7zip", "23.01", "24.08", "winget")]

    def upgrades(self, cancel: CancelToken) -> list[Package]:
        return [Package("7-Zip", "7zip.7zip", "23.01", "24.08", "winget")]

    def search(self, query: str, cancel: CancelToken, limit: int = 8) -> list[Package]:
        return [Package("VLC media player", "VideoLAN.VLC", "3.0.21", source="winget")]


def test_apps_page_lists_and_changes_through_tools(app) -> None:  # noqa: ANN001, F811
    from chiku.ui.apps_page import AppsPage

    calls: list[tuple[str, dict]] = []

    def run_tool(name: str, args: dict) -> ToolResult:
        calls.append((name, args))
        return ToolResult.success("VLC media player is installed.")

    winget = FakeWinget()
    page = AppsPage(winget, run_tool)
    page.ensure_loaded()
    assert pump(app, lambda: page.installed.rowCount() == 2)
    assert page.installed.item(0, 0).text() == "7-Zip"  # sorted by name
    page.filter.setText("zoom")
    assert page.installed.rowCount() == 1
    page._install()
    assert "Select an app" in page.status.text() and calls == []
    page.query.setText("vlc")
    page.search()
    assert pump(app, lambda: page.results.rowCount() == 1)
    page.results.selectRow(0)
    page._install()
    assert pump(app, lambda: calls and page.status.text().startswith("✓"))
    assert calls == [("install_package", {"id": "VideoLAN.VLC", "source": "winget"})]
    assert pump(app, lambda: winget.calls.count("list") == 2)  # refreshed after the change
    page.load_updates()
    assert pump(app, lambda: page.updates.rowCount() == 1)
    page.shutdown()
    page.deleteLater()


@pytest.mark.usefixtures("app")
def test_context_panel_and_chat_replay() -> None:
    from chiku.assistant import ToolActivity
    from chiku.ui.components.context_panel import MAX_ACTIONS, ContextPanel

    panel = ContextPanel()
    panel.set_sources([{"title": "BBC", "url": "https://www.bbc.com/news"},
                       {"title": "bad", "url": "javascript:alert(1)"}])
    assert "bbc.com" in panel.sources.text() and "javascript" not in panel.sources.text()
    assert not panel.sources.isHidden()
    for i in range(MAX_ACTIONS + 2):
        panel.add_tool(ToolActivity("open_app", "ok", f"opened app {i}"))
    lines = panel.recent_label.text().splitlines()
    assert len(lines) == MAX_ACTIONS and "opened app 7" in lines[0]
    panel.set_sources([])
    assert panel.sources.isHidden()

    from chiku.ui.components.chat import Bubble
    from chiku.ui.components.chat_page import ChatPage, greeting_for

    page = ChatPage()
    page.set_user("Jalaj")
    chat = page.chat
    assert page.is_new_chat and not page.greeting_box.isHidden() and chat.isHidden()
    assert page.greeting.text().endswith(", Jalaj") and page.greeting.text().startswith("Good ")
    chat.add_entry("user", "hi", stamp="09:15")  # the chat starts: the greeting goes
    chat.add_entry("assistant", "Hello!", "local · test", stamp="09:15")
    assert not page.is_new_chat and page.greeting_box.isHidden() and not chat.isHidden()
    assert not chat.is_empty
    page.reset()  # a new chat greets again
    assert page.is_new_chat and chat.is_empty and chat.column.count() == 1
    assert all(b.isHidden() for b in chat.widget().findChildren(Bubble))  # none left on screen
    from datetime import datetime

    assert greeting_for("A", datetime(2026, 1, 1, 9)) == "Good morning, A"
    assert greeting_for(None, datetime(2026, 1, 1, 21)) == "Good evening"
    panel.deleteLater()
    page.deleteLater()


def test_run_winget_reads_large_output_without_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: winget's output was read only after it exited, so a listing larger than the
    pipe buffer (`winget list` on a real PC) blocked winget forever."""
    import sys
    import time

    from chiku.tools import packages

    monkeypatch.setattr(packages.shutil, "which", lambda _n: sys.executable)
    t0 = time.monotonic()
    code, out = packages.run_winget(["-c", "print('x' * 400_000)"], 30, CancelToken())
    assert code == 0 and len(out.strip()) == 400_000
    assert time.monotonic() - t0 < 20

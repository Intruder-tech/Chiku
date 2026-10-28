"""UI component tests on Qt's offscreen platform (no window is shown)."""

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from chiku.assistant import Message, MicChanged, State, StateChanged, ToolActivity  # noqa: E402
from chiku.ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def app():  # noqa: ANN201
    inst = QApplication.instance()
    qapp = inst if isinstance(inst, QApplication) else QApplication([])
    yield qapp
    # Destroy every widget while the QApplication still exists; otherwise Python's shutdown
    # order can delete them after Qt is gone and the process crashes (exit code 127 in CI).
    import gc

    for w in qapp.topLevelWidgets():
        w.close()
        w.deleteLater()
    qapp.processEvents()
    gc.collect()
    qapp.processEvents()


def pump(app: QApplication, until, timeout: float = 2.0) -> bool:  # noqa: ANN001
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


def test_viewmodel_delivers_engine_events_on_ui_thread(app: QApplication) -> None:
    from chiku.ui.viewmodel import AssistantViewModel

    vm = AssistantViewModel()
    seen: list[tuple[str, object]] = []
    ui_thread = threading.current_thread()
    vm.state_changed.connect(lambda s, d: seen.append(("state", (s, threading.current_thread()))))
    vm.message.connect(lambda m: seen.append(("msg", m.text)))
    vm.mic_changed.connect(lambda on: seen.append(("mic", on)))
    t = threading.Thread(target=lambda: [vm.on_event(StateChanged(State.THINKING, "x")),
                                         vm.on_event(Message(1, "assistant", "hi", True)),
                                         vm.on_event(MicChanged(True))])
    t.start()
    t.join()
    assert pump(app, lambda: len(seen) == 3)
    assert seen[0][1] == (State.THINKING, ui_thread)  # slots run on the UI thread
    assert ("msg", "hi") in seen and ("mic", True) in seen
    assert vm.state is State.THINKING


def test_confirm_from_worker_round_trip(app: QApplication) -> None:
    from chiku.ui.viewmodel import AssistantViewModel

    vm = AssistantViewModel()
    vm.confirm_requested.connect(lambda n, d, box: (box.__setitem__("ok", "VLC" in d),
                                                    box["done"].set()))
    result: list[bool] = []
    t = threading.Thread(target=lambda: result.append(vm.confirm_from_worker("install_package",
                                                                             "Install VLC")))
    t.start()
    assert pump(app, lambda: bool(result), timeout=5)
    assert result == [True]


def test_chat_view_bubbles_sources_and_tools(app: QApplication) -> None:
    from chiku.ui.components.chat import ChatView

    links: list[str] = []
    view = ChatView(on_link=links.append)
    view.show_message(Message(1, "user", "what's new?", True))
    view.show_message(Message(2, "assistant", "Partial", False, "local · test"))
    view.show_message(Message(2, "assistant", "Full answer.", True, "local · test",
                              [{"title": "<b>Site</b>", "url": "https://news.example/a"},
                               {"title": "bad", "url": "javascript:alert(1)"}]))
    view.show_tool(ToolActivity("web_search", "ok", "Brave: 2 results"))
    assert view.welcome.isHidden()
    assert set(view.bubbles) == {1, 2}
    b = view.bubbles[2]
    assert b.text.text() == "Full answer." and b.sources_added
    assert "with web sources" in b.meta.text()
    src = [w for w in b.findChildren(type(b.text)) if w.objectName() == "sources"][0]
    assert "&lt;b&gt;Site" in src.text() and "javascript" not in src.text()  # escaped, filtered


def test_theme_switches_palettes(app: QApplication) -> None:
    dark = theme.apply("dark")
    assert theme.current_mode == "dark" and theme.PALETTES["dark"]["BG"] == theme.BG
    light = theme.apply("light")
    assert theme.PALETTES["light"]["BG"] == theme.BG and light != dark
    assert theme.resolve("system") in ("light", "dark")
    theme.apply("dark")


def test_voice_bar_and_compact(app: QApplication) -> None:
    from chiku.ui.compact import CompactWindow
    from chiku.ui.components.voice_bar import VoiceBar

    bar = VoiceBar()
    bar.set_state(State.THINKING, "transcribing")
    assert bar.state.text() == "Transcribing…"
    bar.set_component("microphone", True, "ok")
    bar.set_component("language model", False, "Ollama isn't running")
    assert "mic ✓" in bar.status.text() and "Ollama isn't running" in bar.status.text()
    bar.tick(State.SPEAKING, 0.0, 0.2, True)
    c = CompactWindow()
    c.set_state(State.LISTENING, "")
    c.tick(State.LISTENING, 0.05, True)
    assert c.state.text() == "Listening" and c.mic.isChecked()


@pytest.mark.parametrize("model_present", [True, False])
def test_languages_tab_switches_speech_model(app: QApplication, harness, tmp_path,  # noqa: ANN001
                                             monkeypatch: pytest.MonkeyPatch,
                                             model_present: bool) -> None:
    from chiku import models
    from chiku.paths import AppPaths
    from chiku.ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(models, "whisper_present", lambda _d, _n: model_present)
    a = harness.build()
    d = SettingsDialog(a, AppPaths(config_dir=tmp_path / "c", data_dir=tmp_path / "d"))
    assert d.lang_download.isHidden() is model_present  # offered only when missing
    d.lang_mode.setCurrentIndex(d.lang_mode.findData("auto"))
    d.lang_hi.setChecked(True)
    d.lang_other.setText("es, xx1, fr")
    d.hindi_voice.setCurrentText("hf_beta")
    data = a.settings.model_dump()
    d._language_settings(data)
    if model_present:
        assert (data["stt"]["model"], data["stt"]["language"]) == ("small", "auto")
    else:  # never switch to a model that isn't on disk
        assert (data["stt"]["model"], data["stt"]["language"]) == ("small.en", "en")
    assert data["stt"]["preferred_languages"] == ["en", "hi", "es", "fr"]
    assert data["tts"]["voices"]["hi"] == "hf_beta"
    d.close()
    d.deleteLater()

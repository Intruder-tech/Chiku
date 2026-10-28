"""Wake word: phrase matching, the listener's wake mode, screen lock, autostart, settings."""

import contextlib
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from chiku.assistant import Message, MicChanged, State, Woke  # noqa: E402
from chiku.wake import WakeHit, chime, find_wake, strip_wake  # noqa: E402

from .conftest import FakeCapture, FakeWake, Harness, wait_for  # noqa: E402
from .test_ui import app  # noqa: E402, F401  (the shared, cleanly torn down QApplication)

# -- phrase matching -----------------------------------------------------------------------

@pytest.mark.parametrize(("text", "phrase", "rest"), [
    ("Hey Chiku.", "hey chiku", ""),
    ("Hey, Chikku!", "hey chiku", ""),
    ("hey cheeku what's the time", "hey chiku", "what s the time"),
    ("Hi Chico.", "hey chiku", ""),
    ("Okay Chikoo, open notepad", "hey chiku", "open notepad"),
    ("Heychiku", "hey chiku", ""),
    ("Wake up!", "wake up", ""),
    ("Wake up, Chiku.", "wake up", ""),
    ("Wakeup.", "wake up", ""),
    ("Um, hey Chiku.", "hey chiku", ""),
    ("हे चीकू", "hey chiku", ""),
    ("Hechi ku.", "hey chiku", ""),
    ("Hi chiku, play music", "hey chiku", "play music"),
])
def test_wake_phrases(text: str, phrase: str, rest: str) -> None:
    assert find_wake(text) == WakeHit(phrase, rest)


@pytest.mark.parametrize("text", [
    "I bought some chiku at the market",  # the fruit, no greeting
    "I had a Chiku milkshake today",
    "Hey, check out this video",
    "Hey chicken",
    "make up your mind",
    "you should wake",
    "So I was telling him that he should wake up earlier",  # phrase too late in the speech
    "",
])
def test_not_wake_phrases(text: str) -> None:
    assert find_wake(text) is None


@pytest.mark.parametrize(("text", "rest"), [
    ("Hey Chiku, what time is it?", "what time is it?"),
    ("Wake up Chiku. Open Chrome.", "Open Chrome."),
    ("Hey Chiku.", ""),
    ("What time is it?", "What time is it?"),  # no phrase: unchanged
])
def test_strip_wake(text: str, rest: str) -> None:
    assert strip_wake(text) == rest


def test_chime_is_short_and_quiet() -> None:
    c = chime()
    assert 0.1 < len(c) / 24000 < 0.4 and float(abs(c).max()) < 0.3


# -- the listener ------------------------------------------------------------------------------

SPEECH = [0.9] * 20 + [0.0] * 12  # ~0.6 s of speech, then a pause that ends the segment


def wake_harness(h: Harness, hits: list[WakeHit | None], probs: list[float] = SPEECH) -> None:
    h.settings.wake.enabled = True
    h.wake = FakeWake(hits)
    h.capture = FakeCapture(list(probs))


def test_wake_word_opens_the_mic_and_chimes(harness: Harness) -> None:
    wake_harness(harness, [WakeHit("hey chiku", "")])
    a = harness.started()
    assert wait_for(lambda: harness.of(Woke))
    assert a.mic_active and not a.wake_listening
    assert MicChanged(True) in harness.events
    assert len(harness.player.played) == 1  # the chime, nothing spoken
    assert harness.stt.calls == 0  # the wake phrase alone is not a request
    assert wait_for(lambda: a.state is State.LISTENING)


def test_request_in_the_same_breath_is_answered(harness: Harness) -> None:
    wake_harness(harness, [WakeHit("hey chiku", "what time is it")])
    harness.stt.text = "Hey Chiku, what time is it?"
    harness.started()
    assert wait_for(lambda: harness.llm.calls)
    user = [m for m in harness.of(Message) if m.role == "user"]
    assert user[0].text == "what time is it?"  # the wake phrase is not part of the request


def test_other_speech_is_ignored(harness: Harness) -> None:
    wake_harness(harness, [None])
    a = harness.started()
    assert wait_for(lambda: harness.wake is not None and harness.wake.checked == 1)
    assert not harness.of(Woke) and not a.mic_active
    assert harness.stt.calls == 0 and harness.llm.calls == []
    assert wait_for(lambda: a.state is State.WAITING and a.wake_listening)
    a.shutdown()
    assert not a._listener_alive() and not harness.capture.active  # mic really closed


def test_wake_session_ends_after_quiet_follow_up(harness: Harness) -> None:
    wake_harness(harness, [WakeHit("wake up", "")])
    harness.settings.wake.follow_up_s = 0.3  # type: ignore[assignment]
    a = harness.started()
    assert wait_for(lambda: harness.of(Woke))
    assert wait_for(lambda: MicChanged(False) in harness.events, timeout=3)
    assert wait_for(lambda: a.state is State.WAITING and a.wake_listening)


def test_manual_mic_stays_on_and_off_returns_to_waiting(harness: Harness) -> None:
    wake_harness(harness, [], probs=[])
    a = harness.started()
    assert wait_for(lambda: a.wake_listening)
    a.set_mic(True)
    assert wait_for(lambda: a.mic_active and a.state is State.LISTENING)
    a.set_mic(False)
    assert wait_for(lambda: a.wake_listening and a.state is State.WAITING)


def test_locked_screen_keeps_the_mic_closed(harness: Harness) -> None:
    wake_harness(harness, [WakeHit("hey chiku", "")])
    harness.locked = True
    a = harness.started()
    assert not wait_for(lambda: harness.capture is not None and harness.capture.starts > 0,
                        timeout=0.5)
    assert not a.wake_listening
    harness.locked = False
    assert wait_for(lambda: harness.of(Woke), timeout=5)


def test_wake_off_by_default_never_opens_the_mic(harness: Harness) -> None:
    harness.wake = FakeWake([WakeHit("hey chiku", "")])
    harness.capture = FakeCapture(list(SPEECH))
    a = harness.started()
    assert a.state is State.IDLE and harness.capture.starts == 0


def test_enabling_wake_in_settings_starts_listening(harness: Harness) -> None:
    harness.wake = FakeWake([])
    harness.capture = FakeCapture([])
    a = harness.started()
    new = a.settings.model_copy(deep=True)
    new.wake.enabled = True
    a.apply_settings(new)
    assert wait_for(lambda: a.wake_listening and a.state is State.WAITING)
    off = new.model_copy(deep=True)
    off.wake.enabled = False
    a.apply_settings(off)
    assert wait_for(lambda: not a.wake_listening and a.state is State.IDLE)


# -- autostart ---------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_autostart_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    import winreg

    from chiku import autostart

    test_key = r"Software\ChikuTests\Run"  # never the real Run key
    monkeypatch.setattr(autostart, "RUN_KEY", test_key)
    try:
        assert not autostart.is_enabled()
        autostart.set_enabled(True)
        assert autostart.is_enabled()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_key) as key:
            value = winreg.QueryValueEx(key, autostart.VALUE_NAME)[0]
        assert value.endswith("--background")
        autostart.set_enabled(False)
        autostart.set_enabled(False)  # already off: no error
        assert not autostart.is_enabled()
    finally:
        for sub in (test_key, r"Software\ChikuTests"):
            with contextlib.suppress(OSError):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)


# -- settings ----------------------------------------------------------------------------------

@pytest.mark.usefixtures("app")
@pytest.mark.parametrize("model_present", [True, False])
def test_background_tab(harness: Harness, tmp_path, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
                        model_present: bool) -> None:
    from PySide6.QtWidgets import QMessageBox

    from chiku import autostart, models
    from chiku.config import load_settings
    from chiku.paths import AppPaths
    from chiku.ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(models, "whisper_present", lambda _d, _n: model_present)
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    calls: list[bool] = []
    monkeypatch.setattr(autostart, "set_enabled", calls.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: pytest.fail(str(a[2])))
    harness.settings.audio.barge_in_min_ms = 350  # the harness's 96 ms is below the UI minimum
    a = harness.build()
    paths = AppPaths(config_dir=tmp_path / "c", data_dir=tmp_path / "d")
    d = SettingsDialog(a, paths)
    assert d.wake_download.isHidden() is model_present  # offered only when missing
    d.wake_on.setChecked(True)
    d.follow_up.setValue(45)
    d.autostart.setChecked(True)
    d._save()
    saved = load_settings(paths.settings_file)
    assert saved.wake.enabled is model_present  # never on without the model
    assert saved.wake.follow_up_s == 45
    assert calls == ([True] if d.autostart.isEnabled() else [])
    d.close()
    d.deleteLater()

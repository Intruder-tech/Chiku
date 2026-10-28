"""Discord Rich Presence over a fake IPC pipe: what is sent, retries, and clearing on stop."""

import json
import struct
import threading

import pytest
from pydantic import ValidationError

from chiku.config import DiscordSettings
from chiku.discord_presence import REPO_URL, DiscordPresence, encode

from .conftest import wait_for

APP_ID = "1564988707596017754"


class FakePipe:
    """Discord's side of the pipe: answers the handshake and every SET_ACTIVITY."""

    def __init__(self, ready: bool = True, reject: bool = False) -> None:
        self.frames: list[tuple[int, dict]] = []
        self.ready = ready
        self.reject = reject
        self.closed = False
        self._out = bytearray()
        self._cv = threading.Condition()

    def write(self, data: bytes) -> int:
        op, n = struct.unpack("<ii", data[:8])
        payload = json.loads(data[8:8 + n])
        self.frames.append((op, payload))
        if op == 0:
            self._reply({"cmd": "DISPATCH", "evt": "READY" if self.ready else None,
                         "message": "Invalid Client ID"})
        elif op == 1:
            self._reply({"cmd": "SET_ACTIVITY", "evt": "ERROR" if self.reject else None,
                         "data": {"message": "bad"}, "nonce": payload["nonce"]})
        return len(data)

    def _reply(self, payload: dict) -> None:
        with self._cv:
            self._out += encode(1, payload)
            self._cv.notify_all()

    def read(self, n: int) -> bytes:
        with self._cv:
            self._cv.wait_for(lambda: len(self._out) >= n or self.closed, timeout=5)
            chunk, self._out = bytes(self._out[:n]), self._out[n:]
            return chunk

    def close(self) -> None:
        self.closed = True

    def activities(self) -> list[dict | None]:
        return [p["args"].get("activity") for op, p in self.frames
                if op == 1 and p.get("cmd") == "SET_ACTIVITY"]


def test_presence_shows_fixed_text_and_clears_on_stop() -> None:
    pipe = FakePipe()
    p = DiscordPresence(APP_ID, connect=lambda: pipe, refresh_s=60)
    p.start()
    assert wait_for(lambda: p.connected)
    assert pipe.frames[0] == (0, {"v": 1, "client_id": APP_ID})
    act = pipe.activities()[0]
    assert act is not None
    assert act["details"].startswith("Chatting with Chiku")
    assert "github.com/Intruder-tech/Chiku" in act["state"]  # the repository, on its own line
    assert act["buttons"] == [{"label": "Install Chiku from GitHub", "url": REPO_URL}]
    assert set(act) == {"details", "state", "timestamps", "assets", "buttons"}  # nothing else
    p.stop()
    assert not p.running and pipe.activities()[-1] is None  # cleared from the profile
    assert pipe.frames[-1][0] == 2 and pipe.closed


def test_not_running_discord_is_retried_quietly() -> None:
    attempts: list[int] = []
    pipe = FakePipe()

    def connect() -> FakePipe:
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("Discord isn't running")
        return pipe

    p = DiscordPresence(APP_ID, connect=connect, retry_s=0.01)
    p.start()
    assert wait_for(lambda: p.connected)
    assert len(attempts) == 3 and p.last_error == ""
    p.stop()


@pytest.mark.parametrize("pipe", [FakePipe(ready=False), FakePipe(reject=True)])
def test_refused_or_rejected_presence_is_not_reported_connected(pipe: FakePipe) -> None:
    p = DiscordPresence(APP_ID, connect=lambda: pipe, retry_s=10)
    p.start()
    assert wait_for(lambda: p.last_error != "")
    assert not p.connected
    p.stop()


def test_settings_default_and_validation() -> None:
    d = DiscordSettings()
    assert d.enabled is False and d.application_id == APP_ID  # opt-in
    with pytest.raises(ValidationError):
        DiscordSettings(application_id="not-an-id")

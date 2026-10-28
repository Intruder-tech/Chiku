"""Discord Rich Presence (opt-in): "Chatting with Chiku", with buttons to Chiku's GitHub page.

Talks to the Discord desktop app over its local IPC pipe (`\\\\.\\pipe\\discord-ipc-N`); Chiku
itself sends nothing over the network. Only fixed text is shown, never anything from a
conversation, memory, app names or files. If Discord isn't running, Chiku retries quietly.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import struct
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, BinaryIO

log = logging.getLogger(__name__)

REPO_URL = "https://github.com/Intruder-tech/Chiku"
RETRY_S = 30.0  # Discord not running: try again this often
REFRESH_S = 60.0  # connected: re-send now and then, which also notices Discord closing

_HANDSHAKE, _FRAME, _CLOSE = 0, 1, 2


def activity(started: int) -> dict[str, Any]:
    """The whole presence. Fixed text only: nothing about what the user is doing."""
    return {
        "details": "Chatting with Chiku",
        "state": "Local AI voice assistant",
        "timestamps": {"start": started},
        "assets": {"large_image": "chiku", "large_text": "Chiku runs on your own PC"},
        "buttons": [{"label": "Get Chiku", "url": REPO_URL},
                    {"label": "Releases", "url": f"{REPO_URL}/releases"}],
    }


def encode(op: int, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<ii", op, len(data)) + data


def read_frame(pipe: BinaryIO) -> tuple[int, dict[str, Any]]:
    header = pipe.read(8)
    if not header or len(header) < 8:
        raise ConnectionError("Discord closed the connection")
    op, length = struct.unpack("<ii", header)
    body = pipe.read(length) if length else b""
    return op, json.loads(body or b"{}")


def open_pipe() -> BinaryIO:
    """The first Discord IPC pipe that opens (Discord, PTB and Canary use 0-9)."""
    for i in range(10):
        try:
            return open(rf"\\.\pipe\discord-ipc-{i}", "r+b", buffering=0)  # noqa: SIM115
        except OSError:
            continue
    raise ConnectionError("Discord isn't running")


class DiscordPresence:
    def __init__(self, application_id: str, connect: Callable[[], BinaryIO] = open_pipe,
                 retry_s: float = RETRY_S, refresh_s: float = REFRESH_S):
        self.application_id = application_id
        self._connect = connect
        self._retry_s = retry_s
        self._refresh_s = refresh_s
        self._started = int(time.time())
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="chiku-discord")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Clear the presence and disconnect."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                pipe = self._connect()
            except (OSError, ConnectionError) as exc:
                self.last_error = str(exc)
                self._sleep(self._retry_s)
                continue
            try:
                self._session(pipe)
            except (OSError, ConnectionError, ValueError) as exc:
                self.last_error = str(exc)
                log.info("discord presence: %s", exc)
            finally:
                self.connected = False
                with contextlib.suppress(OSError):
                    pipe.close()
            if not self._stop.is_set():
                self._sleep(self._retry_s)

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _session(self, pipe: BinaryIO) -> None:
        pipe.write(encode(_HANDSHAKE, {"v": 1, "client_id": self.application_id}))
        op, ready = read_frame(pipe)
        if op != _FRAME or ready.get("evt") != "READY":
            raise ConnectionError(f"Discord refused the connection: {ready.get('message', op)}")
        self._send(pipe, activity(self._started))
        self.connected = True
        self.last_error = ""
        log.info("discord presence shown")
        while True:
            self._sleep(self._refresh_s)
            if self._stop.is_set():
                break
            self._send(pipe, activity(self._started))
        self._send(pipe, None)  # clear it before leaving
        pipe.write(encode(_CLOSE, {}))

    def _send(self, pipe: BinaryIO, act: dict[str, Any] | None) -> None:
        args: dict[str, Any] = {"pid": os.getpid()}
        if act is not None:
            args["activity"] = act
        pipe.write(encode(_FRAME, {"cmd": "SET_ACTIVITY", "args": args,
                                   "nonce": uuid.uuid4().hex}))
        _op, reply = read_frame(pipe)
        if reply.get("evt") == "ERROR":
            raise ValueError(f"Discord rejected the presence: {reply.get('data')}")

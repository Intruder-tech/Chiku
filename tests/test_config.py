import json
from datetime import datetime
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from pydantic import ValidationError

from chiku import credentials
from chiku.config import LLMSettings, Settings, is_loopback_url, load_settings, save_settings
from chiku.persona import (
    PersonaSettings,
    apply_pronunciations,
    build_system_prompt,
    time_prefix,
)


def test_defaults_are_local_and_private() -> None:
    s = Settings()
    assert is_loopback_url(s.llm.ollama_url)
    assert s.llm.allow_remote_ollama is False
    assert s.tts.voice.startswith("af_")  # female voice


def test_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    s = Settings()
    s.persona.user_name = "Sam"
    s.tts.speed = 1.1
    save_settings(s, p)
    assert load_settings(p) == s
    assert "api_key" not in p.read_text()


def test_example_config_is_valid_and_matches_defaults() -> None:
    example = Path(__file__).parent.parent / "config" / "settings.example.json"
    data = json.loads(example.read_text(encoding="utf-8"))
    loaded = Settings.model_validate(data)
    data["stt"].pop("cpu_threads")  # machine-dependent default
    assert loaded.model_dump(exclude={"stt": {"cpu_threads"}}) == \
        Settings().model_dump(exclude={"stt": {"cpu_threads"}})


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "nope.json") == Settings()


def test_corrupt_file_is_set_aside(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text("{not json")
    assert load_settings(p) == Settings()
    assert (tmp_path / "s.bad.json").exists()


def test_invalid_values_fall_back(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"tts": {"speed": 9}}))
    assert load_settings(p).tts.speed == 1.0


@pytest.mark.parametrize(("url", "local"), [
    ("http://127.0.0.1:11434", True), ("http://localhost:11434", True),
    ("http://[::1]:11434", True), ("http://192.168.0.5:11434", False),
    ("https://api.example.com", False),
])
def test_loopback_detection(url: str, local: bool) -> None:
    assert is_loopback_url(url) is local


def test_bad_ollama_url_rejected() -> None:
    with pytest.raises(ValidationError):
        LLMSettings(ollama_url="ftp://x")


def test_system_prompt_contents() -> None:
    p = PersonaSettings(user_name="Sam", response_length="brief")
    prompt = build_system_prompt(p, model_label="local · test", now=datetime(2026, 1, 2, 9, 30))
    assert "You are Chiku" in prompt and "Sam" in prompt and "local · test" in prompt
    assert "untrusted" in prompt and "No markdown" in prompt
    assert "one or two short" in prompt
    assert "2026" not in prompt  # clock is not in the (cached) system prompt
    assert time_prefix(datetime(2026, 1, 2, 9, 30)) == \
        "[Local time: Friday 02 January 2026, 09:30]\n"


def test_pronunciations() -> None:
    p = PersonaSettings(pronunciations={"GIF": "jif"})
    assert apply_pronunciations("I love a GIF", p) == "I love a jif"


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self.store:
            raise PasswordDeleteError("missing")
        del self.store[(service, username)]


def test_credentials_use_keyring() -> None:
    old = keyring.get_keyring()
    mem = MemoryKeyring()
    keyring.set_keyring(mem)
    try:
        assert not credentials.has_key("search")
        credentials.set_key("search", "  secret ")
        assert mem.store[("Chiku", "search")] == "secret"
        assert credentials.get_key("search") == "secret"
        with pytest.raises(credentials.CredentialError):
            credentials.set_key("search", "  ")
        assert credentials.delete_key("search") and not credentials.delete_key("search")
    finally:
        keyring.set_keyring(old)

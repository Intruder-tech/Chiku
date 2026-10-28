"""Typed application settings, persisted as JSON in the user's APPDATA.

Secrets never live here. API keys (for future cloud adapters or search
providers) are stored in Windows Credential Manager via `chiku.secrets`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator

from chiku.persona import PersonaSettings

log = logging.getLogger(__name__)


class AudioSettings(BaseModel):
    # Device names (not indices: indices change when devices are plugged in). None = system default.
    input_device: str | None = None
    output_device: str | None = None
    vad_threshold: float = Field(0.5, ge=0.1, le=0.95)
    min_speech_ms: int = Field(250, ge=60, le=2000)
    min_silence_ms: int = Field(700, ge=200, le=3000)
    max_utterance_s: float = Field(30.0, ge=3.0, le=120.0)
    barge_in: bool = True
    # Stricter detector while Chiku is talking, so her own voice leaking from speakers
    # is less likely to interrupt her. Headphones make barge-in far more reliable.
    barge_in_threshold: float = Field(0.85, ge=0.3, le=0.99)
    barge_in_min_ms: int = Field(350, ge=100, le=2000)


WhisperModelName = Literal["tiny.en", "base.en", "small.en", "medium.en",
                           "tiny", "base", "small", "medium"]


class STTSettings(BaseModel):
    # "*.en" models understand English only; the others are multilingual.
    model: WhisperModelName = "small.en"
    # "auto": NVIDIA GPU when the CUDA libraries are installed, otherwise CPU.
    device: Literal["cpu", "cuda", "auto"] = "auto"
    compute_type: str = "int8"  # CPU compute type; CUDA always uses float16
    # "auto" (multilingual models only): detect the language of each utterance.
    language: str = Field("en", pattern=r"^(auto|[a-z]{2,3})$")
    # The user's own languages: low-confidence detections fall back to the likeliest of these.
    preferred_languages: list[str] = Field(default_factory=lambda: ["en", "hi"], max_length=8)
    cpu_threads: int = Field(default_factory=lambda: min(16, os.cpu_count() or 4), ge=1, le=64)


class TTSSettings(BaseModel):
    engine: Literal["kokoro"] = "kokoro"
    voice: str = "af_heart"  # English voice
    # Voice per other language, e.g. {"hi": "hf_beta"}; unset languages use chiku.language's
    # defaults, and languages with no Kokoro voice are answered in text.
    voices: dict[str, str] = Field(default_factory=dict)
    speed: float = Field(1.0, ge=0.5, le=2.0)
    lang: str = "en-us"


class LLMSettings(BaseModel):
    # "ollama": local Ollama. "openai_compat": any OpenAI-style /v1/chat/completions server
    # (LM Studio, llama.cpp server, vLLM locally; or a cloud API if allow_cloud is on).
    provider: Literal["ollama", "openai_compat"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    # Non-thinking instruct build: answers immediately, which matters for voice latency.
    model: str = "qwen3:4b-instruct-2507-q4_K_M"
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    context_tokens: int = Field(8192, ge=1024, le=131072)
    request_timeout_s: float = Field(90.0, ge=5.0, le=600.0)
    keep_alive: str = "30m"
    max_history_turns: int = Field(12, ge=1, le=100)
    # Refuse to use an Ollama URL that is not on this machine unless explicitly allowed,
    # so "local mode" can never silently become a network call.
    allow_remote_ollama: bool = False
    openai_base_url: str = "http://127.0.0.1:1234/v1"  # LM Studio's default
    openai_model: str = ""
    # A non-local OpenAI-compatible endpoint (i.e. a cloud API) is refused unless this is on.
    allow_cloud: bool = False

    @field_validator("ollama_url", "openai_base_url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        u = urlparse(v)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("ollama_url must be http(s)://host:port")
        return v.rstrip("/")


KnownFolder = Literal["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]
ALL_FOLDERS: list[KnownFolder] = ["Desktop", "Documents", "Downloads", "Pictures", "Music",
                                  "Videos"]


class WebSettings(BaseModel):
    # Only the search query text is sent to the provider; never audio or chat history.
    search_enabled: bool = True
    provider: Literal["brave"] = "brave"
    max_results: int = Field(5, ge=1, le=10)
    country: str = Field("", max_length=2)  # e.g. "IN", "US"; empty = provider default
    safesearch: Literal["off", "moderate", "strict"] = "moderate"
    # Let Chiku fetch result pages to read them (requests go directly to those sites).
    allow_page_reading: bool = True


class FileSettings(BaseModel):
    enabled: bool = True
    folders: list[KnownFolder] = Field(default_factory=lambda: list(ALL_FOLDERS))
    allow_reading: bool = True  # read text files so Chiku can summarise them
    allow_changes: bool = True  # create folders; move/rename/recycle (always confirmed)


class MemorySettings(BaseModel):
    enabled: bool = True
    # Saves the model initiates (not ones you ask for with "remember ...") need your OK.
    ask_before_model_saves: bool = True
    # Memories are part of the prompt; keep them out of requests to non-local providers.
    share_with_cloud: bool = False
    max_in_prompt: int = Field(40, ge=0, le=200)


class ConversationSettings(BaseModel):
    # Off by default: chats live in memory and are gone when Chiku closes.
    keep_history: bool = False
    max_saved: int = Field(50, ge=1, le=500)


class WakeSettings(BaseModel):
    # Opt-in: while the mic is off, listen for "Hey Chiku" / "Wake up" (checked on this
    # computer, nothing kept). Closing the window then keeps Chiku running in the tray.
    enabled: bool = False
    # After waking, keep listening this long after the last thing said or answered.
    follow_up_s: int = Field(20, ge=5, le=300)


class UISettings(BaseModel):
    theme: Literal["system", "light", "dark"] = "system"
    start_compact: bool = False  # open minimised (in the mini_mode style)
    # Minimised Chiku: a notch hanging from the top edge of a screen, or a floating card.
    mini_mode: Literal["notch", "card"] = "notch"
    # Notch position per screen (screen name -> centre as a fraction of the screen's width),
    # and the screen it was last on.
    notch_positions: dict[str, float] = Field(default_factory=dict)
    notch_screen: str | None = None


class ToolSettings(BaseModel):
    disabled: list[str] = Field(default_factory=list)  # tool names switched off by the user


class PermissionSettings(BaseModel):
    auto_allow_open_apps: bool = True
    confirm_sensitive_apps: bool = True
    confirm_close_apps: bool = True
    # URLs on these domains (and subdomains) open without confirmation.
    allowed_url_domains: list[str] = Field(
        default_factory=lambda: [
            "google.com",
            "youtube.com",
            "wikipedia.org",
            "github.com",
            "microsoft.com",
            "bing.com",
        ]
    )


class Settings(BaseModel):
    version: int = 1
    onboarding_done: bool = False  # first-run setup (hardware check, models) completed
    audio: AudioSettings = Field(default_factory=AudioSettings)
    stt: STTSettings = Field(default_factory=STTSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    persona: PersonaSettings = Field(default_factory=PersonaSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    files: FileSettings = Field(default_factory=FileSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    ui: UISettings = Field(default_factory=UISettings)
    wake: WakeSettings = Field(default_factory=WakeSettings)
    conversations: ConversationSettings = Field(default_factory=ConversationSettings)


def is_loopback_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def load_settings(path: Path) -> Settings:
    """Load settings; a missing or corrupt file yields defaults (corrupt file is kept as .bad)."""
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Settings.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        log.warning("settings file unreadable (%s); using defaults", type(exc).__name__)
        with contextlib.suppress(OSError):
            path.replace(path.with_suffix(".bad.json"))
        return Settings()


def save_settings(settings: Settings, path: Path) -> None:
    """Atomic write: temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump_json(indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

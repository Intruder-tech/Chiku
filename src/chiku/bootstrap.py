"""Wires real components together from settings."""

from __future__ import annotations

from collections.abc import Callable

from chiku import credentials
from chiku.assistant import Assistant, Event
from chiku.audio.capture import MicCapture
from chiku.audio.playback import AudioPlayer
from chiku.audio.vad import EnergyVAD, SileroVAD, SpeechDetector
from chiku.audit import AuditLog
from chiku.config import LLMSettings, PermissionSettings, Settings, STTSettings
from chiku.llm.base import LLMError, LLMProvider, ProviderStatus
from chiku.llm.ollama import OllamaProvider
from chiku.llm.openai_compat import OPENAI_KEY_NAME, OpenAICompatProvider
from chiku.memory import MemoryStore
from chiku.models import SILERO_VAD
from chiku.paths import AppPaths
from chiku.stt.whisper import WhisperSTT
from chiku.tools.apps import AppCatalog, FindAppTool, OpenAppTool
from chiku.tools.files import FileScope, file_tools
from chiku.tools.memory_tools import memory_tools
from chiku.tools.registry import ToolExecutor, ToolRegistry
from chiku.tools.research import ReadWebPageTool, SourceSession, WebSearchTool
from chiku.tools.system_info import SystemInfoTool
from chiku.tools.web import OpenUrlTool
from chiku.tools.windows_ctl import Win32Backend, WindowBackend, window_tools
from chiku.tts.kokoro import KokoroTTS
from chiku.web.search import BRAVE_KEY_NAME, BraveSearch, SearchProvider


class UnavailableProvider(LLMProvider):
    """Stands in when the configured provider can't be constructed (e.g. refused remote URL)."""

    is_local = True

    def __init__(self, reason: str):
        self.reason = reason
        self.label = "no language model"

    def status(self) -> ProviderStatus:
        return ProviderStatus(False, self.reason)

    def stream_chat(self, messages, tools, cancel):  # noqa: ANN001
        raise LLMError(self.reason)


def make_llm(settings: LLMSettings) -> LLMProvider:
    try:
        if settings.provider == "openai_compat":
            return OpenAICompatProvider(settings, lambda: credentials.get_key(OPENAI_KEY_NAME))
        return OllamaProvider(settings)
    except LLMError as exc:
        return UnavailableProvider(str(exc))


def make_search_provider(settings_getter: Callable[[], Settings]) -> BraveSearch:
    return BraveSearch(lambda: credentials.get_key(BRAVE_KEY_NAME),
                       lambda: settings_getter().web)


def build_registry(
    settings_getter: Callable[[], Settings],
    catalog: AppCatalog,
    search: SearchProvider | None = None,
    windows: WindowBackend | None = None,
    memory: MemoryStore | None = None,
    user_text: Callable[[], str] = lambda: "",
) -> ToolRegistry:
    reg = ToolRegistry()

    def perms() -> PermissionSettings:
        return settings_getter().permissions

    reg.register(OpenAppTool(catalog, perms))
    reg.register(FindAppTool(catalog))
    reg.register(OpenUrlTool(perms))
    reg.register(SystemInfoTool())

    session = SourceSession()
    reg.register(WebSearchTool(search or make_search_provider(settings_getter),
                               lambda: settings_getter().web, session))
    reg.register(ReadWebPageTool(lambda: settings_getter().web, session))

    scopes: dict[tuple[str, ...], FileScope] = {}

    def scope() -> FileScope:
        key = tuple(settings_getter().files.folders)
        if key not in scopes:
            scopes.clear()
            scopes[key] = FileScope.for_folders(list(key))
        return scopes[key]

    for tool in file_tools(scope, lambda: settings_getter().files):
        reg.register(tool)
    for tool in window_tools(windows or Win32Backend(), catalog, perms):
        reg.register(tool)
    if memory is not None:
        for tool in memory_tools(memory, lambda: settings_getter().memory, user_text):
            reg.register(tool)
    return reg


def build_assistant(
    settings: Settings,
    paths: AppPaths,
    on_event: Callable[[Event], None] | None = None,
) -> Assistant:
    paths.ensure()
    catalog = AppCatalog()
    holder: dict[str, Assistant] = {}

    def current_settings() -> Settings:
        return holder["a"].settings if "a" in holder else settings

    memory = MemoryStore(paths.memory_file)
    registry = build_registry(
        current_settings, catalog, memory=memory,
        user_text=lambda: holder["a"].current_user_text() if "a" in holder else "",
    )
    executor = ToolExecutor(registry, audit=AuditLog(paths.audit_file),
                            disabled=lambda: current_settings().tools.disabled)

    def make_vad() -> SpeechDetector:
        path = paths.models_dir / SILERO_VAD.relpath
        return SileroVAD(path) if path.exists() else EnergyVAD()

    def make_stt(s: STTSettings) -> WhisperSTT:
        return WhisperSTT(s, paths.models_dir)

    assistant = Assistant(
        settings,
        make_stt=make_stt,
        tts=KokoroTTS(settings.tts, paths.models_dir),
        make_llm=make_llm,
        make_vad=make_vad,
        make_capture=MicCapture,
        player=AudioPlayer(settings.audio.output_device),
        registry=registry,
        executor=executor,
        catalog=catalog,
        memory=memory,
        on_event=on_event,
    )
    holder["a"] = assistant
    return assistant

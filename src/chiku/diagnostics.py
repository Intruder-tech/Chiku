"""Command-line diagnostics and manual hardware tests (python -m chiku --check, --say, ...)."""

from __future__ import annotations

import sys
import threading
import time

import numpy as np

from chiku import models
from chiku.config import Settings
from chiku.paths import AppPaths, cli_command


def _p(line: str = "") -> None:
    print(line, flush=True)


class NullPlayer:
    """Discards audio; used by --chat --mute."""

    level = 0.0
    device_name = None
    is_playing = False

    def play(self, audio: np.ndarray) -> None:
        pass

    def stop(self) -> None:
        pass

    def wait_until_drained(self, cancel) -> bool:  # noqa: ANN001
        return True

    def close_if_idle(self, idle_s: float = 20.0) -> None:
        pass

    def close(self) -> None:
        pass


def download_models(settings: Settings, paths: AppPaths, assume_yes: bool) -> int:
    _p(models.plan_text(paths.models_dir, settings.stt.model))
    if not models.missing(paths.models_dir, settings.stt.model):
        _p("All models present.")
        return 0
    if not assume_yes:
        answer = input("Download the missing models now? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            _p("Nothing downloaded.")
            return 1
    for f in models.FILES:
        models.download_file(f, paths.models_dir, _p)
    models.download_whisper(settings.stt.model, paths.models_dir, _p)
    _p("Done.")
    return 0


def check(settings: Settings, paths: AppPaths) -> int:
    from chiku import hardware
    from chiku.audio import devices
    from chiku.audio.capture import resample
    from chiku.bootstrap import make_llm
    from chiku.stt.whisper import WhisperSTT
    from chiku.tts.kokoro import KokoroTTS

    problems = 0
    _p("== Hardware")
    hw = hardware.summary(paths.models_dir)
    for k, v in hw.items():
        _p(f"  {k}: {v}")
    _p(f"  recommendation: "
       f"{hardware.recommendation(hardware.gpu_info(), float(str(hw['ram_total_gb'])))}")

    _p("== Audio devices")
    _p(f"  default input : {devices.default_device_name('input')}")
    _p(f"  default output: {devices.default_device_name('output')}")
    ins = devices.list_devices("input")
    _p(f"  inputs ({len(ins)}): " + "; ".join(d.name for d in ins[:8]))
    if not ins:
        problems += 1
        _p("  !! no microphone found")

    _p("== Models")
    _p(models.plan_text(paths.models_dir, settings.stt.model))
    missing = models.missing(paths.models_dir, settings.stt.model)
    if missing:
        problems += 1
        _p(f"  !! missing: {', '.join(missing)} (run: {cli_command()} --download-models)")

    _p("== Language model")
    llm = make_llm(settings.llm)
    st = llm.status()
    _p(f"  {llm.label}: {'OK' if st.available else 'UNAVAILABLE'} - {st.message}")
    if not st.available:
        problems += 1

    _p("== Web search (not contacted by --check)")
    from chiku import credentials
    from chiku.web.search import BRAVE_KEY_NAME

    try:
        has_key = credentials.has_key(BRAVE_KEY_NAME)
    except credentials.CredentialError as exc:
        has_key = False
        _p(f"  !! credential store: {exc}")
    state = "off" if not settings.web.search_enabled else ("ready" if has_key else "no API key")
    _p(f"  Brave Search: {state}" + ("" if has_key else
                                     " (set one in Settings → Web or: "
                                     f"{cli_command()} --set-search-key)"))

    _p("== Files")
    from chiku.tools.files import FileScope

    if settings.files.enabled:
        for name, root in FileScope.for_folders(list(settings.files.folders)).roots.items():
            _p(f"  {name:<9} {root} {'' if root.is_dir() else '!! missing'}")
    else:
        _p("  file access is turned off")

    if not missing:
        _p("== Voice -> speech recognition round trip")
        tts = KokoroTTS(settings.tts, paths.models_dir)
        t0 = time.perf_counter()
        tts.load()
        tts.synthesize("Hi.")
        _p(f"  voice loaded in {time.perf_counter() - t0:.1f}s ({settings.tts.voice})")
        phrase = "Chiku, please open the calculator."
        t0 = time.perf_counter()
        audio = tts.synthesize(phrase)
        synth_s = time.perf_counter() - t0
        _p(f"  synthesized {len(audio) / tts.sample_rate:.2f}s of audio in {synth_s:.2f}s")
        stt = WhisperSTT(settings.stt, paths.models_dir)
        t0 = time.perf_counter()
        stt.load()
        _p(f"  speech recognition loaded in {time.perf_counter() - t0:.1f}s on {stt.device_used}")
        tr = stt.transcribe(resample(audio, tts.sample_rate, 16000))
        _p(f"  heard: {tr.text!r} in {tr.elapsed_s:.2f}s")
        if "calculator" not in tr.text.lower():
            problems += 1
            _p("  !! round trip did not match")
    _p()
    _p("All checks passed." if problems == 0 else f"{problems} problem(s) found.")
    return 0 if problems == 0 else 1


def say(settings: Settings, paths: AppPaths, text: str) -> int:
    from chiku.audio.playback import AudioPlayer
    from chiku.cancel import CancelToken
    from chiku.tts.kokoro import KokoroTTS

    tts = KokoroTTS(settings.tts, paths.models_dir)
    audio = tts.synthesize(text)
    player = AudioPlayer(settings.audio.output_device)
    _p(f"Playing {len(audio) / tts.sample_rate:.1f}s with voice {settings.tts.voice} ...")
    player.play(audio)
    player.wait_until_drained(CancelToken())
    time.sleep(0.2)
    player.close()
    return 0


def mic_test(settings: Settings, paths: AppPaths, seconds: float) -> int:
    from chiku.audio.capture import MicCapture
    from chiku.audio.vad import SileroVAD, SpeechEnded, UtteranceSegmenter
    from chiku.stt.whisper import WhisperSTT

    a = settings.audio
    vad = SileroVAD(paths.models_dir / models.SILERO_VAD.relpath)
    seg = UtteranceSegmenter(a.vad_threshold, a.min_speech_ms, a.min_silence_ms, a.max_utterance_s)
    stt = WhisperSTT(settings.stt, paths.models_dir)
    stt.load()
    cap = MicCapture(a.input_device)
    cap.start()
    _p(f"Recording {seconds:.0f}s - speak now ...")
    end = time.monotonic() + seconds
    peak, frames, speech_frames, utterances = 0.0, 0, 0, []
    while time.monotonic() < end:
        f = cap.read(0.5)
        if f is None:
            continue
        frames += 1
        peak = max(peak, float(np.abs(f).max()))
        p = vad(f)
        speech_frames += p >= a.vad_threshold
        for ev in seg.process(f, p):
            if isinstance(ev, SpeechEnded):
                utterances.append(ev.audio)
    cap.stop()
    _p(f"frames={frames} peak={peak:.3f} speech_frames={speech_frames} "
       f"dropped={cap.dropped} overflows={cap.overflows}")
    if frames == 0:
        _p("!! no audio received from the microphone")
        return 1
    for u in utterances:
        tr = stt.transcribe(u)
        _p(f"utterance {len(u) / 16000:.1f}s -> {tr.text!r} ({tr.elapsed_s:.2f}s)")
    if not utterances:
        _p("No complete utterance detected (try speaking, then pausing for a second).")
    return 0


def chat(settings: Settings, paths: AppPaths, text: str, mute: bool) -> int:
    from chiku.assistant import ComponentStatus, Message, State, StateChanged, ToolActivity
    from chiku.bootstrap import build_assistant

    done = threading.Event()
    seen_reply = threading.Event()

    def on_event(ev) -> None:  # noqa: ANN001
        if isinstance(ev, Message) and ev.final and ev.role != "user":
            _p(f"[{ev.role} · {ev.source}] {ev.text}")
            if ev.role == "assistant":
                seen_reply.set()
        elif isinstance(ev, Message) and ev.role == "user":
            _p(f"[you] {ev.text}")
        elif isinstance(ev, ToolActivity):
            _p(f"  (tool {ev.name}: {ev.status} - {ev.message})")
        elif isinstance(ev, ComponentStatus) and not ev.ok:
            _p(f"  !! {ev.component}: {ev.message}")
        elif isinstance(ev, StateChanged) and ev.state in (State.IDLE, State.LISTENING) \
                and seen_reply.is_set():
            done.set()

    a = build_assistant(settings, paths, on_event)
    a.confirm_handler = lambda _n, desc: input(f"Allow: {desc}? [y/N] ").strip().lower() == "y"
    if mute:
        a._player = NullPlayer()  # type: ignore[assignment]
    a.start()
    a.submit_text(text)
    ok = done.wait(timeout=180)
    a.shutdown()
    return 0 if ok else 1


def list_apps(query: str) -> int:
    from chiku.tools.apps import AppCatalog

    cat = AppCatalog()
    if query:
        for e, s in cat.search(query, limit=10):
            _p(f"{s:.2f}  {e.name}  [{e.kind}] {e.exe_name}")
    else:
        for e in sorted(cat.entries(), key=lambda e: e.name.lower()):
            _p(f"{e.name}  [{e.kind}] {e.exe_name}")
    return 0


def open_app(settings: Settings, paths: AppPaths, name: str) -> int:
    from chiku.audit import AuditLog
    from chiku.bootstrap import build_registry
    from chiku.cancel import CancelToken
    from chiku.tools.apps import AppCatalog
    from chiku.tools.registry import ToolExecutor

    reg = build_registry(lambda: settings, AppCatalog())
    ex = ToolExecutor(
        reg,
        confirm=lambda _n, d: input(f"Allow: {d}? [y/N] ").strip().lower() == "y",
        audit=AuditLog(paths.audit_file),
    )
    t0 = time.perf_counter()
    res = ex.execute("open_app", {"app_name": name}, CancelToken())
    _p(f"ok={res.ok} code={res.error_code} message={res.message!r} data={res.data} "
       f"({time.perf_counter() - t0:.1f}s)")
    ex.shutdown()
    return 0 if res.ok else 1


def set_search_key() -> int:
    import getpass

    from chiku import credentials
    from chiku.web.search import BRAVE_KEY_NAME

    key = getpass.getpass("Brave Search API key (input hidden, empty = remove): ").strip()
    if not key:
        removed = credentials.delete_key(BRAVE_KEY_NAME)
        _p("Key removed." if removed else "No key was stored.")
        return 0
    credentials.set_key(BRAVE_KEY_NAME, key)
    _p("Stored in Windows Credential Manager (service 'Chiku').")
    return 0


def run_tool(settings: Settings, paths: AppPaths, name: str, args: dict) -> int:
    """Run one tool through the normal permissioned, audited executor and print the result."""
    import json

    from chiku.audit import AuditLog
    from chiku.bootstrap import build_registry
    from chiku.cancel import CancelToken
    from chiku.tools.apps import AppCatalog
    from chiku.tools.registry import ToolExecutor

    reg = build_registry(lambda: settings, AppCatalog())
    ex = ToolExecutor(
        reg,
        confirm=lambda _n, d: input(f"Allow: {d}? [y/N] ").strip().lower() == "y",
        audit=AuditLog(paths.audit_file),
    )
    t0 = time.perf_counter()
    res = ex.execute(name, args, CancelToken())
    ex.shutdown()
    _p(f"ok={res.ok} code={res.error_code} ({time.perf_counter() - t0:.2f}s)")
    _p(res.message)
    if res.data:
        _p(json.dumps(res.data, indent=2, ensure_ascii=False)[:4000])
    return 0 if res.ok else 1


def show_memory(paths: AppPaths) -> int:
    from chiku.memory import MemoryStore

    store = MemoryStore(paths.memory_file)
    items = store.all()
    _p(f"{len(items)} memory item(s) in {paths.memory_file}")
    for it in items:
        _p(f"  [{it.id}] ({it.category}, {it.source}, {it.updated}) {it.text}")
    store.close()
    return 0


def show_models(settings: Settings) -> int:
    from chiku.llm.manage import ModelError, OllamaManager

    mgr = OllamaManager(settings.llm.ollama_url)
    try:
        loaded = mgr.loaded()
        for m in mgr.installed():
            mark = "*" if m.name == settings.llm.model else " "
            caps = ",".join(mgr.capabilities(m.name))
            vram = f" loaded {loaded[m.name]:.1f} GB VRAM" if m.name in loaded else ""
            _p(f"{mark} {m.name:<36} {m.size_gb:>5.1f} GB  {m.parameter_size:<6} "
               f"{m.quantization:<8} [{caps}]{vram}")
    except ModelError as exc:
        _p(str(exc))
        return 1
    finally:
        mgr.close()
    _p(f"provider: {settings.llm.provider}  (* = model in use)")
    return 0


def benchmark(settings: Settings, paths: AppPaths, runs: int) -> int:
    from chiku.benchmark import run_benchmark, summarize, write_report

    report = run_benchmark(settings, paths, runs=runs, progress=lambda m: _p(f"… {m}"))
    j, m = write_report(report, paths.logs_dir)
    _p(summarize(report))
    _p(f"Saved: {m}\n       {j}")
    return 0


def version_info() -> int:
    """Load every native dependency (used as the packaged-app smoke test)."""
    import importlib

    from chiku import __version__

    _p(f"Chiku {__version__} (Python {sys.version.split()[0]}, frozen="
       f"{getattr(sys, 'frozen', False)})")
    failures = 0
    for mod in ("numpy", "onnxruntime", "ctranslate2", "faster_whisper", "kokoro_onnx",
                "espeakng_loader", "phonemizer", "sounddevice", "httpx", "pydantic",
                "keyring", "psutil", "PySide6.QtWidgets"):
        try:
            m = importlib.import_module(mod)
            _p(f"  ok   {mod} {getattr(m, '__version__', '')}")
        except Exception as exc:
            failures += 1
            _p(f"  FAIL {mod}: {type(exc).__name__}: {exc}")
    try:
        from pathlib import Path

        import espeakng_loader

        lib, data = Path(espeakng_loader.get_library_path()), Path(espeakng_loader.get_data_path())
        ok = lib.exists() and data.is_dir()
        _p(f"  {'ok  ' if ok else 'FAIL'} espeak-ng library+data")
        failures += 0 if ok else 1
        import sounddevice as sd

        _p(f"  ok   audio devices: {len(sd.query_devices())}")
        from chiku.branding import ICON, asset

        ok = asset(ICON).exists()
        _p(f"  {'ok  ' if ok else 'FAIL'} brand assets ({asset(ICON).parent})")
        failures += 0 if ok else 1
    except Exception as exc:
        failures += 1
        _p(f"  FAIL runtime check: {exc}")
    return 1 if failures else 0


def stdout_available() -> bool:
    return sys.stdout is not None

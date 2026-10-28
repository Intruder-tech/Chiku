"""Reproducible performance benchmark (`python -m chiku --benchmark`).

Measures on the user's own machine, with the configured models:
startup per component, speech recognition latency + word error rate, voice synthesis speed,
LLM time-to-first-token, full voice turns (spoken question -> first audio), cancellation
latency, and CPU / GPU / memory use. Speech input is synthesized (several Kokoro voices),
so accuracy numbers describe clean synthetic speech, not real microphones; the report says so.
Nothing is sent anywhere; results go to %LOCALAPPDATA%\\Chiku\\logs\\benchmark-*.{json,md}.
"""

from __future__ import annotations

import json
import platform
import re
import statistics
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from chiku import __version__, hardware, models
from chiku.audio.capture import resample
from chiku.cancel import CancelToken
from chiku.config import Settings
from chiku.paths import AppPaths, cli_command

STT_SAMPLES = [
    ("af_heart", "Hey Chiku, can you open Google Chrome for me please?"),
    ("am_michael", "What is the weather going to be like in Mumbai tomorrow afternoon?"),
    ("bf_emma", "Remember that my sister's birthday is on the fourteenth of March."),
]
E2E_QUESTIONS = [
    "Hey Chiku, what is two plus two?",
    "Tell me one fun fact about octopuses.",
    "How many days are there in a leap year?",
]
LLM_PROMPTS = [
    "In one sentence, what is a GPU?",
    "Say hello in a friendly way.",
    "Give me one tip for better sleep.",
]


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate (Levenshtein over normalised words)."""
    def norm(s: str) -> list[str]:
        return re.sub(r"[^a-z0-9' ]", " ", s.lower()).split()

    r, h = norm(reference), norm(hypothesis)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 3) if xs else float("nan")


class _Sampler:
    """Samples system CPU %, process RSS and NVIDIA GPU utilisation in the background."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.cpu: list[float] = []
        self.gpu: list[float] = []
        self.rss_peak = 0
        self._stop = threading.Event()
        self._proc = psutil.Process()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _Sampler:
        psutil.cpu_percent(None)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.cpu.append(psutil.cpu_percent(None))
            self.rss_peak = max(self.rss_peak, self._proc.memory_info().rss)
            g = _gpu_util()
            if g is not None:
                self.gpu.append(g)


def _gpu_util() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()
        return float(out[0]) if out else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class _TimingPlayer:
    """Player stand-in: records when audio would start, plays nothing (benchmark is silent)."""

    level = 0.0
    device_name: str | None = None
    is_playing = False

    def __init__(self) -> None:
        self.first_play: float | None = None

    def play(self, audio: np.ndarray) -> None:
        if self.first_play is None:
            self.first_play = time.monotonic()

    def stop(self) -> None:
        pass

    def wait_until_drained(self, cancel: CancelToken) -> bool:
        return True

    def close_if_idle(self, idle_s: float = 20.0) -> None:
        pass

    def close(self) -> None:
        pass


class _Feed:
    """Microphone stand-in: delivers a recording frame by frame at real-time pace."""

    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.level = 0.0
        self._active = False

    def load(self, audio16k: np.ndarray) -> None:
        pad = np.zeros(16000, dtype=np.float32)
        x = np.concatenate([pad, audio16k.astype(np.float32), pad, pad])
        self.frames = [x[i:i + 512] for i in range(0, len(x) - 512, 512)]

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        time.sleep(0.032)
        return self.frames.pop(0) if self.frames else np.zeros(512, dtype=np.float32)


def _environment() -> dict[str, Any]:
    """Conditions that strongly affect timings (other apps, memory pressure, power)."""
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    batt = psutil.sensors_battery()
    return {
        "ram_available_gb": round(vm.available / 2**30, 1),
        "pagefile_used_gb": round(sw.used / 2**30, 1),
        "on_battery": bool(batt and not batt.power_plugged),
        "cpu_busy_percent_before": psutil.cpu_percent(interval=1.0),
    }


def run_benchmark(settings: Settings, paths: AppPaths, runs: int = 3,
                  progress: Callable[[str], None] = print) -> dict[str, Any]:
    from chiku.assistant import Assistant, Message, TurnMetrics
    from chiku.audio.vad import EnergyVAD, SileroVAD
    from chiku.bootstrap import build_registry, make_llm
    from chiku.llm.base import ChatMessage
    from chiku.stt.whisper import WhisperSTT
    from chiku.tools.apps import AppCatalog
    from chiku.tools.registry import ToolExecutor
    from chiku.tts.kokoro import KokoroTTS

    report: dict[str, Any] = {
        "chiku_version": __version__,
        "date": datetime.now().isoformat(timespec="seconds"),
        "method": "Synthetic speech from Kokoro voices is used as microphone input; audio "
                  "output is timed but not played. Medians over runs.",
        "hardware": {**hardware.summary(paths.models_dir), "os": platform.platform(),
                     "python": platform.python_version()},
        "environment": _environment(),
        "config": {"stt_model": settings.stt.model, "stt_device": settings.stt.device,
                   "tts_voice": settings.tts.voice, "llm": settings.llm.model,
                   "llm_provider": settings.llm.provider,
                   "end_of_turn_pause_ms": settings.audio.min_silence_ms},
    }
    if models.missing(paths.models_dir, settings.stt.model):
        raise RuntimeError("Models are missing; open Chiku's setup or run "
                           f"{cli_command()} --download-models.")

    # -- startup, per component ---------------------------------------------------------
    progress("startup: loading components…")
    startup: dict[str, float] = {}
    t = time.perf_counter()
    vad_path = paths.models_dir / models.SILERO_VAD.relpath
    SileroVAD(vad_path)
    startup["vad_s"] = round(time.perf_counter() - t, 3)
    t = time.perf_counter()
    tts = KokoroTTS(settings.tts, paths.models_dir)
    tts.load()
    tts.synthesize("Hi.")
    startup["tts_load_and_warm_s"] = round(time.perf_counter() - t, 3)
    t = time.perf_counter()
    stt = WhisperSTT(settings.stt, paths.models_dir)
    stt.load()
    startup["stt_load_s"] = round(time.perf_counter() - t, 3)
    startup["stt_device"] = stt.device_used  # type: ignore[assignment]
    t = time.perf_counter()
    llm = make_llm(settings.llm)
    st = llm.status()
    if st.available:
        llm.warm_up()
    startup["llm_check_and_warm_s"] = round(time.perf_counter() - t, 3)
    report["startup"] = startup
    if not st.available:
        report["llm_unavailable"] = st.message

    # -- speech recognition --------------------------------------------------------------
    progress("speech recognition…")
    stt_rows = []
    for voice, text in STT_SAMPLES:
        audio = resample(tts.synthesize(text, voice=voice), tts.sample_rate, 16000)
        times, hyp = [], ""
        for _ in range(runs):
            tr = stt.transcribe(audio)
            times.append(tr.elapsed_s)
            hyp = tr.text
        stt_rows.append({"voice": voice, "audio_s": round(len(audio) / 16000, 2),
                         "latency_s": _median(times), "wer": round(wer(text, hyp), 3),
                         "heard": hyp})
    report["stt"] = stt_rows

    # -- voice synthesis -------------------------------------------------------------------
    progress("voice synthesis…")
    tts_rows = {}
    for label, text in (("short", "Sure, opening Chrome for you."),
                        ("long", "Octopuses have three hearts, blue blood, and each of their "
                                 "eight arms can taste what it touches.")):
        times, dur = [], 0.0
        for _ in range(runs):
            t = time.perf_counter()
            wav = tts.synthesize(text)
            times.append(time.perf_counter() - t)
            dur = len(wav) / tts.sample_rate
        tts_rows[label] = {"synth_s": _median(times), "audio_s": round(dur, 2),
                           "rtf": round(_median(times) / dur, 3)}
    report["tts"] = tts_rows

    # -- language model --------------------------------------------------------------------
    llm_rows = []
    if st.available:
        progress("language model…")
        for prompt in LLM_PROMPTS:
            firsts, rates = [], []
            for _ in range(runs):
                t0 = time.perf_counter()
                first = None
                chars = 0
                for ch in llm.stream_chat([ChatMessage("user", prompt)], None, CancelToken()):
                    if ch.text and first is None:
                        first = time.perf_counter() - t0
                    chars += len(ch.text)
                total = time.perf_counter() - t0
                if first is not None:
                    firsts.append(first)
                    if total > first:
                        rates.append(chars / (total - first))
            llm_rows.append({"prompt": prompt, "first_token_s": _median(firsts),
                             "chars_per_s": round(statistics.median(rates), 1) if rates else 0})
    report["llm"] = llm_rows

    # -- full voice turns -----------------------------------------------------------------
    e2e_rows: list[dict[str, Any]] = []
    cancel_ms: list[float] = []
    if st.available:
        progress("full voice turns…")
        feed = _Feed()
        player = _TimingPlayer()
        events: list[Any] = []
        s = settings.model_copy(deep=True)
        reg = build_registry(lambda: s, AppCatalog(loader=list))
        t_engine = time.perf_counter()
        a = Assistant(s, make_stt=lambda _s: stt, tts=tts, make_llm=lambda _s: llm,
                      make_vad=lambda: SileroVAD(vad_path) if vad_path.exists() else EnergyVAD(),
                      make_capture=lambda _n: feed, player=player, registry=reg,
                      executor=ToolExecutor(reg), on_event=events.append)
        a.start()
        a._ready.wait(120)
        report["startup"]["engine_ready_with_preloaded_models_s"] = round(
            time.perf_counter() - t_engine, 3)
        with _Sampler() as sampler:
            for q in E2E_QUESTIONS[:max(1, runs)]:
                n = len(events)
                feed.load(resample(tts.synthesize(q), tts.sample_rate, 16000))
                a.set_mic(True)
                end = time.monotonic() + 90
                while time.monotonic() < end and not any(
                        isinstance(e, TurnMetrics) for e in events[n:]):
                    time.sleep(0.05)
                a.set_mic(False)
                m: dict[str, Any] = next(
                    (dict(e.values) for e in events[n:] if isinstance(e, TurnMetrics)), {})
                reply = next((e.text for e in events[n:] if isinstance(e, Message)
                              and e.role == "assistant" and e.final), "")
                if m:
                    m["perceived_s"] = round(m.get("end_of_speech_to_audio_s", 0)
                                             + s.audio.min_silence_ms / 1000, 3)
                    m["question"] = q
                    m["reply"] = reply[:120]
                    e2e_rows.append(m)
            progress("cancellation…")
            for _ in range(max(3, runs)):
                n = len(events)
                a.submit_text("Tell me a long, detailed story about a dragon who learns to code.")
                end = time.monotonic() + 30
                while time.monotonic() < end and not any(
                        isinstance(e, Message) and e.role == "assistant" and e.text
                        and not e.final for e in events[n:]):
                    time.sleep(0.005)
                time.sleep(0.3)
                t = time.perf_counter()
                a.interrupt("benchmark")
                while a._current is not None and time.perf_counter() - t < 10:
                    time.sleep(0.001)
                cancel_ms.append(round((time.perf_counter() - t) * 1000, 1))
        a.shutdown()
        report["resources"] = {
            "process_rss_peak_mb": round(sampler.rss_peak / 2**20),
            "system_cpu_percent_median": _median(sampler.cpu),
            "gpu_util_percent_median": _median(sampler.gpu) if sampler.gpu else None,
            "gpu_memory": hardware.gpu_info(),
        }
    report["e2e"] = e2e_rows
    report["cancel_ms"] = cancel_ms
    return report


def summarize(r: dict[str, Any]) -> str:
    hw = r["hardware"]
    gpus = ", ".join(str(g.get("name")) for g in hw.get("gpus") or []) or "none detected"
    lines = [
        f"# Chiku benchmark {r['date']} (v{r['chiku_version']})", "",
        f"- CPU: {hw.get('cpu')} ({hw.get('logical_cores')} threads), RAM {hw.get('ram_total_gb')}"
        f" GB, GPU: {gpus}", f"- OS: {hw.get('os')}, Python {hw.get('python')}",
        f"- Config: {json.dumps(r['config'])}",
        f"- Conditions: {json.dumps(r.get('environment', {}))}",
        f"- Method: {r['method']}", "",
        "## Startup", *(f"- {k}: {v}" for k, v in r["startup"].items()), "",
        "## Speech recognition", "| voice | audio s | latency s | WER | heard |",
        "|---|---|---|---|---|",
        *(f"| {x['voice']} | {x['audio_s']} | {x['latency_s']} | {x['wer']} | {x['heard']} |"
          for x in r["stt"]), "",
        "## Voice synthesis", *(f"- {k}: {v}" for k, v in r["tts"].items()), "",
        "## Language model", *(f"- {x['prompt']!r}: first token {x['first_token_s']} s, "
                               f"{x['chars_per_s']} chars/s" for x in r["llm"]), "",
        "## Full voice turns",
    ]
    for x in r["e2e"]:
        lines.append(f"- {x['question']!r}: STT {x.get('stt_s')} s, LLM first token "
                     f"{x.get('llm_first_token_s')} s, end of speech → audio "
                     f"{x.get('end_of_speech_to_audio_s')} s (perceived incl. pause "
                     f"{x.get('perceived_s')} s)")
    if r["e2e"]:
        vals = [x["end_of_speech_to_audio_s"] for x in r["e2e"] if "end_of_speech_to_audio_s" in x]
        lines.append(f"- **median end of speech → audio: {_median(vals)} s**")
    lines += ["", f"## Cancellation (ms): {r['cancel_ms']}"]
    if "resources" in r:
        lines += ["", "## Resources", *(f"- {k}: {v}" for k, v in r["resources"].items())]
    return "\n".join(lines) + "\n"


def write_report(r: dict[str, Any], logs_dir: Path) -> tuple[Path, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    j = logs_dir / f"benchmark-{stamp}.json"
    m = logs_dir / f"benchmark-{stamp}.md"
    j.write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
    m.write_text(summarize(r), encoding="utf-8")
    return j, m

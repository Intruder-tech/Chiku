"""Hardware detection used for diagnostics and model recommendations."""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys
from pathlib import Path

import psutil


@functools.cache
def cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    import platform

    return platform.processor() or "unknown CPU"


def gpu_info() -> list[dict[str, object]]:
    """NVIDIA GPUs via nvidia-smi (empty list if unavailable)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.used,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            gpus.append({
                "name": parts[0],
                "vram_total_mb": int(float(parts[1])),
                "vram_used_mb": int(float(parts[2])),
                "driver": parts[3],
            })
    return gpus


def summary(models_dir: Path) -> dict[str, object]:
    vm = psutil.virtual_memory()
    anchor = models_dir if models_dir.exists() else Path(models_dir.anchor)
    return {
        "cpu": cpu_name(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(),
        "ram_total_gb": round(vm.total / 2**30, 1),
        "ram_available_gb": round(vm.available / 2**30, 1),
        "gpus": gpu_info(),
        "models_drive_free_gb": round(shutil.disk_usage(anchor).free / 2**30, 1),
    }


def display_adapters() -> list[str]:
    """Names of all graphics adapters (any vendor) via WMI; empty if unavailable."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


LLM_SIZES_GB = {"qwen3:4b-instruct-2507-q4_K_M": 2.5, "llama3.2:3b": 2.0, "llama3.2:1b": 1.3}


def recommend_setup(nvidia: list[dict[str, object]], ram_gb: float, cores: int,
                    cuda_ready: bool) -> dict[str, object]:
    """Concrete model choices for this PC. Works without any NVIDIA GPU."""
    vram = max((int(str(g.get("vram_total_mb", 0))) for g in nvidia), default=0)
    notes: list[str] = []
    if vram >= 2000:
        stt_model, stt_device = "small.en", "auto"
        if not cuda_ready:
            notes.append("Fast speech recognition on your NVIDIA GPU needs the optional NVIDIA "
                         "speech pack (~1.2 GB). Without it, speech recognition runs on the CPU.")
    elif ram_gb >= 12 and cores >= 8:
        stt_model, stt_device = "base.en", "cpu"
        notes.append("No NVIDIA GPU found: speech recognition runs on the CPU with the smaller "
                     "base.en model to keep replies quick.")
    else:
        stt_model, stt_device = "tiny.en", "cpu"
        notes.append("Modest hardware: using the smallest speech model (tiny.en).")
    if vram >= 6000:
        llm = "qwen3:4b-instruct-2507-q4_K_M"
    elif vram >= 4000 or ram_gb >= 16:
        llm = "qwen3:4b-instruct-2507-q4_K_M" if ram_gb >= 16 else "llama3.2:3b"
        if vram < 4000:
            notes.append("The language model will run on the CPU; expect a few seconds per "
                         "reply. Ollama can also use many AMD GPUs.")
    elif ram_gb >= 8:
        llm = "llama3.2:3b"
        notes.append("Using a smaller 3B language model to fit your memory.")
    else:
        llm = "llama3.2:1b"
        notes.append("Using the smallest 1B language model; answers will be simpler.")
    return {"stt_model": stt_model, "stt_device": stt_device, "llm_model": llm,
            "llm_size_gb": LLM_SIZES_GB.get(llm, 2.5), "vram_mb": vram, "notes": notes}


def recommendation(gpus: list[dict[str, object]], ram_gb: float) -> str:
    vram = max((int(str(g.get("vram_total_mb", 0))) for g in gpus), default=0)
    if vram >= 7000:
        llm = ("qwen3:4b-instruct-2507 (~3.5 GB VRAM) fits comfortably; 8B models (~6 GB) fit "
               "but leave little room for speech recognition on the GPU")
    elif vram >= 4000:
        llm = "qwen3:4b-instruct-2507 or llama3.2:3b"
    else:
        llm = "llama3.2:3b or qwen3:1.7b on CPU (slower)"
    if vram >= 2000:
        stt = "small.en on the GPU (needs the [cuda] extra)"
    else:
        stt = "small.en on CPU int8" if ram_gb >= 12 else "base.en on CPU int8"
    return f"LLM: {llm}. Speech recognition: {stt}. Voice: Kokoro-82M on CPU."

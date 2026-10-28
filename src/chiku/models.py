"""Model inventory and downloader.

Nothing here runs automatically. `python -m chiku --download-models` prints the
plan (sizes, destinations, free disk space) and only then downloads.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class ModelFile:
    key: str
    description: str
    url: str
    relpath: str
    approx_mb: int
    sha256: str | None
    license: str


KOKORO_MODEL = ModelFile(
    key="kokoro-model",
    description="Kokoro-82M text-to-speech (ONNX, fp32)",
    url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    relpath="kokoro/kokoro-v1.0.onnx",
    approx_mb=311,
    sha256="7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    license="Apache-2.0 (hexgrad/Kokoro-82M)",
)
KOKORO_VOICES = ModelFile(
    key="kokoro-voices",
    description="Kokoro voice style vectors",
    url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
    relpath="kokoro/voices-v1.0.bin",
    approx_mb=27,
    sha256="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    license="Apache-2.0 (hexgrad/Kokoro-82M)",
)
SILERO_VAD = ModelFile(
    key="silero-vad",
    description="Silero voice activity detector v5 (ONNX)",
    url="https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx",
    relpath="vad/silero_vad.onnx",
    approx_mb=2,
    sha256="2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f",
    license="MIT (snakers4/silero-vad)",
)

FILES: tuple[ModelFile, ...] = (KOKORO_MODEL, KOKORO_VOICES, SILERO_VAD)

WHISPER_SIZES_MB = {"tiny.en": 75, "base.en": 145, "small.en": 484, "medium.en": 1530}


def whisper_dir(models_dir: Path, name: str) -> Path:
    return models_dir / "whisper" / name


def whisper_present(models_dir: Path, name: str) -> bool:
    d = whisper_dir(models_dir, name)
    return (d / "model.bin").exists() and (d / "config.json").exists()


def file_present(models_dir: Path, f: ModelFile) -> bool:
    return (models_dir / f.relpath).exists()


def missing(models_dir: Path, whisper_model: str) -> list[str]:
    out = [f.key for f in FILES if not file_present(models_dir, f)]
    if not whisper_present(models_dir, whisper_model):
        out.append(f"whisper-{whisper_model}")
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fetch_resumable(f: ModelFile, tmp: Path, progress: Callable[[str], None]) -> None:
    have = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    timeout = httpx.Timeout(30.0, read=60.0)
    with httpx.stream(
        "GET", f.url, follow_redirects=True, timeout=timeout, headers=headers
    ) as r:
        if r.status_code == 416:  # already complete
            return
        r.raise_for_status()
        if have and r.status_code != 206:  # server ignored Range: restart
            have = 0
        total = have + int(r.headers.get("content-length", 0))
        done = have
        last_pct = -10
        with tmp.open("ab" if have else "wb") as out:
            for chunk in r.iter_bytes(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct >= last_pct + 10:
                        progress(f"       {f.key}: {pct}%")
                        last_pct = pct


def download_file(
    f: ModelFile, models_dir: Path, progress: Callable[[str], None] = print, retries: int = 5
) -> Path:
    dest = models_dir / f.relpath
    if dest.exists():
        progress(f"[skip] {f.key} already present at {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    progress(f"[get ] {f.key} (~{f.approx_mb} MB) from {f.url}")
    for attempt in range(1, retries + 1):
        try:
            _fetch_resumable(f, tmp, progress)
            break
        except httpx.HTTPError as exc:
            if attempt == retries:
                raise
            progress(f"       {f.key}: {type(exc).__name__}, resuming (attempt {attempt + 1})")
            time.sleep(2 * attempt)
    if f.sha256:
        digest = sha256_of(tmp)
        if digest != f.sha256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"checksum mismatch for {f.key}: got {digest}")
    tmp.replace(dest)
    progress(f"[ok  ] {f.key} -> {dest}")
    return dest


def download_whisper(name: str, models_dir: Path, progress: Callable[[str], None] = print) -> Path:
    dest = whisper_dir(models_dir, name)
    if whisper_present(models_dir, name):
        progress(f"[skip] whisper {name} already present at {dest}")
        return dest
    from faster_whisper import download_model

    progress(f"[get ] whisper {name} (~{WHISPER_SIZES_MB.get(name, '?')} MB) from Hugging Face")
    dest.mkdir(parents=True, exist_ok=True)
    download_model(name, output_dir=str(dest))
    progress(f"[ok  ] whisper {name} -> {dest}")
    return dest


def plan_text(models_dir: Path, whisper_model: str) -> str:
    lines = [f"Model directory: {models_dir}"]
    total = 0
    for f in FILES:
        state = "present" if file_present(models_dir, f) else "MISSING"
        if state == "MISSING":
            total += f.approx_mb
        lines.append(f"  {f.key:<16} ~{f.approx_mb:>4} MB  {state:<8} {f.license}")
    w_state = "present" if whisper_present(models_dir, whisper_model) else "MISSING"
    w_mb = WHISPER_SIZES_MB.get(whisper_model, 500)
    if w_state == "MISSING":
        total += w_mb
    lines.append(f"  whisper-{whisper_model:<8} ~{w_mb:>4} MB  {w_state:<8} MIT (OpenAI Whisper)")
    anchor = models_dir if models_dir.exists() else models_dir.anchor
    free_gb = shutil.disk_usage(anchor).free / 1e9
    lines.append(f"To download: ~{total} MB. Free space on drive: {free_gb:.1f} GB")
    return "\n".join(lines)

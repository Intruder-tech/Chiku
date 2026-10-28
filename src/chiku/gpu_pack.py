"""Optional "NVIDIA speech pack": cuBLAS + cuDNN DLLs for GPU speech recognition.

The installer doesn't bundle these (~1.2 GB download, NVIDIA licence). On an NVIDIA PC the
onboarding offers this pack: the two official wheels are downloaded from PyPI over HTTPS,
their SHA-256 is checked against the digest PyPI publishes, and only the DLLs under
``nvidia/*/bin/`` are extracted into %LOCALAPPDATA%\\Chiku\\cuda. Nothing is executed.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from chiku.cancel import Cancelled, CancelToken

# Versions verified with Chiku (faster-whisper 1.2 / CTranslate2 4.8, RTX 50-series).
PACKAGES = (("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.27.0.42"))
APPROX_DOWNLOAD_MB = 1240
APPROX_INSTALLED_MB = 2300


class PackError(RuntimeError):
    """Message is safe to show."""


def pack_dir(data_dir: Path) -> Path:
    return data_dir / "cuda"


def installed(data_dir: Path) -> bool:
    d = pack_dir(data_dir)
    return (d / "cublas64_12.dll").exists() and any(d.glob("cudnn64_*.dll"))


def wheel_info(client: httpx.Client, name: str, version: str) -> tuple[str, str, int]:
    """(url, sha256, size) of the win_amd64 wheel, as published by PyPI."""
    r = client.get(f"https://pypi.org/pypi/{name}/{version}/json")
    r.raise_for_status()
    for f in r.json().get("urls", []):
        if f.get("packagetype") == "bdist_wheel" and f.get("filename", "").endswith(
                "win_amd64.whl"):
            url = str(f["url"])
            if not url.startswith("https://files.pythonhosted.org/"):
                raise PackError("Unexpected download host for the NVIDIA pack.")
            return url, str(f["digests"]["sha256"]), int(f.get("size", 0))
    raise PackError(f"No Windows build of {name} {version} on PyPI.")


def install(data_dir: Path, progress: Callable[[str, int, int], None], cancel: CancelToken,
            transport: httpx.BaseTransport | None = None) -> Path:
    dest = pack_dir(data_dir)
    free = shutil.disk_usage(data_dir if data_dir.exists() else data_dir.anchor).free
    if free < (APPROX_DOWNLOAD_MB + APPROX_INSTALLED_MB) * 2**20 + 2 * 2**30:
        raise PackError("Not enough free disk space for the NVIDIA speech pack (~3.5 GB).")
    staging = Path(tempfile.mkdtemp(prefix="chiku-cuda-"))
    try:
        with httpx.Client(transport=transport, follow_redirects=True,
                          timeout=httpx.Timeout(30.0, read=120.0)) as client:
            for name, version in PACKAGES:
                url, digest, size = wheel_info(client, name, version)
                whl = staging / f"{name}.whl"
                h = hashlib.sha256()
                done = 0
                with client.stream("GET", url) as r, whl.open("wb") as out:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length") or size)
                    for chunk in r.iter_bytes(1 << 20):
                        if cancel.cancelled:
                            raise Cancelled("cancelled")
                        out.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        progress(f"Downloading {name}", done, total)
                if h.hexdigest() != digest:
                    raise PackError(f"{name} failed its checksum; nothing was installed.")
                progress(f"Extracting {name}", 0, 0)
                with zipfile.ZipFile(whl) as z:
                    for member in z.namelist():
                        parts = member.split("/")
                        if (len(parts) == 4 and parts[0] == "nvidia" and parts[2] == "bin"
                                and member.lower().endswith(".dll") and ".." not in parts):
                            target = staging / "dlls" / parts[3]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with z.open(member) as src, target.open("wb") as dst:
                                shutil.copyfileobj(src, dst)
                whl.unlink()
        dest.mkdir(parents=True, exist_ok=True)
        for dll in (staging / "dlls").glob("*.dll"):
            shutil.move(str(dll), dest / dll.name)
    except httpx.HTTPError as exc:
        raise PackError(f"Download failed ({type(exc).__name__}).") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if not installed(data_dir):
        raise PackError("The NVIDIA pack didn't install correctly.")
    return dest


def remove(data_dir: Path) -> None:
    shutil.rmtree(pack_dir(data_dir), ignore_errors=True)

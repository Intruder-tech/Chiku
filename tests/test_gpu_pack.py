import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from chiku import gpu_pack, hardware
from chiku.benchmark import wer
from chiku.cancel import CancelToken


def make_wheel(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def fake_pypi(wheels: dict[str, bytes], tamper: str | None = None):  # noqa: ANN201
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "pypi.org":
            name = req.url.path.split("/")[2]
            data = wheels[name]
            digest = hashlib.sha256(data).hexdigest()
            if name == tamper:
                digest = "0" * 64
            return httpx.Response(200, json={"urls": [{
                "packagetype": "bdist_wheel", "filename": f"{name}-x-win_amd64.whl",
                "url": f"https://files.pythonhosted.org/{name}.whl",
                "digests": {"sha256": digest}, "size": len(data)}]})
        name = req.url.path.strip("/").removesuffix(".whl")
        return httpx.Response(200, content=wheels[name])
    return httpx.MockTransport(handler)


WHEELS = {
    "nvidia-cublas-cu12": make_wheel({"nvidia/cublas/bin/cublas64_12.dll": b"MZ1",
                                      "nvidia/cublas/include/x.h": b"h",
                                      "nvidia/cublas/bin/../../evil.dll": b"no"}),
    "nvidia-cudnn-cu12": make_wheel({"nvidia/cudnn/bin/cudnn64_9.dll": b"MZ2"}),
}


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    class Usage:
        free = 100 * 2**30

    monkeypatch.setattr(gpu_pack.shutil, "disk_usage", lambda _p: Usage())


def test_installs_only_bin_dlls_after_checksum(tmp_path: Path) -> None:
    seen = []
    dest = gpu_pack.install(tmp_path, lambda s, d, t: seen.append(s), CancelToken(),
                            transport=fake_pypi(WHEELS))
    assert sorted(p.name for p in dest.iterdir()) == ["cublas64_12.dll", "cudnn64_9.dll"]
    assert gpu_pack.installed(tmp_path)
    assert not (tmp_path / "evil.dll").exists()
    gpu_pack.remove(tmp_path)
    assert not gpu_pack.installed(tmp_path)


def test_checksum_mismatch_installs_nothing(tmp_path: Path) -> None:
    with pytest.raises(gpu_pack.PackError, match="checksum"):
        gpu_pack.install(tmp_path, lambda *a: None, CancelToken(),
                         transport=fake_pypi(WHEELS, tamper="nvidia-cudnn-cu12"))
    assert not gpu_pack.pack_dir(tmp_path).exists()


def test_recommendations_cover_non_nvidia_pcs() -> None:
    nv = [{"vram_total_mb": 8151}]
    rich = hardware.recommend_setup(nv, 16, 24, cuda_ready=True)
    assert rich["stt_model"] == "small.en" and rich["llm_model"].startswith("qwen3:4b")
    no_pack = hardware.recommend_setup(nv, 16, 24, cuda_ready=False)
    assert any("speech pack" in n for n in no_pack["notes"])
    laptop = hardware.recommend_setup([], 16, 8, cuda_ready=False)
    assert laptop["stt_model"] == "base.en" and laptop["stt_device"] == "cpu"
    small = hardware.recommend_setup([], 8, 4, cuda_ready=False)
    assert small["stt_model"] == "tiny.en" and small["llm_model"] == "llama3.2:3b"
    tiny = hardware.recommend_setup([], 4, 2, cuda_ready=False)
    assert tiny["llm_model"] == "llama3.2:1b"


def test_wer() -> None:
    assert wer("open google chrome", "Open Google Chrome.") == 0
    assert wer("on the fourteenth of march", "on the 14th of march") == pytest.approx(0.2)

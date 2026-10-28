"""First-run setup: welcome, hardware check, model downloads (with sizes, all optional)."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

import psutil
from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from chiku import gpu_pack, hardware, models
from chiku.branding import WELCOME, asset
from chiku.cancel import CancelToken
from chiku.config import Settings, save_settings
from chiku.llm.manage import ModelError, OllamaManager
from chiku.paths import AppPaths


class _Sig(QObject):
    progress = Signal(str, int, int)
    line = Signal(str)
    done = Signal(bool)
    hw = Signal(object)


def _label(text: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    return lab


class OnboardingWizard(QWizard):
    def __init__(self, settings: Settings, paths: AppPaths, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.paths = paths
        self.plan: dict[str, Any] = {}
        self.sig = _Sig()
        self.setWindowTitle("Welcome to Chiku")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setPixmap(QWizard.WizardPixmap.WatermarkPixmap,
                       QPixmap(str(asset(WELCOME))).scaledToWidth(
                           200, Qt.TransformationMode.SmoothTransformation))
        self.resize(760, 560)
        self.addPage(self._welcome())
        self.addPage(self._hardware())
        self.addPage(self._downloads())
        self.button(QWizard.WizardButton.FinishButton).clicked.connect(self._finish)

    # -- pages ---------------------------------------------------------------------------
    def _welcome(self) -> QWizardPage:
        p = QWizardPage()
        p.setTitle("Hi, I'm Chiku!")
        lay = QVBoxLayout(p)
        lay.addWidget(_label(
            "I'm your voice assistant. I listen, talk back in a natural voice, and can open "
            "apps, search the web, find files and remember things you ask me to.\n\n"
            "Privacy first: speech recognition, my voice and my brain (the language model) run "
            "on this computer. The microphone only listens while the Mic button is on, and "
            "audio is never saved.\n\nNext, I'll check your computer and suggest models that "
            "fit it. Nothing is downloaded without your OK."))
        lay.addStretch()
        return p

    def _hardware(self) -> QWizardPage:
        p = QWizardPage()
        p.setTitle("Your computer")
        p.setSubTitle("Detected on this PC. Recommendations adapt to it.")
        lay = QVBoxLayout(p)
        self.hw_label = _label("Checking hardware…")
        lay.addWidget(self.hw_label)
        lay.addStretch()
        self.sig.hw.connect(self._show_hw)
        threading.Thread(target=self._detect, daemon=True).start()
        return p

    def _detect(self) -> None:
        nvidia = hardware.gpu_info()
        ram = psutil.virtual_memory().total / 2**30
        cores = psutil.cpu_count() or 4
        cuda_ready = gpu_pack.installed(self.paths.data_dir) or _pip_cuda_present()
        plan = hardware.recommend_setup(nvidia, ram, cores, cuda_ready)
        plan.update(cpu=hardware.cpu_name(), ram=round(ram, 1), cores=cores,
                    adapters=hardware.display_adapters(), nvidia=nvidia, cuda_ready=cuda_ready)
        self.sig.hw.emit(plan)

    @Slot(object)
    def _show_hw(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        gpus = ", ".join(plan["adapters"]) or "none reported"
        lines = [
            f"Processor: {plan['cpu']} ({plan['cores']} threads)",
            f"Memory: {plan['ram']} GB",
            f"Graphics: {gpus}",
            "",
            "Suggested setup:",
            f"  • Speech recognition: Whisper {plan['stt_model']} "
            f"({'GPU' if plan['stt_device'] == 'auto' and plan['vram_mb'] else 'CPU'})",
            f"  • Voice: Kokoro {self.settings.tts.voice} (CPU)",
            f"  • Language model: {plan['llm_model']} (~{plan['llm_size_gb']} GB) via Ollama",
        ]
        if plan["notes"]:
            lines += ["", *[f"Note: {n}" for n in plan["notes"]]]
        self.hw_label.setText("\n".join(lines))
        self._fill_downloads()

    def _downloads(self) -> QWizardPage:
        p = QWizardPage()
        p.setTitle("Models")
        p.setSubTitle("Choose what to download now. You can skip and do it later in Settings.")
        lay = QVBoxLayout(p)
        self.box_voice = QCheckBox()
        self.box_stt = QCheckBox()
        self.box_gpu = QCheckBox()
        self.box_llm = QCheckBox()
        for b in (self.box_voice, self.box_stt, self.box_gpu, self.box_llm):
            lay.addWidget(b)
        self.ollama_row = QHBoxLayout()
        self.ollama_status = _label("")
        get = QPushButton("Get Ollama…")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://ollama.com/download")))
        recheck = QPushButton("Check again")
        recheck.clicked.connect(self._fill_downloads)
        self.ollama_row.addWidget(self.ollama_status, 1)
        self.ollama_row.addWidget(get)
        self.ollama_row.addWidget(recheck)
        lay.addLayout(self.ollama_row)
        row = QHBoxLayout()
        self.go = QPushButton("Download selected")
        self.go.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Cancel download")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self.go)
        row.addWidget(self.cancel_btn)
        row.addStretch()
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.log = _label("")
        lay.addWidget(self.log)
        lay.addStretch()
        self._token: CancelToken | None = None
        self.sig.progress.connect(self._on_progress)
        self.sig.line.connect(lambda s: self.log.setText(s))
        self.sig.done.connect(self._on_done)
        return p

    def _cancel(self) -> None:
        if self._token is not None:
            self._token.cancel("user")

    def _fill_downloads(self) -> None:
        md = self.paths.models_dir
        stt = str(self.plan.get("stt_model", self.settings.stt.model))
        voice_missing = [f for f in models.FILES if not models.file_present(md, f)]
        mb = sum(f.approx_mb for f in voice_missing)
        self.box_voice.setText("Voice and voice detection (Kokoro + Silero"
                               + (f", ~{mb} MB)" if voice_missing else ")  ✓ installed"))
        self.box_voice.setChecked(bool(voice_missing))
        self.box_voice.setEnabled(bool(voice_missing))
        have_stt = models.whisper_present(md, stt)
        self.box_stt.setText(f"Speech recognition: Whisper {stt} "
                             f"(~{models.WHISPER_SIZES_MB.get(stt, 500)} MB)"
                             + ("  ✓ installed" if have_stt else ""))
        self.box_stt.setChecked(not have_stt)
        self.box_stt.setEnabled(not have_stt)
        show_gpu = bool(self.plan.get("vram_mb")) and not self.plan.get("cuda_ready")
        self.box_gpu.setVisible(show_gpu)
        self.box_gpu.setText(f"Optional NVIDIA speech pack for fast GPU speech recognition "
                             f"(~{gpu_pack.APPROX_DOWNLOAD_MB} MB download, from PyPI, "
                             "checksum-verified)")
        llm = str(self.plan.get("llm_model", self.settings.llm.model))
        try:
            mgr = OllamaManager(self.settings.llm.ollama_url)
            names = [m.name for m in mgr.installed()]
            mgr.close()
            ok = True
        except ModelError:
            names, ok = [], False
        self._ollama_ok = ok
        self.ollama_status.setText("Ollama is running ✓" if ok else
                                   "Ollama (the local AI runtime) isn't running or installed. "
                                   "Install it, then press Check again.")
        have_llm = llm in names
        self.box_llm.setText(f"Language model {llm} (~{self.plan.get('llm_size_gb', 2.5)} GB, "
                             "via Ollama)" + ("  ✓ installed" if have_llm else ""))
        self.box_llm.setChecked(ok and not have_llm)
        self.box_llm.setEnabled(ok and not have_llm)

    # -- downloading -------------------------------------------------------------------------
    def _start(self) -> None:
        self._token = CancelToken()
        token = self._token
        jobs: list[tuple[str, Callable[[], object]]] = []
        md, stt = self.paths.models_dir, str(self.plan.get("stt_model", self.settings.stt.model))
        if self.box_voice.isChecked() and self.box_voice.isEnabled():
            jobs.append(("voice", lambda: [models.download_file(f, md, self.sig.line.emit)
                                           for f in models.FILES]))
        if self.box_stt.isChecked() and self.box_stt.isEnabled():
            jobs.append(("speech", lambda: models.download_whisper(stt, md, self.sig.line.emit)))
        if self.box_gpu.isVisible() and self.box_gpu.isChecked():
            jobs.append(("gpu", lambda: gpu_pack.install(
                self.paths.data_dir, self.sig.progress.emit, token)))
        if self.box_llm.isChecked() and self.box_llm.isEnabled():
            llm = str(self.plan.get("llm_model"))

            def pull() -> None:
                mgr = OllamaManager(self.settings.llm.ollama_url)
                try:
                    mgr.pull(llm, self.sig.progress.emit, token)
                finally:
                    mgr.close()

            jobs.append(("llm", pull))
        if not jobs:
            return
        self.go.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.bar.setVisible(True)

        def run() -> None:
            ok = True
            for name, job in jobs:
                if token.cancelled:
                    ok = False
                    break
                self.sig.line.emit(f"Working on {name}…")
                try:
                    job()
                except Exception as exc:
                    ok = False
                    self.sig.line.emit(f"{name}: {exc}")
                    break
            self.sig.done.emit(ok)

        threading.Thread(target=run, daemon=True).start()

    @Slot(str, int, int)
    def _on_progress(self, status: str, done: int, total: int) -> None:
        if total:
            self.bar.setMaximum(1000)
            self.bar.setValue(int(done * 1000 / total))
            self.log.setText(f"{status}: {done / 1e9:.2f} / {total / 1e9:.2f} GB")
        else:
            self.bar.setMaximum(0)
            self.log.setText(status)

    @Slot(bool)
    def _on_done(self, ok: bool) -> None:
        self.go.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.bar.setVisible(False)
        if ok:
            self.log.setText("All selected downloads finished ✓")
        self._fill_downloads()

    def _finish(self) -> None:
        data = self.settings.model_dump()
        if self.plan:
            data["stt"]["model"] = self.plan["stt_model"]
            data["stt"]["device"] = self.plan["stt_device"]
            try:
                mgr = OllamaManager(self.settings.llm.ollama_url)
                names = [m.name for m in mgr.installed()]
                mgr.close()
            except ModelError:
                names = []
            if self.plan["llm_model"] in names:
                data["llm"]["model"] = self.plan["llm_model"]
        data["onboarding_done"] = True
        self.settings = Settings.model_validate(data)
        save_settings(self.settings, self.paths.settings_file)


def _pip_cuda_present() -> bool:
    import site

    return any(os.path.isdir(os.path.join(b, "nvidia", "cublas", "bin"))
               for b in site.getsitepackages())


def needs_onboarding(settings: Settings, paths: AppPaths) -> bool:
    return not settings.onboarding_done or bool(models.missing(paths.models_dir,
                                                               settings.stt.model))

"""Model management: installed Ollama models, download/delete/switch, Whisper size."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chiku import hardware, models
from chiku.assistant import Assistant
from chiku.cancel import CancelToken
from chiku.config import Settings, save_settings
from chiku.llm.manage import ModelError, OllamaManager, free_bytes, ollama_models_dir
from chiku.paths import AppPaths


class _Signals(QObject):
    progress = Signal(str, int, int)
    finished = Signal(str, bool)  # message, ok
    listed = Signal(object, object)  # models, loaded


class ModelsDialog(QDialog):
    def __init__(self, assistant: Assistant, paths: AppPaths, parent: QWidget | None = None):
        super().__init__(parent)
        self.assistant = assistant
        self.paths = paths
        self.mgr = OllamaManager(assistant.settings.llm.ollama_url)
        self.sig = _Signals()
        self.sig.progress.connect(self._on_progress)
        self.sig.finished.connect(self._on_finished)
        self.sig.listed.connect(self._on_listed)
        self._cancel: CancelToken | None = None
        self.setWindowTitle("Models")
        self.resize(760, 540)
        lay = QVBoxLayout(self)

        gpus = hardware.gpu_info()
        vram = max((int(str(g.get("vram_total_mb", 0))) for g in gpus), default=0)
        free_gb = free_bytes(ollama_models_dir()) / 1e9
        info = QLabel(f"GPU memory: {vram / 1024:.1f} GB · free disk for models: "
                      f"{free_gb:.0f} GB. Models up to about {max(1.0, vram / 1024 - 2.5):.1f} GB "
                      "fit on the GPU next to speech recognition.")
        info.setObjectName("notice")
        info.setWordWrap(True)
        lay.addWidget(info)

        lay.addWidget(QLabel("Installed language models (Ollama)"))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Model", "Size", "Parameters", "Quantization",
                                              "Can use tools", "Loaded (VRAM)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.use_btn = QPushButton("Use selected")
        self.use_btn.clicked.connect(self._use)
        self.del_btn = QPushButton("Delete selected…")
        self.del_btn.clicked.connect(self._delete)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        for b in (self.use_btn, self.del_btn, refresh):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)

        pull_row = QHBoxLayout()
        self.pull_name = QLineEdit()
        self.pull_name.setPlaceholderText("Model to download, e.g. llama3.2:3b or gemma3:4b")
        self.pull_btn = QPushButton("Download")
        self.pull_btn.clicked.connect(self._pull)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_pull)
        pull_row.addWidget(self.pull_name, 1)
        pull_row.addWidget(self.pull_btn)
        pull_row.addWidget(self.cancel_btn)
        lay.addLayout(pull_row)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setObjectName("notice")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        lay.addWidget(QLabel("Speech recognition model (Whisper)"))
        w_row = QHBoxLayout()
        self.whisper = QComboBox()
        for name, mb in models.WHISPER_SIZES_MB.items():
            have = models.whisper_present(paths.models_dir, name)
            self.whisper.addItem(f"{name} · {mb} MB · {'installed' if have else 'not downloaded'}",
                                 name)
        idx = self.whisper.findData(assistant.settings.stt.model)
        self.whisper.setCurrentIndex(max(0, idx))
        w_btn = QPushButton("Download && use")
        w_btn.clicked.connect(self._use_whisper)
        w_row.addWidget(self.whisper, 1)
        w_row.addWidget(w_btn)
        lay.addLayout(w_row)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close)
        self.refresh()

    # -- listing ------------------------------------------------------------------------
    def refresh(self) -> None:
        self.status.setText("Loading…")

        def work() -> None:
            try:
                installed = self.mgr.installed()
                loaded = self.mgr.loaded()
                caps = {m.name: self.mgr.capabilities(m.name) for m in installed}
                self.sig.listed.emit((installed, caps), loaded)
            except ModelError as exc:
                self.sig.finished.emit(str(exc), False)

        threading.Thread(target=work, daemon=True).start()

    @Slot(object, object)
    def _on_listed(self, data: tuple, loaded: dict) -> None:
        installed, caps = data
        self._models = installed
        self.table.setRowCount(len(installed))
        current = self.assistant.settings.llm.model
        for r, m in enumerate(installed):
            c = caps.get(m.name, [])
            tools = "yes" if "tools" in c else "no"
            if "thinking" in c:
                tools += " (reasoning)"
            vals = [("● " if m.name == current else "") + m.name, f"{m.size_gb:.1f} GB",
                    m.parameter_size, m.quantization, tools,
                    f"{loaded[m.name]:.1f} GB" if m.name in loaded else ""]
            for col, v in enumerate(vals):
                self.table.setItem(r, col, QTableWidgetItem(v))
        self.status.setText(f"{len(installed)} model(s). ● = in use.")

    def _selected(self) -> str | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self._models[min(rows)].name if rows else None

    # -- actions ------------------------------------------------------------------------
    def _apply(self, mutate) -> None:  # noqa: ANN001
        data = self.assistant.settings.model_dump()
        mutate(data)
        new = Settings.model_validate(data)
        save_settings(new, self.paths.settings_file)
        self.assistant.apply_settings(new)

    def _use(self) -> None:
        name = self._selected()
        if not name:
            return

        def mutate(d: dict) -> None:
            d["llm"]["provider"] = "ollama"
            d["llm"]["model"] = name

        self._apply(mutate)
        self.status.setText(f"Chiku now uses {name}.")
        self.refresh()

    def _delete(self) -> None:
        name = self._selected()
        if not name:
            return
        if name == self.assistant.settings.llm.model:
            QMessageBox.information(self, "Chiku", "That model is in use. Switch to another "
                                    "model first.")
            return
        if QMessageBox.question(
            self, "Chiku", f"Delete {name} from this computer? You can download it again later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.mgr.delete(name)
            self.status.setText(f"Deleted {name}.")
        except ModelError as exc:
            self.status.setText(str(exc))
        self.refresh()

    def _pull(self) -> None:
        name = self.pull_name.text().strip()
        if not name:
            return
        self._cancel = CancelToken()
        token = self._cancel
        self.pull_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)

        def work() -> None:
            try:
                self.mgr.pull(name, lambda s, d, t: self.sig.progress.emit(s, d, t), token)
                self.sig.finished.emit(f"Downloaded {name}.", True)
            except ModelError as exc:
                self.sig.finished.emit(str(exc), False)
            except Exception as exc:  # Cancelled or unexpected
                self.sig.finished.emit(f"Download stopped ({type(exc).__name__}).", False)

        threading.Thread(target=work, daemon=True).start()

    def _cancel_pull(self) -> None:
        if self._cancel is not None:
            self._cancel.cancel("user")

    @Slot(str, int, int)
    def _on_progress(self, status: str, done: int, total: int) -> None:
        if total:
            self.progress.setMaximum(1000)
            self.progress.setValue(int(done * 1000 / total))
            self.status.setText(f"{status}: {done / 1e9:.2f} / {total / 1e9:.2f} GB")
        else:
            self.status.setText(status)

    @Slot(str, bool)
    def _on_finished(self, message: str, ok: bool) -> None:
        self.pull_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.setVisible(False)
        self.status.setText(message)
        if ok:
            self.refresh()

    def _use_whisper(self) -> None:
        name = str(self.whisper.currentData())
        mb = models.WHISPER_SIZES_MB.get(name, 0)
        if not models.whisper_present(self.paths.models_dir, name) and QMessageBox.question(
            self, "Chiku", f"Download Whisper {name} (about {mb} MB) from Hugging Face?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.status.setText(f"Preparing Whisper {name}…")

        def work() -> None:
            try:
                models.download_whisper(name, self.paths.models_dir, lambda _m: None)
            except Exception as exc:
                self.sig.finished.emit(f"Whisper download failed: {exc}", False)
                return

            def mutate(d: dict) -> None:
                d["stt"]["model"] = name

            self._apply(mutate)
            self.sig.finished.emit(f"Speech recognition now uses Whisper {name}.", True)

        threading.Thread(target=work, daemon=True).start()

    def done(self, result: int) -> None:  # noqa: D401 - Qt override
        self._cancel_pull()
        self.mgr.close()
        super().done(result)

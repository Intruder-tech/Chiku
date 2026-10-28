"""Settings: audio, voice, AI model, persona, permissions & privacy."""

from __future__ import annotations

import os
import re

from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chiku import credentials
from chiku.assistant import Assistant
from chiku.audio import devices
from chiku.config import ALL_FOLDERS, Settings, is_loopback_url, save_settings
from chiku.llm.openai_compat import OPENAI_KEY_NAME
from chiku.paths import AppPaths
from chiku.web.search import BRAVE_KEY_NAME

_DEFAULT = "System default"
_FALLBACK_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "bf_emma",
                    "bf_isabella", "hf_alpha", "hf_beta"]


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("notice")
    lab.setWordWrap(True)
    return lab


class SettingsDialog(QDialog):
    def __init__(self, assistant: Assistant, paths: AppPaths, parent: QWidget | None = None):
        super().__init__(parent)
        self.assistant = assistant
        self.paths = paths
        self.s = assistant.settings
        self.setWindowTitle("Chiku settings")
        self.resize(520, 560)
        tabs = QTabWidget()
        tabs.addTab(self._audio_tab(), "Audio")
        tabs.addTab(self._voice_tab(), "Voice")
        tabs.addTab(self._ai_tab(), "AI model")
        tabs.addTab(self._persona_tab(), "Persona")
        tabs.addTab(self._web_tab(), "Web")
        tabs.addTab(self._tools_tab(), "Tools")
        tabs.addTab(self._privacy_tab(), "Privacy")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)

    # -- tabs -------------------------------------------------------------------------
    def _audio_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        devices.rescan()
        self.in_dev = QComboBox()
        self.out_dev = QComboBox()
        for combo, kind, current in ((self.in_dev, "input", self.s.audio.input_device),
                                     (self.out_dev, "output", self.s.audio.output_device)):
            combo.addItem(_DEFAULT)
            for d in devices.list_devices(kind):  # type: ignore[arg-type]
                combo.addItem(d.name)
            if current:
                idx = combo.findText(current)
                if idx < 0:
                    combo.addItem(f"{current} (not connected)", current)
                    idx = combo.count() - 1
                combo.setCurrentIndex(idx)
        f.addRow("Microphone", self.in_dev)
        f.addRow("Speaker", self.out_dev)
        self.vad = QSlider(Qt.Orientation.Horizontal)
        self.vad.setRange(30, 85)
        self.vad.setValue(int(self.s.audio.vad_threshold * 100))
        f.addRow("Speech detection strictness", self.vad)
        self.pause_ms = QSpinBox()
        self.pause_ms.setRange(300, 2500)
        self.pause_ms.setSingleStep(100)
        self.pause_ms.setSuffix(" ms")
        self.pause_ms.setValue(self.s.audio.min_silence_ms)
        f.addRow("Pause that ends your turn", self.pause_ms)
        self.barge = QCheckBox("Let me interrupt Chiku by talking")
        self.barge.setChecked(self.s.audio.barge_in)
        f.addRow(self.barge)
        f.addRow(_note("Interrupting by voice works best with headphones. On laptop speakers "
                       "Chiku may hear herself; use Esc or the Stop button instead."))
        return w

    def _voice_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.voice = QComboBox()
        voices = self.assistant.available_voices() or _FALLBACK_VOICES
        self.voice.addItems(voices)
        idx = self.voice.findText(self.s.tts.voice)
        self.voice.setCurrentIndex(max(0, idx))
        f.addRow("Voice", self.voice)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.7, 1.4)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(self.s.tts.speed)
        f.addRow("Speaking pace", self.speed)
        test = QPushButton("Test voice")
        test.clicked.connect(
            lambda: self.assistant.preview_voice(self.voice.currentText(), self.speed.value())
        )
        f.addRow(test)
        f.addRow(_note("Voices are Kokoro-82M female voices. 'af_' = American English, 'bf_' = "
                       "British English, 'hf_' = Hindi. Generated on this computer."))
        return w

    def _ai_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.provider = QComboBox()
        self.provider.addItem("Ollama (local)", "ollama")
        self.provider.addItem("OpenAI-compatible server (LM Studio, llama.cpp, …)",
                              "openai_compat")
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.s.llm.provider)))
        f.addRow("Provider", self.provider)
        manage = QPushButton("Manage models…")
        manage.clicked.connect(self._manage_models)
        f.addRow(manage)
        f.addRow(QLabel("— Ollama —"))
        self.url = QLineEdit(self.s.llm.ollama_url)
        f.addRow("Ollama address", self.url)
        self.model = QComboBox()
        self.model.setEditable(True)
        try:
            names = self.assistant._llm.status().models  # noqa: SLF001
        except Exception:
            names = []
        self.model.addItems(sorted(set(names) | {self.s.llm.model}))
        self.model.setCurrentText(self.s.llm.model)
        f.addRow("Model", self.model)
        self.temp = QDoubleSpinBox()
        self.temp.setRange(0.0, 1.5)
        self.temp.setSingleStep(0.1)
        self.temp.setValue(self.s.llm.temperature)
        f.addRow("Creativity (temperature)", self.temp)
        self.turns = QSpinBox()
        self.turns.setRange(1, 50)
        self.turns.setValue(self.s.llm.max_history_turns)
        f.addRow("Turns of context remembered", self.turns)
        self.remote = QCheckBox("Allow an Ollama server on another computer")
        self.remote.setChecked(self.s.llm.allow_remote_ollama)
        f.addRow(self.remote)
        f.addRow(_note("With a local address, conversations never leave this computer. A remote "
                       "address sends your conversation to that machine; replies are then "
                       "labelled REMOTE."))
        f.addRow(QLabel("— OpenAI-compatible server —"))
        self.oa_url = QLineEdit(self.s.llm.openai_base_url)
        f.addRow("Base URL", self.oa_url)
        self.oa_model = QLineEdit(self.s.llm.openai_model)
        self.oa_model.setPlaceholderText("model id as the server lists it")
        f.addRow("Model", self.oa_model)
        key_row = QHBoxLayout()
        self.oa_key = QLineEdit()
        self.oa_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.oa_key.setPlaceholderText("API key (optional for local servers)")
        store = QPushButton("Store key")
        store.clicked.connect(self._store_oa_key)
        key_row.addWidget(self.oa_key, 1)
        key_row.addWidget(store)
        f.addRow(key_row)
        self.cloud = QCheckBox("Allow a cloud provider (sends conversations off this computer)")
        self.cloud.setChecked(self.s.llm.allow_cloud)
        self.cloud.toggled.connect(self._confirm_cloud)
        f.addRow(self.cloud)
        return w

    def _manage_models(self) -> None:
        from chiku.ui.models_dialog import ModelsDialog

        ModelsDialog(self.assistant, self.paths, self).exec()
        self.s = self.assistant.settings
        self.model.setCurrentText(self.s.llm.model)

    def _store_oa_key(self) -> None:
        key = self.oa_key.text().strip()
        if not key:
            credentials.delete_key(OPENAI_KEY_NAME)
            QMessageBox.information(self, "Chiku", "API key removed.")
            return
        try:
            credentials.set_key(OPENAI_KEY_NAME, key)
        except credentials.CredentialError as exc:
            QMessageBox.warning(self, "Chiku", str(exc))
            return
        self.oa_key.clear()
        QMessageBox.information(self, "Chiku", "Key stored in Windows Credential Manager.")

    def _confirm_cloud(self, on: bool) -> None:
        if not on:
            return
        answer = QMessageBox.question(
            self, "Allow a cloud provider?",
            "If the OpenAI-compatible URL is not on this computer, everything you say to Chiku "
            "(your transcribed speech, the conversation so far, tool results such as web pages "
            "or file text she read) will be sent to that provider. Your microphone audio is "
            "never sent. Memories are only included if you also allow it under Privacy. "
            "Replies will be labelled CLOUD.\n\nAllow this?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.cloud.setChecked(False)

    def _persona_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        p = self.s.persona
        self.user_name = QLineEdit(p.user_name or "")
        f.addRow("Your name", self.user_name)
        self.tone = QLineEdit(p.tone)
        f.addRow("Tone", self.tone)
        self.length = QComboBox()
        self.length.addItems(["brief", "balanced", "detailed"])
        self.length.setCurrentText(p.response_length)
        f.addRow("Reply length", self.length)
        self.language = QLineEdit(p.language)
        f.addRow("Language", self.language)
        self.pron = QPlainTextEdit("\n".join(f"{k}={v}" for k, v in p.pronunciations.items()))
        self.pron.setPlaceholderText("One per line: word=how to say it")
        self.pron.setMaximumHeight(80)
        f.addRow("Pronunciations", self.pron)
        self.extra = QPlainTextEdit(p.extra_instructions)
        self.extra.setPlaceholderText("Anything else Chiku should keep in mind")
        self.extra.setMaximumHeight(90)
        f.addRow("Preferences", self.extra)
        self.theme = QComboBox()
        for key, text in (("system", "Follow Windows"), ("light", "Light"), ("dark", "Dark")):
            self.theme.addItem(text, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(self.s.ui.theme)))
        f.addRow("Theme", self.theme)
        self.start_compact = QCheckBox("Start in compact mode (small floating Chiku)")
        self.start_compact.setChecked(self.s.ui.start_compact)
        f.addRow(self.start_compact)
        return w

    def _web_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        web = self.s.web
        self.search_on = QCheckBox("Let Chiku search the web (Brave Search)")
        self.search_on.setChecked(web.search_enabled)
        f.addRow(self.search_on)
        self.key_status = QLabel()
        self._refresh_key_status()
        f.addRow("API key", self.key_status)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste a Brave Search API key")
        row = QHBoxLayout()
        row.addWidget(self.key_edit, 1)
        save_key = QPushButton("Store key")
        save_key.clicked.connect(self._store_key)
        remove_key = QPushButton("Remove")
        remove_key.clicked.connect(self._remove_key)
        row.addWidget(save_key)
        row.addWidget(remove_key)
        f.addRow(row)
        self.country = QLineEdit(web.country)
        self.country.setMaxLength(2)
        self.country.setPlaceholderText("e.g. IN, US (optional)")
        f.addRow("Results for country", self.country)
        self.results = QSpinBox()
        self.results.setRange(1, 10)
        self.results.setValue(web.max_results)
        f.addRow("Results per search", self.results)
        self.read_pages = QCheckBox("Let Chiku open result pages to read them")
        self.read_pages.setChecked(web.allow_page_reading)
        f.addRow(self.read_pages)
        f.addRow(_note(
            "What is sent: only the search words go to Brave Search (with your API key). Reading "
            "a result page connects directly to that website. Your voice, conversation history "
            "and files are never sent. Get a free key at api-search.brave.com. It is stored in "
            "Windows Credential Manager, not in a file."
        ))
        return w

    def _refresh_key_status(self) -> None:
        try:
            has = credentials.has_key(BRAVE_KEY_NAME)
        except credentials.CredentialError as exc:
            self.key_status.setText(f"Credential store unavailable: {exc}")
            return
        self.key_status.setText("Stored in Windows Credential Manager ✓" if has else "Not set")

    def _store_key(self) -> None:
        key = self.key_edit.text().strip()
        if len(key) < 10:
            QMessageBox.warning(self, "Chiku", "That doesn't look like an API key.")
            return
        try:
            credentials.set_key(BRAVE_KEY_NAME, key)
        except credentials.CredentialError as exc:
            QMessageBox.warning(self, "Chiku", str(exc))
            return
        self.key_edit.clear()
        self._refresh_key_status()

    def _remove_key(self) -> None:
        credentials.delete_key(BRAVE_KEY_NAME)
        self._refresh_key_status()

    def _tools_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(_note("Switch individual abilities off. A switched-off tool is hidden from "
                          "the AI and refused if anything tries to run it."))
        self.tool_boxes: dict[str, QCheckBox] = {}
        scroll_body = QWidget()
        inner = QVBoxLayout(scroll_body)
        for tool in self.assistant._registry.all():  # noqa: SLF001
            first = re.split(r"(?<!e\.g)(?<!i\.e)\.(?:\s|$)", tool.description)[0]
            if len(first) > 75:
                first = first[:74].rsplit(" ", 1)[0] + "…"
            box = QCheckBox(f"{tool.name}  —  {first}")
            box.setToolTip(tool.description)
            box.setChecked(tool.name not in self.s.tools.disabled)
            self.tool_boxes[tool.name] = box
            inner.addWidget(box)
        inner.addStretch()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(scroll_body)
        v.addWidget(area, 1)
        return w

    def _privacy_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        mem = self.s.memory
        self.mem_on = QCheckBox("Let Chiku remember things you ask her to remember")
        self.mem_on.setChecked(mem.enabled)
        self.mem_ask = QCheckBox("Ask me before Chiku saves something I didn't ask her to")
        self.mem_ask.setChecked(mem.ask_before_model_saves)
        self.mem_cloud = QCheckBox("Include memories when using a cloud provider")
        self.mem_cloud.setChecked(mem.share_with_cloud)
        for b in (self.mem_on, self.mem_ask, self.mem_cloud):
            v.addWidget(b)
        self.auto_apps = QCheckBox("Open apps without asking")
        self.auto_apps.setChecked(self.s.permissions.auto_allow_open_apps)
        self.sensitive = QCheckBox("Always ask before opening terminals and admin tools")
        self.sensitive.setChecked(self.s.permissions.confirm_sensitive_apps)
        self.confirm_close = QCheckBox("Ask before closing apps")
        self.confirm_close.setChecked(self.s.permissions.confirm_close_apps)
        v.addWidget(self.auto_apps)
        v.addWidget(self.sensitive)
        v.addWidget(self.confirm_close)
        fs = self.s.files
        self.files_on = QCheckBox("Let Chiku find and open files in these folders:")
        self.files_on.setChecked(fs.enabled)
        v.addWidget(self.files_on)
        folder_row = QHBoxLayout()
        self.folder_boxes: dict[str, QCheckBox] = {}
        for name in ALL_FOLDERS:
            box = QCheckBox(name)
            box.setChecked(name in fs.folders)
            self.folder_boxes[name] = box
            folder_row.addWidget(box)
        v.addLayout(folder_row)
        self.files_read = QCheckBox("Let Chiku read text files to summarise them")
        self.files_read.setChecked(fs.allow_reading)
        self.files_change = QCheckBox("Let Chiku create folders and (with your OK each time) "
                                      "move, rename or recycle files")
        self.files_change.setChecked(fs.allow_changes)
        v.addWidget(self.files_read)
        v.addWidget(self.files_change)
        v.addWidget(QLabel("Websites that open without asking (one per line):"))
        self.domains = QPlainTextEdit("\n".join(self.s.permissions.allowed_url_domains))
        self.domains.setMaximumHeight(110)
        v.addWidget(self.domains)
        v.addWidget(_note(
            "Privacy: the microphone only runs while the Mic button is on. Audio is processed "
            "in memory and never saved. Speech recognition, the voice and the language model "
            "all run on this computer; only web searches (the query words) leave it. "
            "Conversations are not written to disk or logs; every action Chiku takes is "
            "recorded in a local action log. Deleting always goes to the Recycle Bin."
        ))
        row = QHBoxLayout()
        log_btn = QPushButton("Open action log folder")
        log_btn.clicked.connect(lambda: os.startfile(self.paths.audit_file.parent))  # noqa: S606
        clear_btn = QPushButton("Forget this conversation")
        clear_btn.clicked.connect(self._clear)
        wipe_btn = QPushButton("Delete all Chiku data…")
        wipe_btn.clicked.connect(self._wipe)
        row.addWidget(log_btn)
        row.addWidget(clear_btn)
        row.addWidget(wipe_btn)
        v.addLayout(row)
        v.addWidget(_note(f"Data on this computer: settings {self.paths.settings_file}; memory "
                          f"{self.paths.memory_file}; action log {self.paths.audit_file}; "
                          f"models {self.paths.models_dir}; logs {self.paths.logs_dir}."))
        v.addStretch()
        return w

    # -- save -------------------------------------------------------------------------
    def _clear(self) -> None:
        self.assistant.clear_history()
        QMessageBox.information(self, "Chiku", "Conversation context cleared.")

    def _wipe(self) -> None:
        if QMessageBox.question(
            self, "Chiku",
            "Delete all memories, the action history, logs and the current conversation? "
            "Settings, stored API keys and downloaded models are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.assistant.clear_history()
        if self.assistant.memory is not None:
            self.assistant.memory.clear()
        audit = self.assistant._executor.audit  # noqa: SLF001
        if audit is not None:
            audit.clear()
        for f in self.paths.logs_dir.glob("chiku.log.*"):
            f.unlink(missing_ok=True)
        QMessageBox.information(self, "Chiku", "Done. (The current log file is emptied when "
                                "Chiku restarts.)")

    def _device(self, combo: QComboBox) -> str | None:
        data = combo.currentData()
        if data:
            return str(data)
        text = combo.currentText()
        return None if text == _DEFAULT else text

    def _save(self) -> None:
        data = self.s.model_dump()
        data["audio"].update(
            input_device=self._device(self.in_dev),
            output_device=self._device(self.out_dev),
            vad_threshold=self.vad.value() / 100,
            min_silence_ms=self.pause_ms.value(),
            barge_in=self.barge.isChecked(),
        )
        data["tts"].update(voice=self.voice.currentText(), speed=round(self.speed.value(), 2))
        data["llm"].update(
            provider=self.provider.currentData(),
            ollama_url=self.url.text().strip(),
            model=self.model.currentText().strip(),
            temperature=round(self.temp.value(), 2),
            max_history_turns=self.turns.value(),
            allow_remote_ollama=self.remote.isChecked(),
            openai_base_url=self.oa_url.text().strip(),
            openai_model=self.oa_model.text().strip(),
            allow_cloud=self.cloud.isChecked(),
        )
        data["memory"].update(
            enabled=self.mem_on.isChecked(),
            ask_before_model_saves=self.mem_ask.isChecked(),
            share_with_cloud=self.mem_cloud.isChecked(),
        )
        data["tools"]["disabled"] = [n for n, b in self.tool_boxes.items() if not b.isChecked()]
        pron = {}
        for line in self.pron.toPlainText().splitlines():
            if "=" in line:
                k, _, val = line.partition("=")
                if k.strip() and val.strip():
                    pron[k.strip()] = val.strip()
        data["ui"].update(theme=self.theme.currentData(),
                          start_compact=self.start_compact.isChecked())
        data["persona"].update(
            user_name=self.user_name.text().strip() or None,
            tone=self.tone.text().strip() or self.s.persona.tone,
            response_length=self.length.currentText(),
            language=self.language.text().strip() or "English",
            pronunciations=pron,
            extra_instructions=self.extra.toPlainText().strip(),
        )
        data["web"].update(
            search_enabled=self.search_on.isChecked(),
            country=self.country.text().strip().upper(),
            max_results=self.results.value(),
            allow_page_reading=self.read_pages.isChecked(),
        )
        data["files"].update(
            enabled=self.files_on.isChecked(),
            folders=[n for n, b in self.folder_boxes.items() if b.isChecked()],
            allow_reading=self.files_read.isChecked(),
            allow_changes=self.files_change.isChecked(),
        )
        data["permissions"].update(
            auto_allow_open_apps=self.auto_apps.isChecked(),
            confirm_sensitive_apps=self.sensitive.isChecked(),
            confirm_close_apps=self.confirm_close.isChecked(),
            allowed_url_domains=[d.strip().lower() for d in self.domains.toPlainText().split()
                                 if d.strip()],
        )
        try:
            new = Settings.model_validate(data)
        except ValidationError as exc:
            QMessageBox.warning(self, "Chiku", f"Some settings are invalid:\n{exc}")
            return
        if new.llm.provider == "openai_compat" and not new.llm.openai_model:
            QMessageBox.warning(self, "Chiku", "Enter the model id for the OpenAI-compatible "
                                "server.")
            return
        if new.llm.provider == "openai_compat" and not is_loopback_url(new.llm.openai_base_url) \
                and not new.llm.allow_cloud:
            QMessageBox.warning(self, "Chiku", "That server is not on this computer. Tick "
                                "'Allow a cloud provider' to use it.")
            return
        if not is_loopback_url(new.llm.ollama_url) and not new.llm.allow_remote_ollama:
            QMessageBox.warning(self, "Chiku", "That Ollama address is not on this computer. "
                                "Tick 'Allow an Ollama server on another computer' to use it.")
            return
        save_settings(new, self.paths.settings_file)
        self.assistant.apply_settings(new)
        self.accept()

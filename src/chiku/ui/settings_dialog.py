"""Settings: audio, voice, AI model, persona, permissions & privacy."""

from __future__ import annotations

import os
import re
import threading

from pydantic import ValidationError
from PySide6.QtCore import QObject, Qt, Signal
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

from chiku import autostart, credentials, data_controls, models
from chiku.assistant import Assistant
from chiku.audio import devices
from chiku.config import ALL_FOLDERS, Settings, is_loopback_url, save_settings
from chiku.conversations import ConversationStore
from chiku.language import DEFAULT_VOICES
from chiku.llm.openai_compat import OPENAI_KEY_NAME
from chiku.paths import AppPaths
from chiku.wake import WAKE_MODEL
from chiku.web.search import BRAVE_KEY_NAME

_DEFAULT = "System default"
_FALLBACK_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "bf_emma",
                    "bf_isabella", "hf_alpha", "hf_beta"]


_HINDI_SAMPLE = "नमस्ते, मैं चिकू हूँ। मैं हिंदी में भी बात कर सकती हूँ।"
_LANGUAGE_MODES = [
    ("en", "English only"),
    ("auto", "Detect the language I speak (English, Hindi and more)"),
    ("hi", "Hindi only"),
]


class _Download(QObject):
    finished = Signal(str, bool)  # message, ok


def multilingual_model(model: str) -> str:
    """Multilingual Whisper of the same size: "small.en" -> "small"."""
    return model.removesuffix(".en")


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("notice")
    lab.setWordWrap(True)
    return lab


class SettingsDialog(QDialog):
    def __init__(self, assistant: Assistant, paths: AppPaths, parent: QWidget | None = None,
                 conversations: ConversationStore | None = None):
        super().__init__(parent)
        self.assistant = assistant
        self.paths = paths
        self.conversations = conversations
        self.s = assistant.settings
        self.setWindowTitle("Chiku settings")
        self.resize(520, 560)
        tabs = self.tabs = QTabWidget()
        tabs.addTab(self._audio_tab(), "Audio")
        tabs.addTab(self._voice_tab(), "Voice")
        tabs.addTab(self._language_tab(), "Languages")
        tabs.addTab(self._background_tab(), "Background")
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
                       "British English. Voices for other languages are on the Languages "
                       "tab. Generated on this computer."))
        return w

    def _language_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        stt = self.s.stt
        self.multi_name = multilingual_model(stt.model)
        self.lang_mode = QComboBox()
        for code, label in _LANGUAGE_MODES:
            self.lang_mode.addItem(label, code)
        current = "en" if stt.model.endswith(".en") else stt.language
        self.lang_mode.setCurrentIndex(max(0, self.lang_mode.findData(current)))
        f.addRow("Understand", self.lang_mode)
        self.lang_download = QPushButton(
            f"Download multilingual speech model ({self.multi_name}, "
            f"~{models.WHISPER_SIZES_MB.get(self.multi_name, 500)} MB)")
        self.lang_download.clicked.connect(self._download_multilingual)
        self.lang_status = _note("")
        f.addRow(self.lang_download)
        f.addRow(self.lang_status)
        self._dl = _Download()
        self._dl.finished.connect(self._downloaded)
        self._refresh_language_model()
        self.lang_en = QCheckBox("English")
        self.lang_hi = QCheckBox("Hindi")
        self.lang_en.setChecked("en" in stt.preferred_languages)
        self.lang_hi.setChecked("hi" in stt.preferred_languages)
        others = [c for c in stt.preferred_languages if c not in ("en", "hi")]
        self.lang_other = QLineEdit(", ".join(others))
        self.lang_other.setPlaceholderText("Other language codes, e.g. es, fr")
        row = QHBoxLayout()
        for widget in (self.lang_en, self.lang_hi, self.lang_other):
            row.addWidget(widget)
        f.addRow("Languages I speak", row)
        self.hindi_voice = QComboBox()
        self.hindi_voice.addItems(["hf_alpha", "hf_beta"])
        self.hindi_voice.setCurrentText(self.s.tts.voices.get("hi", DEFAULT_VOICES["hi"]))
        test = QPushButton("Test Hindi voice")
        test.clicked.connect(lambda: self.assistant.preview_voice(
            self.hindi_voice.currentText(), self.speed.value(), _HINDI_SAMPLE))
        hv = QHBoxLayout()
        hv.addWidget(self.hindi_voice, 1)
        hv.addWidget(test)
        f.addRow("Hindi voice", hv)
        self.language = QLineEdit(self.s.persona.language)
        f.addRow("Default reply language", self.language)
        f.addRow(_note(
            "Chiku replies in the language you use. She has voices for English, Hindi, "
            "Spanish, French, Italian, Portuguese, Japanese and Chinese; in other languages she "
            "replies in text. Detection happens on this computer. If it isn't sure which "
            "language you spoke, it picks the likeliest of the languages you ticked above."))
        return w

    def _refresh_language_model(self) -> None:
        have = models.whisper_present(self.paths.models_dir, self.multi_name)
        self.lang_download.setVisible(not have)
        model = self.lang_mode.model()
        for i in range(self.lang_mode.count()):
            if self.lang_mode.itemData(i) != "en":
                model.item(i).setEnabled(have)  # type: ignore[attr-defined]
        self.lang_status.setText("" if have else "Hindi and other languages need the "
                                 "multilingual speech model. Nothing is downloaded until you "
                                 "click the button.")

    def _download_multilingual(self) -> None:
        self.lang_download.setEnabled(False)
        self.lang_status.setText(f"Downloading Whisper {self.multi_name} from Hugging Face…")
        name, md, sig = self.multi_name, self.paths.models_dir, self._dl

        def work() -> None:
            try:
                models.download_whisper(name, md, lambda _m: None)
            except Exception as exc:  # network, disk
                sig.finished.emit(f"Download failed: {exc}", False)
                return
            sig.finished.emit("Downloaded. Choose “Detect the language I speak” and save.", True)

        threading.Thread(target=work, daemon=True, name="chiku-whisper-dl").start()

    def _downloaded(self, message: str, ok: bool) -> None:
        self.lang_download.setEnabled(True)
        if ok:
            self._refresh_language_model()
            self.lang_mode.setCurrentIndex(self.lang_mode.findData("auto"))
        self.lang_status.setText(message)

    def _language_settings(self, data: dict) -> None:
        stt = data["stt"]
        mode = self.lang_mode.currentData()
        if mode == "en" or not models.whisper_present(self.paths.models_dir, self.multi_name):
            stt.update(model=self.multi_name + ".en", language="en")
        else:
            stt.update(model=self.multi_name, language=mode)
        langs = [c for c, box in (("en", self.lang_en), ("hi", self.lang_hi)) if box.isChecked()]
        langs += [c.strip().lower() for c in re.split(r"[,\s]+", self.lang_other.text())
                  if re.fullmatch(r"[A-Za-z]{2,3}", c.strip())]
        stt["preferred_languages"] = list(dict.fromkeys(langs))[:8] or ["en"]
        data["tts"]["voices"] = {**data["tts"].get("voices", {}),
                                 "hi": self.hindi_voice.currentText()}

    def _background_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.wake_on = QCheckBox("Listen for “Hey Chiku” or “Wake up” while the mic is off")
        self.wake_on.setChecked(self.s.wake.enabled)
        f.addRow(self.wake_on)
        self.wake_download = QPushButton(
            f"Download the wake word model ({WAKE_MODEL}, "
            f"~{models.WHISPER_SIZES_MB[WAKE_MODEL]} MB)")
        self.wake_download.clicked.connect(self._download_wake)
        self.wake_status = _note("")
        f.addRow(self.wake_download)
        f.addRow(self.wake_status)
        self._wake_dl = _Download()
        self._wake_dl.finished.connect(self._wake_downloaded)
        self.follow_up = QSpinBox()
        self.follow_up.setRange(5, 300)
        self.follow_up.setSuffix(" s")
        self.follow_up.setValue(self.s.wake.follow_up_s)
        f.addRow("After waking, keep listening for", self.follow_up)
        self.autostart = QCheckBox("Start Chiku in the background when I sign in to Windows")
        self.autostart.setChecked(autostart.is_enabled())
        self.autostart.setEnabled(autostart.command() is not None)
        f.addRow(self.autostart)
        f.addRow(_note(
            "With the wake word on, the microphone stays open while Chiku runs; the tray icon "
            "shows a green dot. Speech is checked on this computer for the wake phrase and "
            "then discarded: it is never saved, logged or sent. Listening pauses while the "
            "screen is locked. Closing the window keeps Chiku in the tray; quit from the tray "
            "icon's menu."))
        self._refresh_wake_model()
        return w

    def _refresh_wake_model(self) -> None:
        have = models.whisper_present(self.paths.models_dir, WAKE_MODEL)
        self.wake_download.setVisible(not have)
        self.wake_on.setEnabled(have)
        if not have:
            self.wake_on.setChecked(False)
            self.wake_status.setText("The wake word needs a small English speech model. "
                                     "Nothing is downloaded until you click the button.")

    def _download_wake(self) -> None:
        self.wake_download.setEnabled(False)
        self.wake_status.setText(f"Downloading Whisper {WAKE_MODEL} from Hugging Face…")
        md, sig = self.paths.models_dir, self._wake_dl

        def work() -> None:
            try:
                models.download_whisper(WAKE_MODEL, md, lambda _m: None)
            except Exception as exc:  # network, disk
                sig.finished.emit(f"Download failed: {exc}", False)
                return
            sig.finished.emit("Downloaded. Tick the box above and save.", True)

        threading.Thread(target=work, daemon=True, name="chiku-wake-dl").start()

    def _wake_downloaded(self, message: str, ok: bool) -> None:
        self.wake_download.setEnabled(True)
        if ok:
            self._refresh_wake_model()
            self.wake_on.setChecked(True)
        self.wake_status.setText(message)

    def _apply_autostart(self) -> None:
        if self.autostart.isEnabled() and self.autostart.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.autostart.isChecked())
            except OSError as exc:
                QMessageBox.warning(self, "Chiku", f"Couldn't change the sign-in setting: {exc}")

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
        self.mini_mode = QComboBox()
        self.mini_mode.addItem("Notch at the top of the screen (drag it to any monitor)", "notch")
        self.mini_mode.addItem("Floating card", "card")
        self.mini_mode.setCurrentIndex(max(0, self.mini_mode.findData(self.s.ui.mini_mode)))
        f.addRow("Minimised Chiku", self.mini_mode)
        self.start_compact = QCheckBox("Start minimised")
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
        self.keep_history = QCheckBox("Keep conversation history on this computer (so chats "
                                      "are still there after Chiku restarts)")
        self.keep_history.setChecked(self.s.conversations.keep_history)
        for b in (self.mem_on, self.mem_ask, self.mem_cloud, self.keep_history):
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
            "Privacy: the microphone only runs while the Mic button is on, or while the wake "
            "word is on (Background tab; the tray icon shows a green dot). Audio is processed "
            "in memory and never saved. Speech recognition, the voice and the language model "
            "all run on this computer; only web searches (the query words) leave it. "
            "Conversations are not written to disk or logs unless you keep conversation "
            "history (above; turning it off deletes the saved file). Every action Chiku takes "
            "is recorded in a local action log. Deleting always goes to the Recycle Bin."
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
            "Delete all memories, the action history, saved conversations, your profile "
            "picture, logs and the current conversation? Settings, stored API keys and "
            "downloaded models are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        data_controls.wipe(self.assistant, self.paths, self.conversations)
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
        self._language_settings(data)
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
        data["conversations"]["keep_history"] = self.keep_history.isChecked()
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
                          start_compact=self.start_compact.isChecked(),
                          mini_mode=self.mini_mode.currentData())
        data["wake"].update(enabled=self.wake_on.isEnabled() and self.wake_on.isChecked(),
                            follow_up_s=self.follow_up.value())
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
        self._apply_autostart()
        if new.conversations.keep_history:
            if self.conversations is not None:
                self.conversations.save()
        elif self.conversations is not None:
            self.conversations.forget_saved()
        else:
            self.paths.conversations_file.unlink(missing_ok=True)
        self.accept()

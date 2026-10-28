"""Main window: waveform, state, transcript and controls, all driven by assistant events."""

from __future__ import annotations

import html
import threading
from typing import TypeVar
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from chiku.assistant import (
    ComponentStatus,
    Message,
    MicChanged,
    State,
    StateChanged,
    ToolActivity,
    TurnMetrics,
)
from chiku.bootstrap import build_assistant
from chiku.branding import AVATAR, WELCOME, asset
from chiku.config import Settings
from chiku.paths import AppPaths
from chiku.tools.web import UrlRejected, validate_url
from chiku.ui import theme
from chiku.ui.mascot import MascotWidget
from chiku.ui.waveform import Waveform

_STATE_TEXT = {
    State.LOADING: "Getting ready…",
    State.IDLE: "Mic is off. Turn it on or type below.",
    State.LISTENING: "Listening",
    State.HEARING: "Hearing you…",
    State.THINKING: "Thinking…",
    State.SPEAKING: "Speaking · talk or press Esc to interrupt",
    State.ERROR: "Something went wrong",
}
_W = TypeVar("_W", bound=QWidget)
_SHORT = {"microphone": "mic", "voice detection": "VAD", "speech recognition": "speech-to-text",
          "language model": "AI model"}
_COMPONENT_ORDER = ["microphone", "voice detection", "speech recognition", "language model",
                    "voice", "speaker"]


def _named(w: _W, name: str) -> _W:
    w.setObjectName(name)
    return w


def _label(text: str, name: str) -> QLabel:
    return _named(QLabel(text), name)


def _button(text: str, name: str) -> QPushButton:
    return _named(QPushButton(text), name)


class Bridge(QObject):
    """Carries events from assistant threads to the Qt thread (queued connections)."""

    assistant_event = Signal(object)
    confirm = Signal(str, str, object)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, paths: AppPaths):
        super().__init__()
        self.paths = paths
        self.setWindowTitle("Chiku")
        self.resize(460, 720)
        self.setMinimumSize(380, 520)

        self.bridge = Bridge()
        self.bridge.assistant_event.connect(self._on_event)
        self.bridge.confirm.connect(self._on_confirm)
        self.assistant = build_assistant(
            settings, paths, on_event=self.bridge.assistant_event.emit
        )
        self.assistant.confirm_handler = self._confirm_from_worker

        self._bubbles: dict[int, QLabel] = {}
        self._metas: dict[int, QLabel] = {}
        self._cols: dict[int, QVBoxLayout] = {}
        self._sourced: set[int] = set()
        self._components: dict[str, tuple[bool, str]] = {}
        self._build()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.assistant.start()

    # -- layout -------------------------------------------------------------------
    def _build(self) -> None:
        central = _named(QWidget(), "central")
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        avatar = QLabel()
        avatar.setPixmap(QPixmap(str(asset(AVATAR))).scaled(
            30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        avatar.setAccessibleName("Chiku")
        header.addWidget(avatar)
        header.addWidget(_label("chiku", "wordmark"))
        header.addStretch()
        self.badge = _label("", "badge")
        self._set_badge()
        self.badge.setToolTip("Where replies are generated. 'local' means on this computer.")
        header.addWidget(self.badge)
        for text, slot, tip in (
            ("Memory", self._open_memory, "What Chiku remembers about you"),
            ("Activity", self._open_activity, "Every action Chiku took"),
        ):
            b = _button(text, "ghost")
            b.setToolTip(tip)
            b.clicked.connect(slot)
            header.addWidget(b)
        self.settings_btn = _button("Settings", "ghost")
        self.settings_btn.clicked.connect(self._open_settings)
        header.addWidget(self.settings_btn)
        root.addLayout(header)

        self.mascot = MascotWidget(112)
        root.addWidget(self.mascot)
        self.wave = Waveform()
        self.wave.setMinimumHeight(40)
        self.wave.setMaximumHeight(48)
        root.addWidget(self.wave)
        self.state_label = _label(_STATE_TEXT[State.LOADING], "state")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setWordWrap(True)
        root.addWidget(self.state_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.transcript = QVBoxLayout(holder)
        self.transcript.setContentsMargins(0, 4, 4, 4)
        self.transcript.setSpacing(8)
        self.transcript.addStretch()
        self.welcome = self._welcome_card()
        self.transcript.insertWidget(0, self.welcome)
        self.scroll_area.setWidget(holder)
        self.scroll_area.verticalScrollBar().rangeChanged.connect(self._autoscroll)
        root.addWidget(self.scroll_area, 1)

        self.status = _label("", "status")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.mic_btn = _button("Mic", "mic")
        self.mic_btn.setCheckable(True)
        self.mic_btn.setToolTip("Turn the microphone on or off (Ctrl+M)")
        self.mic_btn.clicked.connect(self._toggle_mic)
        bottom.addWidget(self.mic_btn)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a message…")
        self.input.returnPressed.connect(self._send)
        bottom.addWidget(self.input, 1)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setToolTip("Stop talking / cancel (Esc)")
        self.stop_btn.clicked.connect(lambda: self.assistant.interrupt("stop button"))
        bottom.addWidget(self.stop_btn)
        root.addLayout(bottom)

        self.setCentralWidget(central)
        for keys, action in (("Ctrl+M", self.mic_btn.click),
                             ("Esc", lambda: self.assistant.interrupt("esc")),
                             ("Ctrl+,", self._open_settings)):
            QShortcut(QKeySequence(keys), self).activated.connect(action)

    def _set_badge(self) -> None:
        full = self.assistant.llm_label
        self.badge.setText(full if len(full) <= 24 else full[:23] + "…")
        self.badge.setToolTip(full + "\nWhere replies are generated. 'local' means on this "
                              "computer; CLOUD means a provider you allowed.")

    def _welcome_card(self) -> QWidget:
        card = _named(QWidget(), "welcome")
        lay = QVBoxLayout(card)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        img = QLabel()
        img.setPixmap(QPixmap(str(asset(WELCOME))).scaled(
            180, 180, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        img.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        img.setFixedSize(180, 180)
        lay.addWidget(img, 0, Qt.AlignmentFlag.AlignHCenter)
        hello = _label("Hi, I'm Chiku! Press Mic and talk to me, or type below.\n"
                       "Try: “open Notepad”, “what's the weather tomorrow?”, "
                       "“remember that I like jazz”.", "notice")
        hello.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hello.setWordWrap(True)
        lay.addWidget(hello)
        return card

    def maybe_onboard(self) -> None:
        from chiku.ui.onboarding import OnboardingWizard, needs_onboarding

        if not needs_onboarding(self.assistant.settings, self.paths):
            return
        wiz = OnboardingWizard(self.assistant.settings, self.paths, self)
        wiz.exec()
        if wiz.settings.onboarding_done:
            self.assistant.apply_settings(wiz.settings)
            self.assistant.reload()

    # -- actions --------------------------------------------------------------------
    def _toggle_mic(self) -> None:
        self.assistant.set_mic(self.mic_btn.isChecked())

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.assistant.submit_text(text)

    def _open_memory(self) -> None:
        from chiku.ui.memory_dialog import MemoryDialog

        if self.assistant.memory is not None:
            MemoryDialog(self.assistant.memory, self).exec()

    def _open_activity(self) -> None:
        from chiku.ui.activity_dialog import ActivityDialog

        audit = self.assistant._executor.audit  # noqa: SLF001
        if audit is not None:
            ActivityDialog(audit, self).exec()

    def _open_settings(self) -> None:
        from chiku.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.assistant, self.paths, self)
        if dlg.exec():
            self._set_badge()

    # -- per-frame UI updates -------------------------------------------------------------
    def _tick(self) -> None:
        inp, out = self.assistant.levels()
        self.mascot.update_state(self.assistant.state, out if out > 0 else inp)
        if out > 0:
            self.wave.push(out, "output")
        elif self.assistant.mic_active:
            self.wave.push(inp, "input")
        else:
            self.wave.push(0.0, "idle")
        live = self.assistant.mic_active
        if self.mic_btn.isChecked() != live and not self._mic_starting():
            self.mic_btn.setChecked(live)

    def _mic_starting(self) -> bool:
        # The button may be on while the stream is opening; don't flip it back during that.
        return self.mic_btn.isChecked() and self.assistant._mic_on  # noqa: SLF001

    # -- events from the assistant ----------------------------------------------------------
    @Slot(object)
    def _on_event(self, ev: object) -> None:
        if isinstance(ev, StateChanged):
            text = _STATE_TEXT[ev.state]
            if ev.detail and ev.state in (State.LOADING, State.THINKING, State.ERROR):
                text = ev.detail[0].upper() + ev.detail[1:] + ("…" if ev.state is not State.ERROR
                                                               else "")
            self.state_label.setText(text)
            self.state_label.setProperty("error", ev.state is State.ERROR)
            self.state_label.style().unpolish(self.state_label)
            self.state_label.style().polish(self.state_label)
        elif isinstance(ev, Message):
            self._show_message(ev)
        elif isinstance(ev, ToolActivity):
            if ev.status != "running":
                mark = "✓" if ev.status == "ok" else "✕"
                self._add_line(f"{mark} {ev.name} · {ev.message}", "tool")
        elif isinstance(ev, ComponentStatus):
            self._components[ev.component] = (ev.ok, ev.message)
            self._render_status()
            if ev.component == "language model":
                self._set_badge()
        elif isinstance(ev, MicChanged):
            self.mic_btn.setChecked(ev.on)
        elif isinstance(ev, TurnMetrics):
            v = ev.values
            if "end_of_speech_to_audio_s" in v:
                self.status.setToolTip(
                    f"Last turn: {v['end_of_speech_to_audio_s']:.2f}s from end of speech to voice"
                )

    def _render_status(self) -> None:
        parts = []
        tips = []
        for name in _COMPONENT_ORDER:
            if name in self._components:
                ok, msg = self._components[name]
                parts.append(f"{_SHORT.get(name, name)} {'✓' if ok else '✕'}")
                tips.append(f"{name}: {msg}")
        bad = [f"{n}: {m}" for n, (ok, m) in self._components.items() if not ok]
        self.status.setText("   ".join(parts) + ("\n" + bad[-1] if bad else ""))
        self.status.setToolTip("\n".join(tips))

    def _show_message(self, ev: Message) -> None:
        if self.welcome.isVisible() and ev.role in ("user", "assistant"):
            self.welcome.hide()
        if ev.role == "notice":
            self._add_line(ev.text, "notice")
            return
        label = self._bubbles.get(ev.id)
        if label is None:
            label = _label("", "userBubble" if ev.role == "user" else "assistantText")
            label.setTextFormat(Qt.TextFormat.PlainText)  # model output is never rendered as HTML
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            row = QHBoxLayout()
            if ev.role == "user":
                row.addStretch()
                label.setMaximumWidth(int(self.scroll_area.width() * 0.8))
                row.addWidget(label)
            else:
                col = QVBoxLayout()
                col.setSpacing(2)
                col.addWidget(label)
                meta = _label(ev.source, "meta")
                col.addWidget(meta)
                self._metas[ev.id] = meta
                self._cols[ev.id] = col
                row.addLayout(col, 1)
            self.transcript.insertLayout(self.transcript.count() - 1, row)
            self._bubbles[ev.id] = label
        label.setText(ev.text or "…")
        if ev.final and ev.sources and ev.id in self._cols and ev.id not in self._sourced:
            self._sourced.add(ev.id)
            self._metas[ev.id].setText(f"{ev.source} · with web sources")
            self._cols[ev.id].insertWidget(1, self._sources_label(ev.sources))

    def _sources_label(self, sources: list[dict[str, str]]) -> QLabel:
        """Numbered source links. Text is escaped; only validated http(s) URLs become links,
        and clicking goes through the permissioned open_url tool, not straight to a browser."""
        rows: list[str] = []
        for s in sources[:8]:
            try:
                url = validate_url(s.get("url", ""))
            except UrlRejected:
                continue
            host = urlsplit(url).hostname or ""
            title = html.escape(s.get("title", host)[:90])
            rows.append(f'{len(rows) + 1}. <a href="{html.escape(url, quote=True)}" '
                        f'style="color:{theme.TEXT}; text-decoration:none">{title}</a> '
                        f'<span style="color:{theme.FAINT}">· {html.escape(host)}</span>')
        lab = _label("<br>".join(rows), "sources")
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setWordWrap(True)
        lab.setOpenExternalLinks(False)
        lab.setToolTip("Sources. Click to open in your browser.")
        lab.linkActivated.connect(self.assistant.open_link)
        return lab

    def _add_line(self, text: str, kind: str) -> None:
        label = _label(text, kind)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        self.transcript.insertWidget(self.transcript.count() - 1, label)

    def _autoscroll(self, _min: int, maximum: int) -> None:
        self.scroll_area.verticalScrollBar().setValue(maximum)

    # -- confirmation prompts (called from a worker thread) ------------------------------------
    def _confirm_from_worker(self, name: str, description: str) -> bool:
        box: dict[str, object] = {"done": threading.Event(), "ok": False}
        self.bridge.confirm.emit(name, description, box)
        done = box["done"]
        assert isinstance(done, threading.Event)
        if not done.wait(timeout=120):
            return False
        return bool(box["ok"])

    @Slot(str, str, object)
    def _on_confirm(self, name: str, description: str, box: dict) -> None:
        self.raise_()
        self.activateWindow()
        answer = QMessageBox.question(
            self, "Chiku needs your OK",
            f"{description}\n\n(action: {name})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        box["ok"] = answer == QMessageBox.StandardButton.Yes
        box["done"].set()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self._timer.stop()
        self.assistant.shutdown()
        super().closeEvent(event)


__all__ = ["MainWindow", "theme"]

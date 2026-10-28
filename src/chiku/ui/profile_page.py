"""Profile page: who you are to Chiku, how she talks to you, what she remembers, what she has
done for you, and your data. Everything here stays on this computer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from chiku import data_controls
from chiku.assistant import Assistant
from chiku.audit import usage_stats
from chiku.config import Settings, save_settings
from chiku.conversations import ConversationStore
from chiku.language import DEFAULT_VOICES
from chiku.language import name as language_name
from chiku.memory import CATEGORIES
from chiku.paths import AppPaths
from chiku.ui import theme
from chiku.ui.components import button, label, named

_CATEGORY_NAMES = {"preference": "Preferences", "fact": "Facts", "person": "People",
                   "other": "Other"}
_ENGLISH_VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "bf_emma",
                   "bf_isabella"]
AVATAR_PX = 84


def circle_pixmap(image: QImage, size: int) -> QPixmap:
    """The middle square of `image`, scaled and cut into a circle."""
    side = min(image.width(), image.height())
    square = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    scaled = QPixmap.fromImage(square).scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(path)
    p.drawPixmap(0, 0, scaled)
    p.end()
    return out


def initials_pixmap(name: str, size: int) -> QPixmap:
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ACCENT))
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor(theme.ACCENT_TEXT))
    font = QFont()
    font.setPointSize(int(size / 3.2))
    font.setBold(True)
    p.setFont(font)
    letters = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letters)
    p.end()
    return out


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = named(QFrame(), "card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 12, 16, 14)
    lay.addWidget(label(title, "sectionTitle"))
    return card, lay


class ProfilePage(QScrollArea):
    open_page = Signal(str)  # "memory", or "settings:<tab>"
    saved = Signal()

    def __init__(self, assistant: Assistant, paths: AppPaths,
                 conversations: ConversationStore | None = None,
                 ask: Callable[[QWidget, str], bool] | None = None):
        super().__init__()
        self.assistant = assistant
        self.paths = paths
        self.conversations = conversations
        self._ask = ask or _ask_yes_no
        self.setWidgetResizable(True)
        body = named(QWidget(), "page")
        self.col = QVBoxLayout(body)
        self.col.setContentsMargins(20, 16, 20, 20)
        self.col.setSpacing(12)
        self.setWidget(body)
        top = QHBoxLayout()
        top.addWidget(label("Profile", "pageTitle"), 1)
        self.status = label("", "status")
        top.addWidget(self.status)
        save = button("Save", "primary")
        save.clicked.connect(self.save)
        top.addWidget(save)
        self.col.addLayout(top)
        self._build_you()
        self._build_style()
        self._build_memories()
        self._build_usage()
        self._build_data()
        self.col.addStretch()
        self.refresh()

    # -- sections ---------------------------------------------------------------------------
    def _build_you(self) -> None:
        p = self.assistant.settings.persona
        card, lay = _card("You")
        row = QHBoxLayout()
        self.avatar = QLabel()
        self.avatar.setFixedSize(AVATAR_PX, AVATAR_PX)
        row.addWidget(self.avatar)
        form = QFormLayout()
        self.name = QLineEdit(p.user_name or "")
        self.name.setPlaceholderText("What should Chiku call you?")
        self.name.textChanged.connect(lambda _t: self._show_avatar())
        form.addRow("Name", self.name)
        self.say_as = QLineEdit(p.pronunciations.get(p.user_name or "", ""))
        self.say_as.setPlaceholderText("How to say it, e.g. Juh-luj (optional)")
        hear = button("Hear it", "ghost")
        hear.clicked.connect(self._hear_name)
        say_row = QHBoxLayout()
        say_row.addWidget(self.say_as, 1)
        say_row.addWidget(hear)
        form.addRow("Pronounced", say_row)
        pics = QHBoxLayout()
        choose = button("Choose picture…", "ghost")
        choose.clicked.connect(self._choose_avatar)
        remove = button("Remove picture", "ghost")
        remove.clicked.connect(self._remove_avatar)
        pics.addWidget(choose)
        pics.addWidget(remove)
        pics.addStretch()
        form.addRow("", pics)
        row.addLayout(form, 1)
        lay.addLayout(row)
        self.col.addWidget(card)

    def _build_style(self) -> None:
        s = self.assistant.settings
        p = s.persona
        card, lay = _card("How Chiku talks with you")
        form = QFormLayout()
        self.interests = QLineEdit(", ".join(p.interests))
        self.interests.setPlaceholderText("e.g. cricket, cooking, Python, astronomy")
        form.addRow("Interests", self.interests)
        self.length = QComboBox()
        for key, text in (("brief", "Brief"), ("balanced", "Balanced"), ("detailed", "Detailed")):
            self.length.addItem(text, key)
        self.length.setCurrentIndex(max(0, self.length.findData(p.response_length)))
        form.addRow("Answers", self.length)
        self.tone = QLineEdit(p.tone)
        form.addRow("Tone", self.tone)
        self.language = QLineEdit(p.language)
        form.addRow("Default reply language", self.language)
        lang_row = QHBoxLayout()
        self.speaks = label(", ".join(language_name(c) for c in s.stt.preferred_languages), "")
        lang_row.addWidget(self.speaks, 1)
        change = button("Change…", "ghost")
        change.setToolTip("Settings → Languages")
        change.clicked.connect(lambda: self.open_page.emit("settings:Languages"))
        lang_row.addWidget(change)
        form.addRow("Languages you speak", lang_row)
        self.voice = QComboBox()
        voices = [v for v in self.assistant.available_voices() if v[:2] in ("af", "bf")]
        self.voice.addItems(voices or _ENGLISH_VOICES)
        self.voice.setCurrentText(s.tts.voice)
        self.hindi_voice = QComboBox()
        self.hindi_voice.addItems(["hf_alpha", "hf_beta"])
        self.hindi_voice.setCurrentText(s.tts.voices.get("hi", DEFAULT_VOICES["hi"]))
        form.addRow("English voice", self.voice)
        form.addRow("Hindi voice", self.hindi_voice)
        lay.addLayout(form)
        self.col.addWidget(card)

    def _build_memories(self) -> None:
        card, lay = _card("What Chiku remembers")
        self.memories = label("", "", wrap=True)
        self.memories.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.memories)
        manage = button("Manage memories", "ghost")
        manage.clicked.connect(lambda: self.open_page.emit("memory"))
        lay.addWidget(manage, 0, Qt.AlignmentFlag.AlignLeft)
        self.col.addWidget(card)

    def _build_usage(self) -> None:
        card, lay = _card("What Chiku has done for you")
        self.usage_grid = QGridLayout()
        self.usage_grid.setHorizontalSpacing(24)
        lay.addLayout(self.usage_grid)
        self.top_apps = label("", "status", wrap=True)
        lay.addWidget(self.top_apps)
        lay.addWidget(label("Counted from the local action log; nothing else is tracked.",
                            "status"))
        self.col.addWidget(card)

    def _build_data(self) -> None:
        card, lay = _card("Your data")
        lay.addWidget(label("Settings, memories, the action log and (if you keep it) chat "
                            "history are stored only on this computer.", "notice", wrap=True))
        row = QHBoxLayout()
        export = button("Export my data…")
        export.clicked.connect(self._export)
        delete = button("Delete my data…")
        delete.clicked.connect(self._delete)
        row.addWidget(export)
        row.addWidget(delete)
        row.addStretch()
        lay.addLayout(row)
        self.col.addWidget(card)

    # -- live data ----------------------------------------------------------------------------
    def refresh(self) -> None:
        self._show_avatar()
        store = self.assistant.memory
        items = store.all() if store is not None else []
        if not items:
            self.memories.setText("Nothing yet. Say “remember that …” and it shows up here.")
        else:
            parts = []
            for cat in CATEGORIES:
                texts = [m.text for m in items if m.category == cat]
                if texts:
                    more = f"\n  … and {len(texts) - 5} more" if len(texts) > 5 else ""
                    parts.append(f"{_CATEGORY_NAMES[cat]} ({len(texts)})\n"
                                 + "\n".join(f"  • {t}" for t in texts[:5]) + more)
            self.memories.setText("\n\n".join(parts))
        audit = self.assistant._executor.audit  # noqa: SLF001
        stats = usage_stats(audit.all() if audit is not None else [])
        cells = [("Actions", stats.actions), ("This week", stats.last_7_days),
                 ("Apps opened", stats.apps_opened), ("Web searches", stats.searches),
                 ("Pages read", stats.pages_read), ("File actions", stats.file_actions),
                 ("Apps installed", stats.apps_installed), ("Apps updated", stats.apps_updated),
                 ("Memories saved", stats.memories_saved)]
        while self.usage_grid.count():
            item = self.usage_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for i, (text, n) in enumerate(cells):
            holder = QWidget()
            box = QVBoxLayout(holder)
            box.setContentsMargins(0, 4, 0, 4)
            box.addWidget(label(str(n), "statNumber"))
            box.addWidget(label(text, "status"))
            self.usage_grid.addWidget(holder, i // 3, i % 3)
        self.stats = stats
        tops = ", ".join(f"{n} ({c})" for n, c in stats.top_apps)
        since = f"Since {stats.since}. " if stats.since else ""
        self.top_apps.setText(since + (f"Most opened: {tops}." if tops else ""))

    def _show_avatar(self) -> None:
        f = self.paths.avatar_file
        img = QImage(str(f)) if f.exists() else QImage()
        self.avatar.setPixmap(circle_pixmap(img, AVATAR_PX) if not img.isNull()
                              else initials_pixmap(self.name.text(), AVATAR_PX))

    # -- actions --------------------------------------------------------------------------------
    def _hear_name(self) -> None:
        spoken = self.say_as.text().strip() or self.name.text().strip()
        if spoken:
            self.assistant.preview_voice(self.voice.currentText(),
                                         self.assistant.settings.tts.speed,
                                         f"Hi {spoken}! Nice to see you.")

    def _choose_avatar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a picture", str(Path.home()),
                                              "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.set_avatar(Path(path))

    def set_avatar(self, source: Path) -> bool:
        img = QImage(str(source))
        if img.isNull():
            self.status.setText("That file isn't a picture Chiku can read.")
            return False
        side = min(img.width(), img.height())
        square = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
        self.paths.avatar_file.parent.mkdir(parents=True, exist_ok=True)
        square.scaled(256, 256, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation).save(
            str(self.paths.avatar_file))  # PNG, from the file name
        self._show_avatar()
        return True

    def _remove_avatar(self) -> None:
        self.paths.avatar_file.unlink(missing_ok=True)
        self._show_avatar()

    def save(self) -> bool:
        data = self.assistant.settings.model_dump()
        persona = data["persona"]
        old_name = persona.get("user_name") or ""
        name = self.name.text().strip()
        pron = dict(persona.get("pronunciations", {}))
        pron.pop(old_name, None)
        if name and self.say_as.text().strip():
            pron[name] = self.say_as.text().strip()
        persona.update(
            user_name=name or None,
            pronunciations=pron,
            interests=[i.strip() for i in self.interests.text().split(",") if i.strip()][:20],
            response_length=self.length.currentData(),
            tone=self.tone.text().strip() or persona["tone"],
            language=self.language.text().strip() or "English",
        )
        data["tts"]["voice"] = self.voice.currentText()
        data["tts"]["voices"] = {**data["tts"].get("voices", {}),
                                 "hi": self.hindi_voice.currentText()}
        try:
            new = Settings.model_validate(data)
        except ValidationError as exc:
            self.status.setText(f"Not saved: {exc.errors()[0]['msg']}")
            return False
        save_settings(new, self.paths.settings_file)
        self.assistant.apply_settings(new)
        self.status.setText("Saved.")
        self.saved.emit()
        return True

    def export_to(self, path: Path) -> None:
        data_controls.write_export(path, data_controls.export_all(self.assistant,
                                                                  self.conversations))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export my Chiku data",
                                              str(Path.home() / "chiku-data.json"),
                                              "JSON (*.json)")
        if path:
            try:
                self.export_to(Path(path))
            except OSError as exc:
                self.status.setText(f"Export failed: {exc}")
                return
            self.status.setText(f"Exported to {path}")

    def _delete(self) -> None:
        if not self._ask(self, "Delete your memories, action history, saved conversations, "
                         "profile picture and logs? Settings, API keys and downloaded models "
                         "are kept. This can't be undone."):
            return
        data_controls.wipe(self.assistant, self.paths, self.conversations)
        self.refresh()
        self.status.setText("Your data was deleted.")


def _ask_yes_no(parent: QWidget, text: str) -> bool:
    return QMessageBox.question(
        parent, "Chiku", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

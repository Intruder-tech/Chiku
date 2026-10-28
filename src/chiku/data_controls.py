"""Export or delete the user's personal data (Profile page and Settings → Privacy)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chiku import __version__
from chiku.audit import usage_stats

if TYPE_CHECKING:
    from chiku.assistant import Assistant
    from chiku.conversations import ConversationStore
    from chiku.paths import AppPaths


def export_all(assistant: Assistant, conversations: ConversationStore | None) -> dict[str, Any]:
    """Everything Chiku keeps about the user, as one JSON-ready dict. API keys are not
    included: they live in Windows Credential Manager, never in settings."""
    memory = assistant.memory
    audit = assistant._executor.audit  # noqa: SLF001
    return {
        "exported": datetime.now().isoformat(timespec="seconds"),
        "app": f"Chiku {__version__}",
        "settings": assistant.settings.model_dump(mode="json"),
        "memories": [asdict(m) for m in memory.all()] if memory is not None else [],
        "conversations": conversations.export() if conversations is not None else [],
        "usage": asdict(usage_stats(audit.all())) if audit is not None else {},
    }


def write_export(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def wipe(assistant: Assistant, paths: AppPaths,
         conversations: ConversationStore | None = None) -> None:
    """Delete memories, the action history, saved conversations, the profile picture and old
    logs, and forget the current conversation. Settings, API keys and models are kept."""
    assistant.clear_history()
    if assistant.memory is not None:
        assistant.memory.clear()
    audit = assistant._executor.audit  # noqa: SLF001
    if audit is not None:
        audit.clear()
    if conversations is not None:
        conversations.clear_all()
    else:
        paths.conversations_file.unlink(missing_ok=True)
    paths.avatar_file.unlink(missing_ok=True)
    for f in paths.logs_dir.glob("chiku.log.*"):
        f.unlink(missing_ok=True)

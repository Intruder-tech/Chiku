"""Filesystem locations used by Chiku.

Everything lives under the user's profile. Models and logs go to LOCALAPPDATA
(not roamed, not synced); settings go to APPDATA. Set CHIKU_HOME to relocate
everything into one directory (used by tests and portable installs).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    data_dir: Path

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def memory_file(self) -> Path:
        return self.data_dir / "memory.db"

    @property
    def conversations_file(self) -> Path:  # only written if the user keeps chat history
        return self.data_dir / "conversations.json"

    @property
    def avatar_file(self) -> Path:  # the user's profile picture, if they chose one
        return self.data_dir / "avatar.png"

    @property
    def audit_file(self) -> Path:
        return self.data_dir / "audit" / "actions.jsonl"

    def ensure(self) -> None:
        for d in (self.config_dir, self.models_dir, self.logs_dir, self.audit_file.parent):
            d.mkdir(parents=True, exist_ok=True)


def default_paths() -> AppPaths:
    home = os.environ.get("CHIKU_HOME")
    if home:
        root = Path(home)
        return AppPaths(config_dir=root / "config", data_dir=root / "data")
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return AppPaths(config_dir=roaming / "Chiku", data_dir=local / "Chiku")


def cli_command() -> str:
    """How to run the command-line tool in this installation."""
    return "chiku-cli.exe" if getattr(sys, "frozen", False) else "python -m chiku"

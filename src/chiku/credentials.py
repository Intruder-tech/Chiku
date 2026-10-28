"""API keys live in Windows Credential Manager (via keyring), never in files or logs.

Phase 1 runs fully local and stores no credentials. Future cloud LLM or web
search adapters read their keys through this module only.
"""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "Chiku"


class CredentialError(RuntimeError):
    pass


def get_key(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, name)
    except KeyringError as exc:
        raise CredentialError(f"Credential store unavailable: {exc}") from exc


def set_key(name: str, value: str) -> None:
    if not value.strip():
        raise CredentialError("refusing to store an empty key")
    try:
        keyring.set_password(SERVICE, name, value.strip())
    except KeyringError as exc:
        raise CredentialError(f"Credential store unavailable: {exc}") from exc


def delete_key(name: str) -> bool:
    try:
        keyring.delete_password(SERVICE, name)
        return True
    except PasswordDeleteError:
        return False


def has_key(name: str) -> bool:
    return bool(get_key(name))

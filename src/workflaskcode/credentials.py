"""API key storage. Lookup order: environment, OS keyring (optional), credentials file."""

from __future__ import annotations

import json
import os
import re
from typing import Iterable

from .paths import AgentHome
from .utils import atomic_write

SERVICE = "workflaskcode"
ENV_VARS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
_SECRET_PATTERNS = [
    re.compile(p)
    for p in (
        r"sk-or-v1-[A-Za-z0-9]{8,}",
        r"sk-ant-[A-Za-z0-9_\-]{8,}",
        r"sk-[A-Za-z0-9_\-]{16,}",
        r"AKIA[0-9A-Z]{16}",
        r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}",
    )
]


class CredentialError(Exception):
    """A credential operation could not be completed."""


def mask(secret: str | None) -> str:
    if not secret:
        return "(not set)"
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def redact(text: str, known: Iterable[str] = ()) -> str:
    """Remove known secrets and anything shaped like an API key from text."""
    for secret in known:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _keyring():
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return None
    return keyring


class CredentialStore:
    def __init__(self, home: AgentHome) -> None:
        self.home = home

    @staticmethod
    def env_var_for(provider: str) -> str:
        return ENV_VARS.get(provider) or f"WORKFLASKCODE_KEY_{provider.upper().replace('-', '_')}"

    def _read_file(self) -> dict[str, str]:
        path = self.home.credentials_file
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialError(f"credentials file unreadable: {exc}") from exc
        return {str(k): str(v) for k, v in data.items()}

    def _write_file(self, data: dict[str, str]) -> None:
        self.home.ensure()
        atomic_write(self.home.credentials_file, json.dumps(data, indent=2), mode=0o600)

    def get(self, provider: str) -> str | None:
        env_value = os.environ.get(self.env_var_for(provider))
        if env_value:
            return env_value.strip()
        keyring = _keyring()
        if keyring is not None:
            try:
                value = keyring.get_password(SERVICE, provider)
            except Exception:  # keyring backends raise many types
                value = None
            if value:
                return value
        return self._read_file().get(provider)

    def source(self, provider: str) -> str:
        if os.environ.get(self.env_var_for(provider)):
            return "environment variable"
        keyring = _keyring()
        if keyring is not None:
            try:
                if keyring.get_password(SERVICE, provider):
                    return "OS keyring"
            except Exception:
                pass
        if provider in self._read_file():
            return "credentials file (plain text, 0600)"
        return "not configured"

    def set(self, provider: str, key: str, backend: str = "file") -> str:
        """Store a key. The file backend must only be used after explicit user consent."""
        if backend == "keyring":
            keyring = _keyring()
            if keyring is None:
                raise CredentialError("the 'keyring' package is not installed")
            keyring.set_password(SERVICE, provider, key)
            return "OS keyring"
        if backend != "file":
            raise CredentialError(f"unknown credential backend: {backend}")
        data = self._read_file()
        data[provider] = key
        self._write_file(data)
        return "credentials file"

    def delete(self, provider: str) -> bool:
        removed = False
        data = self._read_file()
        if data.pop(provider, None) is not None:
            self._write_file(data)
            removed = True
        keyring = _keyring()
        if keyring is not None:
            try:
                keyring.delete_password(SERVICE, provider)
                removed = True
            except Exception:
                pass
        return removed

    def known_secrets(self) -> list[str]:
        values = []
        for provider in self._read_file():
            value = self.get(provider)
            if value:
                values.append(value)
        return values


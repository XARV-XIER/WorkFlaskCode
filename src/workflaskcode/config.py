"""Validated TOML configuration. API keys never live here."""

from __future__ import annotations

import copy
import re
import tomllib
from typing import Any

from .paths import AgentHome
from .utils import atomic_write


class ConfigError(Exception):
    """Configuration is malformed or holds an invalid value."""


PERMISSION_MODES = ("read_only", "approve_all", "approve_risky", "restricted_auto")
DEFAULT_MODES = ("chat", "coding")
PROVIDER_KINDS = ("openrouter", "openai", "anthropic", "openai_compatible")
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {
        "default_provider": "",
        "coding_model": "",
        "chat_model": "",
        "fallback_models": [],
        "permission_mode": "approve_risky",
        "default_mode": "coding",
        "setup_complete": False,
    },
    "limits": {
        "max_steps": 25,
        "max_retries": 3,
        "request_timeout_s": 120,
        "command_timeout_s": 120,
        "context_budget_chars": 120000,
        "max_btw_concurrency": 2,
        "max_estimated_cost_usd": 0.0,
    },
    "git": {"enabled": True},
    "privacy": {"btw_sees_main_history": False},
    "providers": {},
}

_INT_LIMITS = (
    "max_steps", "max_retries", "request_timeout_s", "command_timeout_s",
    "context_budget_chars", "max_btw_concurrency",
)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate(data: dict[str, Any]) -> None:
    general = data["general"]
    if general["permission_mode"] not in PERMISSION_MODES:
        raise ConfigError(f"permission_mode must be one of {', '.join(PERMISSION_MODES)}")
    if general["default_mode"] not in DEFAULT_MODES:
        raise ConfigError(f"default_mode must be one of {', '.join(DEFAULT_MODES)}")
    fallbacks = general["fallback_models"]
    if not isinstance(fallbacks, list) or not all(isinstance(x, str) for x in fallbacks):
        raise ConfigError("fallback_models must be a list of strings")
    for key in ("default_provider", "coding_model", "chat_model"):
        if not isinstance(general[key], str):
            raise ConfigError(f"{key} must be a string")
    if not isinstance(general["setup_complete"], bool):
        raise ConfigError("setup_complete must be true or false")
    limits = data["limits"]
    for key in _INT_LIMITS:
        value = limits[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"limits.{key} must be a non-negative integer")
    cost = limits["max_estimated_cost_usd"]
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
        raise ConfigError("limits.max_estimated_cost_usd must be a non-negative number")
    for name, entry in data["providers"].items():
        if not _KEY_RE.match(name):
            raise ConfigError(f"invalid provider name '{name}'")
        if not isinstance(entry, dict):
            raise ConfigError(f"provider '{name}' must be a table")
        if entry.get("kind") not in PROVIDER_KINDS:
            raise ConfigError(f"provider '{name}' has unknown kind {entry.get('kind')!r}")
        if entry["kind"] == "openai_compatible" and not str(entry.get("base_url", "")).startswith(
            ("http://", "https://")
        ):
            raise ConfigError(f"provider '{name}' needs a base_url starting with http(s)://")
        if "model" in entry and not isinstance(entry["model"], str):
            raise ConfigError(f"provider '{name}' model must be a string")


def load_config(home: AgentHome) -> dict[str, Any]:
    data = copy.deepcopy(DEFAULT_CONFIG)
    path = home.config_file
    if path.exists():
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config.toml is malformed: {exc}") from exc
        data = deep_merge(data, raw)
    validate(data)
    return data


def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_fmt_value(v) for v in value) + "]"
    raise ConfigError(f"unsupported configuration value: {value!r}")


def _fmt_key(key: str) -> str:
    return key if _KEY_RE.match(key) else '"' + key.replace('"', '\\"') + '"'


def toml_dumps(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def walk(table: dict[str, Any], path: list[str]) -> None:
        scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
        tables = [(k, v) for k, v in table.items() if isinstance(v, dict)]
        if path and (scalars or not tables):
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("[" + ".".join(_fmt_key(p) for p in path) + "]")
        for key, value in scalars:
            lines.append(f"{_fmt_key(key)} = {_fmt_value(value)}")
        for key, value in tables:
            walk(value, path + [key])

    walk(data, [])
    return "\n".join(lines).strip() + "\n"


def _coerce(raw: str, current: Any) -> Any:
    text = raw.strip()
    if isinstance(current, bool):
        if text.lower() in ("1", "true", "yes", "on"):
            return True
        if text.lower() in ("0", "false", "no", "off"):
            return False
        raise ConfigError("expected true or false")
    if isinstance(current, int):
        try:
            return int(text)
        except ValueError as exc:
            raise ConfigError("expected an integer") from exc
    if isinstance(current, float):
        try:
            return float(text)
        except ValueError as exc:
            raise ConfigError("expected a number") from exc
    if isinstance(current, list):
        return [item.strip() for item in text.split(",") if item.strip()]
    return text


class ConfigStore:
    """Read/modify/save the configuration file."""

    def __init__(self, home: AgentHome) -> None:
        self.home = home
        self.data = load_config(home)

    def save(self) -> None:
        validate(self.data)
        self.home.ensure()
        atomic_write(self.home.config_file, toml_dumps(self.data), mode=0o600)

    def providers(self) -> dict[str, dict[str, Any]]:
        return self.data["providers"]

    def set_provider(
        self, name: str, kind: str, model: str = "", base_url: str = "", enabled: bool = True
    ) -> None:
        entry: dict[str, Any] = {"kind": kind, "model": model, "enabled": enabled}
        if base_url:
            entry["base_url"] = base_url
        self.data["providers"][name] = entry
        validate(self.data)

    def remove_provider(self, name: str) -> bool:
        return self.data["providers"].pop(name, None) is not None

    def get(self, dotted: str) -> Any:
        current: Any = self.data
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                raise ConfigError(f"unknown setting: {dotted}")
            current = current[part]
        return current

    def set(self, dotted: str, raw: str) -> Any:
        current = self.get(dotted)
        value = _coerce(raw, current)
        parent: Any = self.data
        parts = dotted.split(".")
        for part in parts[:-1]:
            parent = parent[part]
        parent[parts[-1]] = value
        validate(self.data)
        return value

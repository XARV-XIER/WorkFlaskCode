"""Locations for the agent home directory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "WorkFlaskCode"


def default_home() -> Path:
    """Return the platform-appropriate agent home (overridable with WORKFLASKCODE_HOME)."""
    override = os.environ.get("WORKFLASKCODE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / APP_DIR_NAME
    return Path.home() / ".workflaskcode"


class AgentHome:
    """Owns every path under the agent home directory. Separate from project roots."""

    SUBDIRS = (
        "providers", "projects", "sessions", "memory", "logs",
        "cache", "templates", "reports", "backups", "state",
    )

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else default_home()

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    @property
    def credentials_file(self) -> Path:
        return self.root / "credentials.json"

    @property
    def db_path(self) -> Path:
        return self.root / "state" / "agent.db"

    @property
    def history_file(self) -> Path:
        return self.root / "state" / "prompt_history"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def command_log(self) -> Path:
        return self.root / "logs" / "commands.jsonl"

    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in self.SUBDIRS:
            (self.root / name).mkdir(exist_ok=True)
        if os.name == "posix":
            os.chmod(self.root, 0o700)

    def is_configured(self) -> bool:
        return self.config_file.exists()

"""File checkpoints: back up what the agent changes so its own work can be undone safely."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .state import StateStore
from .utils import atomic_write, file_sha256, new_id, now_iso


@dataclass
class UndoReport:
    restored: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


class CheckpointManager:
    """Records the pre-change content of each file the agent writes.

    Undo restores only files the agent last wrote and still matches what it wrote.
    A file the user has edited since is reported as a conflict and left alone.
    """

    def __init__(self, state: StateStore, backups_dir: Path, session_id: str, root: Path) -> None:
        self.state = state
        self.backups_dir = backups_dir
        self.session_id = session_id
        self.root = root
        self.current: dict | None = None

    def begin(self, label: str, task_id: str | None = None) -> str:
        self.current = {
            "id": new_id("cp-"),
            "session_id": self.session_id,
            "task_id": task_id,
            "label": label[:120] or "checkpoint",
            "manifest": [],
            "status": "open",
            "created_at": now_iso(),
        }
        return self.current["id"]

    def _ensure_current(self) -> dict:
        if self.current is None:
            self.begin("auto checkpoint")
        assert self.current is not None
        return self.current

    def _persist(self) -> None:
        if self.current is not None:
            self.state.save_checkpoint(self.current)

    def before_write(self, path: Path) -> None:
        cp = self._ensure_current()
        rel = str(path.relative_to(self.root))
        if any(entry["path"] == rel for entry in cp["manifest"]):
            return
        entry: dict = {"path": rel, "existed": path.exists(), "backup": None, "after_sha256": None}
        if entry["existed"]:
            self.backups_dir.mkdir(parents=True, exist_ok=True)
            backup = self.backups_dir / f"{cp['id']}-{len(cp['manifest'])}.bak"
            shutil.copy2(path, backup)
            entry["backup"] = str(backup)
        cp["manifest"].append(entry)
        self._persist()

    def after_write(self, path: Path) -> None:
        cp = self._ensure_current()
        rel = str(path.relative_to(self.root))
        for entry in cp["manifest"]:
            if entry["path"] == rel:
                entry["after_sha256"] = file_sha256(path) if path.exists() else None
        self._persist()

    def changed_files(self) -> list[str]:
        if self.current is None:
            return []
        return [entry["path"] for entry in self.current["manifest"]]

    def undo(self, checkpoint_id: str) -> UndoReport:
        cp = self.state.get_checkpoint(checkpoint_id)
        if cp is None:
            raise KeyError(f"no checkpoint named {checkpoint_id}")
        report = UndoReport()
        for entry in reversed(cp["manifest"]):
            target = self.root / entry["path"]
            current_hash = file_sha256(target) if target.exists() else None
            if current_hash != entry["after_sha256"]:
                report.conflicts.append(entry["path"])
                continue
            if entry["existed"]:
                atomic_write(target, Path(entry["backup"]).read_bytes())
            elif target.exists():
                target.unlink()
            report.restored.append(entry["path"])
        cp["status"] = "reverted" if not report.conflicts else "partially_reverted"
        self.state.save_checkpoint(cp)
        return report

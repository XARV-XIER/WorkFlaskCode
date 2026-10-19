"""Git helpers. Read operations run freely; branch operations are explicit user commands."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .utils import truncate

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")


class GitError(Exception):
    """A git operation failed or git is unavailable."""


def available() -> bool:
    return shutil.which("git") is not None


def _git(root: Path, args: list[str], timeout: int = 30) -> tuple[int, str]:
    if not available():
        raise GitError("git is not installed")
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError("git timed out") from exc
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def is_repo(root: Path) -> bool:
    try:
        rc, _ = _git(root, ["rev-parse", "--is-inside-work-tree"])
    except GitError:
        return False
    return rc == 0


def status(root: Path) -> str:
    _, out = _git(root, ["status", "--short", "--branch"])
    return out or "clean"


def diff(root: Path, staged: bool = False, path: str | None = None) -> str:
    args = ["diff", "--stat"] + (["--staged"] if staged else [])
    _, stat = _git(root, args)
    patch_args = ["diff"] + (["--staged"] if staged else []) + (["--", path] if path else [])
    _, patch = _git(root, patch_args)
    if not stat and not patch:
        return "no changes"
    return truncate(f"{stat}\n\n{patch}".strip(), 20000)


def current_branch(root: Path) -> str:
    _, out = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    return out


def list_branches(root: Path) -> str:
    _, out = _git(root, ["branch", "--list"])
    return out or "(no branches)"


def _validate_branch(name: str) -> None:
    if not _BRANCH_RE.match(name) or ".." in name or name.endswith("/"):
        raise GitError(f"invalid branch name: {name!r}")


def create_branch(root: Path, name: str) -> str:
    _validate_branch(name)
    rc, out = _git(root, ["checkout", "-b", name])
    if rc != 0:
        raise GitError(out or "could not create branch")
    return out or f"created and switched to {name}"


def switch_branch(root: Path, name: str) -> str:
    _validate_branch(name)
    rc, out = _git(root, ["checkout", name])
    if rc != 0:
        raise GitError(out or "could not switch branch")
    return out or f"switched to {name}"

"""Workspace boundaries: every path the agent touches must resolve inside an authorized root."""

from __future__ import annotations

from pathlib import Path


class WorkspaceError(Exception):
    """A path was refused by the workspace policy."""


class Workspace:
    def __init__(self, root: Path | str, extra_roots: tuple[Path | str, ...] = ()) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"not a directory: {self.root}")
        self.roots = [self.root] + [Path(p).expanduser().resolve() for p in extra_roots]

    def _inside(self, path: Path) -> bool:
        return any(path == root or root in path.parents for root in self.roots)

    def resolve(self, path_str: str, *, for_write: bool = False) -> Path:
        """Resolve symlinks and reject anything outside the authorized roots."""
        if not isinstance(path_str, str) or not path_str.strip() or "\x00" in path_str:
            raise WorkspaceError("invalid path")
        candidate = Path(path_str.strip()).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        real = candidate.resolve()
        if not self._inside(real):
            raise WorkspaceError(f"path escapes the workspace: {path_str}")
        if for_write and self._is_under_root(real):
            if ".git" in real.relative_to(self.root).parts:
                raise WorkspaceError("writes inside .git are not allowed")
        return real

    def _is_under_root(self, path: Path) -> bool:
        return path == self.root or self.root in path.parents

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

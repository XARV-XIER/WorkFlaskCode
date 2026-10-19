"""Agent tools: file reads/writes and permission-gated command execution.

Commands never go through a shell. The model's text is split with shlex and passed as an
argument vector to subprocess, so `;`, `&&`, `|` and `$(...)` are inert literal arguments.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .checkpoints import CheckpointManager
from .testing import detect_test_command, read_test_override
from .utils import atomic_write, truncate
from .workspace import Workspace, WorkspaceError

MAX_READ_LINES = 400
MAX_SEARCH_RESULTS = 100
MAX_FILE_BYTES = 1_000_000
MAX_WRITE_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 12000
IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".workflaskcode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".tox",
}
PERMISSION_MODES = ("read_only", "approve_all", "approve_risky", "restricted_auto")

_ENV_KEEP = {
    "PATH", "HOME", "USER", "USERNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR",
    "TEMP", "TMP", "SYSTEMROOT", "PATHEXT", "COMSPEC", "USERPROFILE", "APPDATA",
    "LOCALAPPDATA", "PYTHONPATH", "VIRTUAL_ENV", "CARGO_HOME", "RUSTUP_HOME", "GOPATH", "GOROOT",
}
_ENV_SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIAL|COOKIE)", re.IGNORECASE)
DENY_EXECUTABLES = {
    "sudo", "su", "doas", "mkfs", "dd", "shutdown", "reboot", "halt", "poweroff", "diskpart",
    "format", "chown", "chmod", "mount", "umount", "passwd", "useradd", "userdel", "crontab",
    "systemctl", "launchctl", "reg", "kill", "killall", "pkill",
}
READ_ONLY_EXECUTABLES = {"ls", "cat", "pwd", "echo", "head", "tail", "wc", "bandit"}
TEST_EXECUTABLES = {"pytest", "mypy", "cargo", "go"}
PROTECTED_TARGETS = {"/", "~", "/*", "~/", "*"}


def filtered_env(source: dict[str, str] | None = None) -> dict[str, str]:
    src = dict(os.environ) if source is None else dict(source)
    env = {k: v for k, v in src.items() if k in _ENV_KEEP and not _ENV_SECRET_RE.search(k)}
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _exe_name(argv0: str) -> str:
    name = Path(argv0).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name.startswith("python"):
        return "python"
    return name


def classify_command(argv: list[str]) -> tuple[str, str]:
    """Return (decision, reason). decision is one of allow, test, ask, deny."""
    if not argv:
        return "deny", "empty command"
    exe = _exe_name(argv[0])
    args = argv[1:]
    if exe in DENY_EXECUTABLES:
        return "deny", f"'{exe}' is blocked by policy"
    if any(a in PROTECTED_TARGETS for a in args):
        return "deny", "command targets a protected location"
    if exe == "rm":
        return ("ask", "recursive or forced delete") if any(a.startswith("-") and "r" in a for a in args) \
            else ("ask", "file deletion")
    if exe == "git":
        sub = args[0] if args else ""
        if sub in ("status", "diff", "log", "show") or (sub == "branch" and len(args) == 1):
            return "allow", "read-only git command"
        return "ask", f"git {sub or '(no subcommand)'} may change repository state"
    if exe == "python":
        if len(args) >= 2 and args[0] == "-m" and args[1] in ("pytest", "unittest", "compileall", "py_compile"):
            return "test", f"test runner: python -m {args[1]}"
        if len(args) >= 2 and args[0] == "-m" and args[1] == "mypy":
            return "allow", "type checker"
        return "ask", "arbitrary Python execution"
    if exe == "ruff":
        if "--fix" in args or "format" in args:
            return "ask", "ruff would modify files"
        return "allow", "ruff analysis"
    if exe in TEST_EXECUTABLES:
        if exe == "cargo" and args[:1] != ["test"]:
            return "ask", "cargo command other than test"
        if exe == "go" and args[:1] != ["test"]:
            return "ask", "go command other than test"
        return ("test", f"{exe} test runner") if exe in ("pytest", "cargo", "go") else ("allow", "type checker")
    if exe == "npm":
        if args[:1] == ["test"]:
            return "test", "npm test"
        return "ask", "npm command other than test"
    if exe in READ_ONLY_EXECUTABLES:
        return "allow", "read-only utility"
    return "ask", f"unrecognized executable '{exe}'"


@dataclass
class ToolResult:
    ok: bool
    output: str
    changed: list[str] = field(default_factory=list)
    test: dict[str, Any] | None = None


class PermissionDenied(Exception):
    pass


ApproveFn = Callable[[str, str], bool]


class ToolExecutor:
    def __init__(
        self,
        workspace: Workspace,
        permission_mode: str,
        *,
        checkpoints: CheckpointManager,
        approve: ApproveFn | None = None,
        command_log: Path | None = None,
        command_timeout: int = 120,
        test_override: list[str] | None = None,
    ) -> None:
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(f"unknown permission mode: {permission_mode}")
        self.ws = workspace
        self.mode = permission_mode
        self.cp = checkpoints
        self.approve: ApproveFn = approve or (lambda kind, summary: False)
        self.command_log = command_log
        self.command_timeout = command_timeout
        self.test_override = test_override
        self.write_seq = 0
        self.test_runs: list[dict[str, Any]] = []
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "list_dir": self._list_dir,
            "read_file": self._read_file,
            "search_text": self._search_text,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "delete_file": self._delete_file,
            "run_command": self._run_command,
            "run_tests": self._run_tests,
        }

    def set_mode(self, mode: str) -> None:
        if mode not in PERMISSION_MODES:
            raise ValueError(f"unknown permission mode: {mode}")
        self.mode = mode

    _READ_SCHEMAS = [
        {"name": "list_dir", "description": "List entries of a directory inside the workspace.",
         "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
        {"name": "read_file", "description": "Read a text file with line numbers.",
         "parameters": {"type": "object", "properties": {
             "path": {"type": "string"}, "start_line": {"type": "integer"},
             "max_lines": {"type": "integer"}}, "required": ["path"]}},
        {"name": "search_text", "description": "Case-insensitive text search across project files.",
         "parameters": {"type": "object", "properties": {
             "query": {"type": "string"}, "path": {"type": "string"}}, "required": ["query"]}},
    ]
    _WRITE_SCHEMAS = [
        {"name": "write_file", "description": "Create or overwrite a file with full content.",
         "parameters": {"type": "object", "properties": {
             "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
        {"name": "edit_file", "description": "Replace one exact, unique occurrence of old_text with new_text.",
         "parameters": {"type": "object", "properties": {
             "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
             "required": ["path", "old_text", "new_text"]}},
        {"name": "delete_file", "description": "Delete a file (requires approval).",
         "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "run_command", "description": "Run one program with arguments. No shell syntax is interpreted.",
         "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
        {"name": "run_tests", "description": "Run the project's configured test command and report the exit code.",
         "parameters": {"type": "object", "properties": {}}},
    ]

    def schemas(self) -> list[dict[str, Any]]:
        if self.mode == "read_only":
            return list(self._READ_SCHEMAS)
        return list(self._READ_SCHEMAS) + list(self._WRITE_SCHEMAS)

    def run(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(False, f"error: unknown tool '{name}'")
        if not isinstance(args, dict):
            return ToolResult(False, "error: arguments must be an object")
        try:
            return handler(args)
        except PermissionDenied as exc:
            return ToolResult(False, f"denied: {exc}")
        except WorkspaceError as exc:
            return ToolResult(False, f"refused: {exc}")
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            return ToolResult(False, f"error: {exc}")

    # ---- helpers
    @staticmethod
    def _req(args: dict[str, Any], key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str):
            raise ValueError(f"'{key}' must be a string")
        return value

    def _ask(self, kind: str, summary: str) -> None:
        if not self.approve(kind, summary):
            raise PermissionDenied(f"{kind} was not approved: {summary}")

    def _gate_write(self, summary: str) -> None:
        if self.mode == "read_only":
            raise PermissionDenied("read-only mode: writes are disabled")
        if self.mode == "approve_all":
            self._ask("write", summary)

    def _gate_delete(self, summary: str) -> None:
        if self.mode == "read_only" or self.mode == "restricted_auto":
            raise PermissionDenied(f"deletes are disabled in {self.mode} mode")
        self._ask("delete", summary)

    # ---- read tools
    def _list_dir(self, args: dict[str, Any]) -> ToolResult:
        path = self.ws.resolve(args.get("path") or ".")
        if not path.is_dir():
            return ToolResult(False, f"error: not a directory: {self.ws.rel(path)}")
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for entry in entries[:300]:
            if entry.name in IGNORED_DIRS and entry.is_dir():
                lines.append(f"{entry.name}/  (ignored)")
            else:
                lines.append(entry.name + ("/" if entry.is_dir() else ""))
        if len(entries) > 300:
            lines.append(f"... {len(entries) - 300} more entries")
        return ToolResult(True, "\n".join(lines) or "(empty directory)")

    def _read_file(self, args: dict[str, Any]) -> ToolResult:
        path = self.ws.resolve(self._req(args, "path"))
        if not path.is_file():
            return ToolResult(False, f"error: not a file: {self.ws.rel(path)}")
        raw = path.read_bytes()[: MAX_FILE_BYTES + 1]
        if b"\x00" in raw[:8192]:
            return ToolResult(False, "error: binary file; not displayed")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        start = max(1, int(args.get("start_line") or 1))
        count = min(int(args.get("max_lines") or MAX_READ_LINES), MAX_READ_LINES)
        window = lines[start - 1: start - 1 + count]
        body = "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(window))
        header = f"{self.ws.rel(path)} ({len(lines)} lines"
        header += ", truncated" if len(raw) > MAX_FILE_BYTES else ""
        return ToolResult(True, f"{header}, showing {start}-{start + len(window) - 1})\n{body}")

    def _search_text(self, args: dict[str, Any]) -> ToolResult:
        query = self._req(args, "query")
        if not query:
            raise ValueError("query must not be empty")
        base = self.ws.resolve(args.get("path") or ".")
        needle = query.lower()
        matches: list[str] = []
        files = [base] if base.is_file() else self._walk_files(base)
        for file_path in files:
            if len(matches) >= MAX_SEARCH_RESULTS:
                break
            try:
                if file_path.stat().st_size > MAX_FILE_BYTES:
                    continue
                raw = file_path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if needle in line.lower():
                    matches.append(f"{self.ws.rel(file_path)}:{number}: {line.strip()[:200]}")
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        break
        return ToolResult(True, "\n".join(matches) or f"no matches for {query!r}")

    def _walk_files(self, base: Path):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name

    # ---- write tools
    def _commit_write(self, path: Path, content: str) -> bool:
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ValueError("content exceeds the 2 MB write limit")
        existed = path.exists()
        self.cp.before_write(path)
        atomic_write(path, content)
        self.cp.after_write(path)
        self.write_seq += 1
        return existed

    def _write_file(self, args: dict[str, Any]) -> ToolResult:
        if self.mode == "read_only":
            raise PermissionDenied("read-only mode: writes are disabled")
        path = self.ws.resolve(self._req(args, "path"), for_write=True)
        if path.is_dir():
            raise ValueError("path is a directory")
        content = self._req(args, "content")
        self._gate_write(f"write {self.ws.rel(path)} ({content.count(chr(10)) + 1} lines)")
        existed = self._commit_write(path, content)
        rel = self.ws.rel(path)
        verb = "updated" if existed else "created"
        return ToolResult(True, f"{verb} {rel} ({content.count(chr(10)) + 1} lines)", changed=[rel])

    def _edit_file(self, args: dict[str, Any]) -> ToolResult:
        if self.mode == "read_only":
            raise PermissionDenied("read-only mode: writes are disabled")
        path = self.ws.resolve(self._req(args, "path"), for_write=True)
        if not path.is_file():
            return ToolResult(False, f"error: not a file: {self.ws.rel(path)}")
        old = self._req(args, "old_text")
        new = self._req(args, "new_text")
        if not old:
            raise ValueError("old_text must not be empty")
        original = path.read_text(encoding="utf-8", errors="strict")
        count = original.count(old)
        if count == 0:
            return ToolResult(False, "error: old_text not found; read the file again and copy it exactly")
        if count > 1:
            return ToolResult(False, f"error: old_text matches {count} places; include more surrounding context")
        self._gate_write(f"edit {self.ws.rel(path)}")
        updated = original.replace(old, new, 1)
        self._commit_write(path, updated)
        rel = self.ws.rel(path)
        diff = list(difflib.unified_diff(original.splitlines(), updated.splitlines(),
                                         fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=2))
        return ToolResult(True, f"edited {rel}\n" + "\n".join(diff[:80]), changed=[rel])

    def _delete_file(self, args: dict[str, Any]) -> ToolResult:
        path = self.ws.resolve(self._req(args, "path"), for_write=True)
        if not path.is_file():
            return ToolResult(False, f"error: not a file: {self.ws.rel(path)}")
        self._gate_delete(f"delete {self.ws.rel(path)}")
        self.cp.before_write(path)
        path.unlink()
        self.cp.after_write(path)
        self.write_seq += 1
        rel = self.ws.rel(path)
        return ToolResult(True, f"deleted {rel}", changed=[rel])

    # ---- commands
    def _run_command(self, args: dict[str, Any]) -> ToolResult:
        if self.mode == "read_only":
            raise PermissionDenied("read-only mode: commands are disabled")
        command = self._req(args, "command")
        try:
            argv = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            return ToolResult(False, f"error: could not parse command: {exc}")
        decision, reason = classify_command(argv)
        self._gate_command(argv, decision, reason)
        rc, output = self.execute(argv)
        return ToolResult(rc == 0, f"exit code {rc}\n{output}")

    def _gate_command(self, argv: list[str], decision: str, reason: str) -> None:
        summary = f"{shlex.join(argv)}  [{reason}] cwd={self.ws.root}"
        if decision == "deny":
            raise PermissionDenied(f"blocked: {reason}")
        if decision in ("allow", "test"):
            if self.mode == "approve_all":
                self._ask("command", summary)
            return
        # decision == "ask"
        if self.mode in ("approve_all", "approve_risky"):
            self._ask("command", summary)
            return
        raise PermissionDenied(f"{reason}: not permitted in restricted_auto mode")

    def _run_tests(self, args: dict[str, Any]) -> ToolResult:
        if self.mode == "read_only":
            raise PermissionDenied("read-only mode: commands are disabled")
        argv = detect_test_command(self.ws.root, self.test_override)
        if not argv:
            return ToolResult(False, "error: no test command detected; set test_command in .workflaskcode/state.json")
        self._gate_command(argv, "test", "project test command")
        started = time.monotonic()
        rc, output = self.execute(argv)
        record = {"seq": self.write_seq, "rc": rc, "command": shlex.join(argv),
                  "seconds": round(time.monotonic() - started, 2)}
        self.test_runs.append(record)
        verdict = "PASSED" if rc == 0 else "FAILED"
        return ToolResult(rc == 0, f"tests {verdict} (exit code {rc}) via {record['command']}\n{output}", test=record)

    def execute(self, argv: list[str], timeout: int | None = None) -> tuple[int, str]:
        """Run argv without a shell in the workspace root, with a timeout and process-group cleanup."""
        limit = timeout or self.command_timeout
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(self.ws.root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=filtered_env(),
                start_new_session=os.name == "posix",
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            self._log_command(argv, 127)
            return 127, f"command not found: {argv[0]}"
        except OSError as exc:
            self._log_command(argv, 126)
            return 126, f"could not start command: {exc}"
        try:
            out, _ = proc.communicate(timeout=limit)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            out, _ = proc.communicate()
            rc = -1
            out = (out or "") + f"\n[timed out after {limit}s; process tree terminated]"
        self._log_command(argv, rc)
        return rc, truncate(out or "", MAX_OUTPUT_CHARS)

    def _log_command(self, argv: list[str], rc: int) -> None:
        if self.command_log is None:
            return
        self.command_log.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "argv": argv, "rc": rc,
                 "cwd": str(self.ws.root)}
        with self.command_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


def project_test_override(meta_dir: Path) -> list[str] | None:
    return read_test_override(meta_dir)

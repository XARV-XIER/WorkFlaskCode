"""Application core: owns projects, sessions, models, approvals and background turns."""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__
from .agent import Agent, TurnResult
from .audit import run_audit, write_report
from .btw import SideChat
from .checkpoints import CheckpointManager, UndoReport
from .config import ConfigStore, PERMISSION_MODES as CONFIG_PERMISSION_MODES
from .credentials import CredentialStore
from .paths import AgentHome
from .project import Project
from .providers import ModelInfo
from .router import ModelRouter, NotConfigured, Target, parse_target
from .state import StateStore
from .tools import ToolExecutor
from .ui import Console
from .utils import new_id, now_iso
from .workspace import Workspace, WorkspaceError

MODEL_CACHE_TTL = 24 * 3600


class AppError(Exception):
    """A user-facing error with a clear message and no traceback."""


class App:
    def __init__(self, home: AgentHome | None = None, *, transport=None,
                 console: Console | None = None) -> None:
        self.home = home or AgentHome()
        self.home.ensure()
        self.config = ConfigStore(self.home)
        self.creds = CredentialStore(self.home)
        self.state = StateStore(self.home.db_path)
        self.console = console or Console(color=True)
        self.transport = transport
        self.router = ModelRouter(self.config, self.creds, self.state, transport)
        self.router.on_retry = lambda msg: self.emit("info", f"retrying: {msg}")
        self.project: Project | None = None
        self.workspace: Workspace | None = None
        self.session_id: str | None = None
        self.mode = self.config.data["general"]["default_mode"]
        self.executor: ToolExecutor | None = None
        self.checkpoints: CheckpointManager | None = None
        self.agent: Agent | None = None
        self.side: SideChat | None = None
        self.exit_requested = False
        self.last_request: str | None = None
        self.last_result: TurnResult | None = None
        self._turn_thread: threading.Thread | None = None
        self._turn_cancel: threading.Event | None = None
        self._turn_lock = threading.Lock()
        self._approval_lock = threading.Lock()
        self._pending: dict[str, Any] | None = None

    # ------------------------------------------------------------ output
    def emit(self, kind: str, text: str) -> None:
        if kind == "side" or kind == "side_info" or kind == "side_error":
            self.console.line(text if kind == "side" else f"  {text}",
                              "side" if kind == "side" else "info")
            return
        self.console.event(kind, text)

    # ------------------------------------------------------------ projects & sessions
    def open_project(self, path: str | Path) -> Project:
        if self.busy():
            raise AppError("a task is running; @stop it before switching projects")
        try:
            workspace = Workspace(Path(path).expanduser())
        except WorkspaceError as exc:
            raise AppError(str(exc)) from exc
        self.workspace = workspace
        self.project = Project(workspace.root)
        self.state.upsert_project(str(workspace.root), self.project.name)
        self._new_session()
        return self.project

    def _new_session(self) -> str:
        self.session_id = new_id("s-")
        provider, model = self._current_provider_model()
        self.state.create_session(self.session_id, str(self.workspace.root), self.mode, provider, model)
        self._build_runtime()
        return self.session_id

    def resume(self, session_id: str | None = None) -> int:
        if self.workspace is None:
            raise AppError("open a project first")
        if self.busy():
            raise AppError("a task is running; @stop it first")
        if session_id:
            session = self.state.get_session(session_id)
            if session is None:
                raise AppError(f"no session named {session_id}")
        else:
            session = self.state.last_session(str(self.workspace.root))
            if session is None:
                raise AppError("no earlier session for this project")
        if session["project_path"] != str(self.workspace.root):
            raise AppError("that session belongs to another project; @open it first")
        self.session_id = session["id"]
        self.mode = session["mode"] if session["mode"] in ("chat", "coding") else self.mode
        self._build_runtime()
        rows = self.state.list_messages(self.session_id, "main")
        self.agent.load(rows)
        return len(rows)

    def reload_config(self) -> None:
        self.config = ConfigStore(self.home)
        self.router.config = self.config
        self.router.invalidate()
        if self.executor is not None:
            self.executor.set_mode(self.permission_mode())

    def _current_provider_model(self) -> tuple[str, str]:
        general = self.config.data["general"]
        return general["default_provider"], general["coding_model"]

    def _build_runtime(self) -> None:
        assert self.workspace is not None and self.project is not None and self.session_id is not None
        limits = self.config.data["limits"]
        self.checkpoints = CheckpointManager(self.state, self.home.backups_dir, self.session_id,
                                             self.workspace.root)
        self.executor = ToolExecutor(
            self.workspace,
            self.config.data["general"]["permission_mode"],
            checkpoints=self.checkpoints,
            approve=self._request_approval,
            command_log=self.home.command_log,
            command_timeout=limits["command_timeout_s"],
            test_override=self._test_override(),
        )
        self.agent = Agent(
            root=self.workspace.root, session_id=self.session_id, router=self.router,
            executor=self.executor, state=self.state, max_steps=limits["max_steps"],
            context_budget=limits["context_budget_chars"], emit=self.emit, mode=self.mode,
            brief=self.project.brief(), on_compact=self._on_compact,
        )
        self.side = SideChat(self.router, self.state, self.session_id, self.emit,
                             max_concurrent=limits["max_btw_concurrency"])

    def _test_override(self) -> list[str] | None:
        from .testing import read_test_override

        return read_test_override(self.project.meta) if self.project else None

    def _on_compact(self, summary: str) -> None:
        if self.project and self.project.meta.exists():
            self.project.write_handoff(summary)

    def set_mode(self, mode: str) -> None:
        if mode not in ("chat", "coding"):
            raise AppError("mode must be chat or coding")
        if self.busy():
            raise AppError("a task is running; @stop it before changing mode")
        self.mode = mode
        if self.agent:
            self.agent.mode = mode
        if self.session_id:
            self.state.update_session(self.session_id, mode=mode)

    def permission_mode(self) -> str:
        return self.config.data["general"]["permission_mode"]

    def set_permission_mode(self, mode: str, persist: bool = True) -> None:
        if mode not in CONFIG_PERMISSION_MODES:
            raise AppError(f"unknown mode; choose one of: {', '.join(CONFIG_PERMISSION_MODES)}")
        self.config.data["general"]["permission_mode"] = mode
        if self.executor:
            self.executor.set_mode(mode)
        if persist:
            self.config.save()

    # ------------------------------------------------------------ turns
    def busy(self) -> bool:
        return self._turn_thread is not None and self._turn_thread.is_alive()

    def start_turn(self, text: str) -> None:
        if self.workspace is None or self.agent is None or self.project is None:
            raise AppError("open a project first: @open <path>")
        with self._turn_lock:
            if self.busy():
                raise AppError("a task is already running; use @btw for a side question or @stop")
            self.project.ensure_metadata()
            self.last_request = text
            cancel = threading.Event()
            self._turn_cancel = cancel
            task_id = new_id("t-")
            now = now_iso()
            self.checkpoints.begin(text, task_id)
            self.state.put_task({
                "id": task_id, "session_id": self.session_id, "parent_id": None,
                "description": text[:300], "status": "in_progress", "attempts": 1,
                "model": self._current_provider_model()[1], "files": [], "validation": "",
                "failure": "", "next_action": "", "checkpoint_id": self.checkpoints.current["id"],
                "created_at": now, "updated_at": now,
            })
            self._turn_thread = threading.Thread(
                target=self._run_turn, args=(text, task_id, cancel), daemon=True, name="main-turn")
            self._turn_thread.start()

    def _run_turn(self, text: str, task_id: str, cancel: threading.Event) -> None:
        try:
            result = self.agent.ask(text, cancel)
        except Exception as exc:  # a turn must never kill the interface
            result = TurnResult(status="blocked", error=f"internal error: {exc}")
        self.last_result = result
        try:
            self._finalize_task(task_id, result)
        except Exception as exc:
            self.emit("error", f"could not save task state: {exc}")
        if result.text:
            self.console.agent_block("agent", result.text)
        if result.status == "completed":
            self.console.ok("done.")
        elif result.status == "cancelled":
            self.console.warn("task cancelled; progress is saved. Use @retry to run it again.")
        else:
            self.console.warn(f"task {result.status}: {result.error or 'see @tasks'}")

    def _finalize_task(self, task_id: str, result: TurnResult) -> None:
        task = self.state.get_task(task_id) or {}
        changed = list(dict.fromkeys(result.changed))
        task["files"] = changed
        task["updated_at"] = now_iso()
        if result.status == "cancelled":
            task["status"], task["next_action"] = "cancelled", "@retry to resume"
        elif result.status in ("blocked", "step_limit"):
            task["status"], task["failure"] = "blocked", result.error
            task["next_action"] = "fix the cause, then @retry"
        elif not changed:
            task["status"] = "completed"
            task["validation"] = "no files changed"
        else:
            runs = self.executor.test_runs if self.executor else []
            last = runs[-1] if runs else None
            seq = self.executor.write_seq if self.executor else -1
            if last and last["seq"] == seq and last["rc"] == 0:
                task["status"] = "completed"
                task["validation"] = f"tests passed: {last['command']}"
            elif last and last["seq"] == seq:
                task["status"] = "failed"
                task["validation"] = f"tests failed (exit {last['rc']}): {last['command']}"
                task["next_action"] = "debug the failing tests with @debug"
            else:
                task["status"] = "in_progress"
                task["validation"] = "unverified: no passing test run after the last change"
                task["next_action"] = "run @test to verify"
        task["checkpoint_id"] = self.checkpoints.current["id"] if self.checkpoints and self.checkpoints.current else None
        self.state.put_task(task)

    def cancel_turn(self) -> bool:
        if not self.busy() or self._turn_cancel is None:
            return False
        self._turn_cancel.set()
        return True

    def stop_all(self) -> str:
        cancelled_main = self.cancel_turn()
        if self._turn_thread is not None:
            self._turn_thread.join(5)
        cancelled_side = self.side.cancel() if self.side else 0
        return f"main task: {'cancellation requested' if cancelled_main else 'none running'}; " \
               f"side questions cancelled: {cancelled_side}"

    def pending_approval(self) -> dict[str, Any] | None:
        with self._approval_lock:
            return dict(self._pending) if self._pending else None

    def _request_approval(self, kind: str, summary: str) -> bool:
        slot: dict[str, Any] = {"event": threading.Event(), "ok": False, "kind": kind, "summary": summary}
        with self._approval_lock:
            self._pending = slot
        self.console.event("approval", f"{kind}: {summary}")
        self.console.line("  type @approve or @deny", "approval")
        cancel = self._turn_cancel
        while not slot["event"].wait(0.25):
            if cancel is not None and cancel.is_set():
                break
        with self._approval_lock:
            self._pending = None
        return bool(slot["ok"]) and slot["event"].is_set()

    def resolve_approval(self, approved: bool) -> str:
        with self._approval_lock:
            if self._pending is None:
                return "nothing is waiting for approval"
            self._pending["ok"] = approved
            self._pending["event"].set()
            return ("approved" if approved else "denied") + f": {self._pending['summary']}"

    def ask_side(self, question: str) -> int:
        if self.side is None:
            raise AppError("open a project first")
        snapshot = ""
        status = self.status_text()
        snapshot = f"Main task status:\n{status}"
        if self.config.data["privacy"]["btw_sees_main_history"]:
            rows = self.state.list_messages(self.session_id, "main")[-6:]
            snapshot += "\nRecent main messages:\n" + "\n".join(
                f"{r['role']}: {str(r['payload'].get('content', ''))[:300]}" for r in rows)
        return self.side.ask(question, snapshot)

    # ------------------------------------------------------------ undo / tasks
    def undo(self, checkpoint_id: str = "latest") -> UndoReport:
        if self.checkpoints is None or self.session_id is None:
            raise AppError("open a project first")
        if self.busy():
            raise AppError("a task is running; @stop it first")
        if checkpoint_id in ("latest", ""):
            candidates = [c for c in self.state.list_checkpoints(self.session_id)
                          if c["manifest"] and c["status"] in ("open", "partially_reverted")]
            if not candidates:
                raise AppError("no checkpoints with changes in this session")
            checkpoint_id = candidates[0]["id"]
        try:
            return self.checkpoints.undo(checkpoint_id)
        except KeyError as exc:
            raise AppError(str(exc)) from exc

    # ------------------------------------------------------------ models
    def _provider_or_raise(self, name: str):
        try:
            return self.router.provider(name)
        except NotConfigured as exc:
            raise AppError(str(exc)) from exc

    def models(self, provider_name: str | None = None, refresh: bool = False) -> list[ModelInfo]:
        name = provider_name or self.config.data["general"]["default_provider"]
        if not name:
            raise AppError("no provider configured; run workflaskcode setup")
        cache = self.home.cache_dir / f"models-{name}.json"
        if not refresh and cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if time.time() - float(data["fetched_at"]) < MODEL_CACHE_TTL:
                    return [ModelInfo(**m) for m in data["models"]]
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                pass
        provider = self._provider_or_raise(name)
        models = provider.list_models()
        self.home.cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"fetched_at": time.time(),
                                     "models": [m.to_dict() for m in models]}), encoding="utf-8")
        return models

    def test_model(self, spec: str | None = None) -> tuple[Target, str]:
        general = self.config.data["general"]
        target = parse_target(spec or general["coding_model"], general["default_provider"])
        if not target.model:
            raise AppError("no model specified")
        provider = self._provider_or_raise(target.provider)
        response = provider.generate(
            [{"role": "user", "content": "Reply with the single word: ok"}],
            model=target.model, system="", max_tokens=16,
        )
        self.state.add_usage(None, target.provider, target.model, response.usage, 0, True, None)
        return target, response.text.strip()[:80]

    def change_model(self, spec: str) -> Target:
        """Validate the new model first. On failure the old configuration is kept."""
        if not spec.strip():
            raise AppError("usage: @changemodel <model-id> [provider]")
        general = self.config.data["general"]
        target = parse_target(spec.strip(), general["default_provider"])
        was_busy = self.busy()
        if was_busy:
            self.cancel_turn()
            self._turn_thread.join(10)
        try:
            self.test_model(f"{target.provider}::{target.model}")
        except Exception as exc:
            raise AppError(f"model check failed, configuration unchanged: {exc}") from exc
        old = f"{general['default_provider']}::{general['coding_model']}"
        general["coding_model"] = target.model
        general["default_provider"] = target.provider
        self.config.save()
        if self.session_id:
            self.state.update_session(self.session_id, provider=target.provider, model=target.model)
        if self.project and self.project.meta.exists():
            self.project.write_handoff(f"Model switched from {old} to {target.label()}.\n"
                                       "Completed work, files and tests are unchanged.")
        return target

    def configured_providers(self) -> list[dict[str, Any]]:
        rows = []
        for name, entry in self.config.providers().items():
            rows.append({"name": name, "kind": entry["kind"], "model": entry.get("model", ""),
                         "base_url": entry.get("base_url", ""), "key": self.creds.source(name),
                         "default": name == self.config.data["general"]["default_provider"]})
        return rows

    # ------------------------------------------------------------ reporting
    def status_text(self) -> str:
        general = self.config.data["general"]
        lines = [
            f"project   : {self.workspace.root if self.workspace else '(none)'}",
            f"session   : {self.session_id or '(none)'}",
            f"mode      : {self.mode}  permission: {self.permission_mode()}",
            f"model     : {general['default_provider'] or '-'}::{general['coding_model'] or '-'}",
            f"state     : {'running' if self.busy() else 'idle'}"
            + (f"  pending approval: {self.pending_approval()['summary']}" if self.pending_approval() else ""),
        ]
        if self.side and self.side.active():
            lines.append(f"side q's  : {self.side.active()} running")
        if self.session_id:
            tasks = self.state.list_tasks(self.session_id)
            if tasks:
                last = tasks[-1]
                lines.append(f"last task : [{last['status']}] {last['description'][:80]}")
                if last.get("validation"):
                    lines.append(f"validation: {last['validation']}")
        return "\n".join(lines)

    def banner_lines(self) -> list[str]:
        general = self.config.data["general"]
        return [
            f"WorkFlaskCode v{__version__}",
            "Terminal AI Software Engineering Agent",
            "",
            f"Agent Home : {self.home.root}",
            f"Project    : {self.workspace.root if self.workspace else '(none; use @open <path>)'}",
            f"Provider   : {general['default_provider'] or '(not configured)'}",
            f"Model      : {general['coding_model'] or '(not configured)'}",
            f"Mode       : {self.mode}   Permissions: {self.permission_mode()}",
            f"Session    : {self.session_id or '-'}",
            f"Platform   : {sys.platform}  Python {sys.version.split()[0]}",
        ]

    def audit(self, security_only: bool = False, use_tools: bool = True) -> dict:
        if self.workspace is None:
            raise AppError("open a project first")

        def runner(argv: list[str]) -> tuple[int, str]:
            # Audit tools run only when the command itself is classified as read-only or a test run.
            if self.executor is None or self.executor.mode == "read_only":
                return 127, ""
            if _decision(argv) not in ("allow", "test") or shutil.which(argv[0]) is None:
                return 127, ""
            return self.executor.execute(argv)

        report = run_audit(self.workspace.root, runner if use_tools else None,
                           security_only=security_only, use_tools=use_tools)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        report["saved_to"] = str(write_report(report, self.home.reports_dir, stamp))
        return report

    def run_argv(self, argv: list[str]) -> tuple[int, str]:
        if self.executor is None:
            raise AppError("open a project first")
        from .tools import classify_command

        decision, reason = classify_command(argv)
        summary = f"{' '.join(argv)} [{reason}]"
        if decision == "deny":
            return 126, f"blocked: {reason}"
        if decision == "ask" or (decision in ("allow", "test") and self.executor.mode == "approve_all"):
            if self.executor.mode == "read_only" or not self._request_approval("command", summary):
                return 126, "not approved or disabled in this permission mode"
        elif self.executor.mode == "read_only":
            return 126, "read-only mode: commands are disabled"
        return self.executor.execute(argv)

    def export_transcript(self, target: Path) -> Path:
        if self.session_id is None:
            raise AppError("no active session")
        lines = [f"# WorkFlaskCode session {self.session_id}", ""]
        for row in self.state.list_messages(self.session_id, "main"):
            content = row["payload"].get("content") or ""
            if content:
                lines += [f"## {row['role']}", "", content, ""]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines), encoding="utf-8")
        return target

    def shutdown(self) -> None:
        if self.busy():
            self.cancel_turn()
            self._turn_thread.join(5)
        if self.side:
            self.side.cancel()
            self.side.wait_all(3)
        if self.session_id:
            self.state.update_session(self.session_id, status="paused")
        self.state.close()


def _decision(argv: list[str]) -> str:
    from .tools import classify_command

    return classify_command(argv)[0]


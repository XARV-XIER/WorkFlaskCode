"""The @command system. Every implemented command calls real code; the rest say so."""

from __future__ import annotations

import difflib
import json
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import git
from .app import App, AppError
from .credentials import redact
from .project import Project, ProjectError, scaffold
from .providers.base import ProviderError
from .router import AllTargetsFailed
from .workspace import WorkspaceError

Handler = Callable[[App, str], None]


@dataclass(frozen=True)
class Command:
    name: str
    group: str
    summary: str
    handler: Handler
    implemented: bool = True


COMMANDS: dict[str, Command] = {}
ALIASES: dict[str, str] = {"undo": "rollback", "security": "security", "exit": "exit"}
GROUPS = ("General", "Models", "Modes", "Planning", "Code", "Git", "Not implemented in this release")


def command(name: str, group: str, summary: str, *aliases: str):
    def wrap(fn: Handler) -> Handler:
        COMMANDS[name] = Command(name, group, summary, fn)
        for alias in aliases:
            ALIASES[alias] = name
        return fn
    return wrap


def _not_implemented(name: str, summary: str) -> None:
    COMMANDS[name] = Command(name, GROUPS[-1], summary, lambda app, args: app.console.warn(
        f"@{name} is not implemented in this release (see docs/STATUS.md)"), implemented=False)


# ---------------------------------------------------------------- general
@command("help", "General", "list commands and examples")
def cmd_help(app: App, args: str) -> None:
    app.console.heading("WorkFlaskCode commands")
    for group in GROUPS:
        entries = sorted((c for c in COMMANDS.values() if c.group == group), key=lambda c: c.name)
        if not entries:
            continue
        app.console.line("")
        app.console.heading(group)
        for c in entries:
            app.console.line(f"  @{c.name:<16} {c.summary}")
    app.console.line("")
    app.console.line("Type a request to work on the project. Use @btw <question> for a side question.")


@command("setup", "General", "reopen the configuration wizard")
def cmd_setup(app: App, args: str) -> None:
    from .setup import run_setup

    if app.busy():
        raise AppError("a task is running; @stop it before running setup")
    if run_setup(app.home, transport=app.transport):
        app.reload_config()
        app.console.ok("configuration reloaded")


@command("status", "General", "current work, model and task progress")
def cmd_status(app: App, args: str) -> None:
    app.console.line(app.status_text())


@command("where", "General", "print absolute paths that matter")
def cmd_where(app: App, args: str) -> None:
    rows = [
        ("agent home", app.home.root), ("config", app.home.config_file),
        ("state db", app.home.db_path), ("backups", app.home.backups_dir),
        ("reports", app.home.reports_dir), ("command log", app.home.command_log),
        ("project", app.workspace.root if app.workspace else "(none)"),
        ("project metadata", (app.workspace.root / ".workflaskcode") if app.workspace else "(none)"),
    ]
    app.console.table(["item", "path"], rows)


@command("workspace", "General", "active project and workspace boundaries")
def cmd_workspace(app: App, args: str) -> None:
    if app.workspace is None:
        raise AppError("no project open; use @open <path>")
    repo = git.is_repo(app.workspace.root)
    app.console.line(f"root        : {app.workspace.root}")
    app.console.line(f"authorized  : {', '.join(str(r) for r in app.workspace.roots)}")
    app.console.line(f"permissions : {app.permission_mode()}")
    app.console.line(f"git repo    : {'yes, branch ' + git.current_branch(app.workspace.root) if repo else 'no'}")
    app.console.line(f"stack       : {', '.join(app.project.stack()) or 'unknown'}")


@command("projects", "General", "registered projects")
def cmd_projects(app: App, args: str) -> None:
    rows = [("*" if app.workspace and p["path"] == str(app.workspace.root) else "", p["name"], p["path"],
             p["last_opened_at"]) for p in app.state.list_projects()]
    if not rows:
        app.console.info("no projects yet; use @open <path> or @new <name>")
        return
    app.console.table(["", "name", "path", "last opened"], rows)


@command("open", "General", "switch to a project directory (validated)")
def cmd_open(app: App, args: str) -> None:
    if not args.strip():
        raise AppError("usage: @open <path>")
    project = app.open_project(args.strip())
    app.console.ok(f"project: {project.root}")
    app.console.info(f"session {app.session_id} started; use @resume to restore an earlier one")


@command("new", "General", "create a project from a template: @new <name> [parent-dir]")
def cmd_new(app: App, args: str) -> None:
    parts = shlex.split(args)
    if not parts:
        raise AppError("usage: @new <name> [parent-dir]")
    parent = Path(parts[1]).expanduser() if len(parts) > 1 else app.home.projects_dir
    if not parent.is_dir():
        parent.mkdir(parents=True, exist_ok=True)
    try:
        target = scaffold(parts[0], parent, "python-cli")
    except ProjectError as exc:
        raise AppError(str(exc)) from exc
    app.console.ok(f"created python-cli project at {target}")
    cmd_open(app, str(target))


@command("resume", "General", "restore an earlier session: @resume [session-id]")
def cmd_resume(app: App, args: str) -> None:
    count = app.resume(args.strip() or None)
    app.console.ok(f"restored session {app.session_id} with {count} messages")


@command("history", "General", "list sessions or search messages: @history [text]")
def cmd_history(app: App, args: str) -> None:
    if args.strip():
        rows = [(r["session_id"], r["role"], r["created_at"], str(r["payload"].get("content", ""))[:90])
                for r in app.state.search_messages(args.strip())]
        app.console.table(["session", "role", "when", "text"], rows) if rows else app.console.info("no matches")
        return
    rows = [(s["id"], s["mode"], s["status"], s["updated_at"], s["project_path"])
            for s in app.state.list_sessions(str(app.workspace.root) if app.workspace else None)]
    app.console.table(["session", "mode", "status", "updated", "project"], rows) if rows \
        else app.console.info("no sessions yet")


@command("clear", "General", "clear the screen (data is kept)")
def cmd_clear(app: App, args: str) -> None:
    app.console.clear()


@command("exit", "General", "save state and quit")
def cmd_exit(app: App, args: str) -> None:
    if app.busy():
        app.cancel_turn()
        app.console.info("cancelling the running task and saving state")
    app.exit_requested = True


@command("doctor", "General", "diagnose configuration, dependencies, workspace and providers")
def cmd_doctor(app: App, args: str) -> None:
    from .doctor import run_doctor

    for level, title, detail in run_doctor(app):
        mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}.get(level, level)
        app.console.line(f"[{mark}] {title}: {detail}")


# ---------------------------------------------------------------- models
@command("models", "Models", "list models: @models [refresh] [filter]")
def cmd_models(app: App, args: str) -> None:
    tokens = args.split()
    refresh = "refresh" in tokens
    needle = " ".join(t for t in tokens if t != "refresh").lower()
    models = app.models(refresh=refresh)
    shown = [m for m in models if needle in m.id.lower() or needle in m.name.lower()]
    rows = [(m.id, m.context_length or "?", m.price_label(),
             "yes" if m.supports_tools else ("no" if m.supports_tools is False else "?"),
             "yes" if m.supports_vision else ("no" if m.supports_vision is False else "?"))
            for m in shown[:60]]
    app.console.table(["model id", "context", "price", "tools", "vision"], rows) if rows \
        else app.console.info("no models match")
    app.console.info(f"{len(shown)} of {len(models)} models shown" + (" (use a filter to narrow)" if len(shown) > 60 else ""))


@command("changemodel", "Models", "switch model after validation: @changemodel <model-id> [provider]")
def cmd_changemodel(app: App, args: str) -> None:
    parts = args.split()
    if not parts:
        raise AppError("usage: @changemodel <model-id> [provider]")
    spec = f"{parts[1]}::{parts[0]}" if len(parts) > 1 else parts[0]
    target = app.change_model(spec)
    app.console.ok(f"model set to {target.label()}; project state preserved")


@command("testmodel", "Models", "send a minimal request to check the current or given model")
def cmd_testmodel(app: App, args: str) -> None:
    target, reply = app.test_model(args.strip() or None)
    app.console.ok(f"{target.label()} replied: {reply!r}")


@command("providers", "Models", "configured providers and where their keys come from")
def cmd_providers(app: App, args: str) -> None:
    rows = [(("*" if p["default"] else ""), p["name"], p["kind"], p["model"] or "-",
             p["base_url"] or "-", p["key"]) for p in app.configured_providers()]
    app.console.table(["", "name", "kind", "model", "base url", "key source"], rows) if rows \
        else app.console.info("no providers configured; run @setup")


@command("usage", "Models", "requests, tokens and reported cost")
def cmd_usage(app: App, args: str) -> None:
    rows = [(r["provider"], r["model"], r["requests"], r["successes"], r["input_tokens"] or 0,
             r["output_tokens"] or 0, f"${(r['reported_cost_usd'] or 0):.4f}") for r in app.state.usage_summary()]
    app.console.table(["provider", "model", "requests", "ok", "in tokens", "out tokens", "reported cost"],
                      rows) if rows else app.console.info("no usage recorded yet")
    app.console.info("cost is only what the provider reports; unknown costs are not estimated")


@command("limits", "Models", "limits and budgets in effect")
def cmd_limits(app: App, args: str) -> None:
    for key, value in app.config.data["limits"].items():
        app.console.line(f"{key:<26} {value}")
    budget = app.config.data["limits"]["max_estimated_cost_usd"]
    spent = app.state.session_cost(app.session_id) if app.session_id else 0.0
    app.console.line(f"{'session reported spend':<26} ${spent:.4f}" + ("" if budget else "  (no budget set)"))


@command("fallback", "Models", "fallback chain: @fallback [list | add <spec> | remove <spec> | clear]")
def cmd_fallback(app: App, args: str) -> None:
    parts = args.split(maxsplit=1)
    action = parts[0] if parts else "list"
    chain = app.config.data["general"]["fallback_models"]
    if action == "list":
        app.console.line("primary : " + (app.config.data["general"]["coding_model"] or "-"))
        for i, spec in enumerate(chain, 1):
            app.console.line(f"fallback {i}: {spec}")
        if not chain:
            app.console.info("no fallbacks; use 'add <model-id>' or 'add provider::model-id'")
        return
    value = parts[1].strip() if len(parts) > 1 else ""
    if action == "add" and value:
        if value not in chain:
            chain.append(value)
    elif action == "remove" and value:
        if value in chain:
            chain.remove(value)
        else:
            raise AppError(f"{value} is not in the fallback chain")
    elif action == "clear":
        chain.clear()
    else:
        raise AppError("usage: @fallback [list | add <spec> | remove <spec> | clear]")
    app.config.save()
    app.console.ok("fallback chain saved")


@command("retry", "Models", "run the last request again")
def cmd_retry(app: App, args: str) -> None:
    if not app.last_request:
        raise AppError("nothing to retry yet")
    app.start_turn(app.last_request)
    app.console.info("retrying the last request in the background")


# ---------------------------------------------------------------- modes
@command("chat", "Modes", "switch to read-only conversation mode")
def cmd_chat(app: App, args: str) -> None:
    app.set_mode("chat")
    app.console.ok("chat mode: no file writes or commands; uses the chat model")


@command("codingagent", "Modes", "switch to the coding agent (tools enabled)")
def cmd_codingagent(app: App, args: str) -> None:
    app.set_mode("coding")
    app.console.ok("coding mode: tools enabled within the permission policy")


@command("btw", "Modes", "non-blocking, read-only side question: @btw <question>")
def cmd_btw(app: App, args: str) -> None:
    if not args.strip():
        raise AppError("usage: @btw <question>")
    job = app.ask_side(args.strip())
    app.console.info(f"side question {job} started; the main task continues")


@command("stop", "Modes", "cancel the main task (or: @stop btw)")
def cmd_stop(app: App, args: str) -> None:
    if args.strip() == "btw":
        count = app.side.cancel() if app.side else 0
        app.console.info(f"cancelled {count} side question(s)")
        return
    app.console.info(app.stop_all())


@command("approve", "Modes", "approve the pending action")
def cmd_approve(app: App, args: str) -> None:
    app.console.info(app.resolve_approval(True))


@command("deny", "Modes", "deny the pending action")
def cmd_deny(app: App, args: str) -> None:
    app.console.info(app.resolve_approval(False))


# ---------------------------------------------------------------- planning
def _oneshot(app: App, prompt: str, label: str = "agent") -> str:
    response, target = app.router.generate(
        "chat", [{"role": "user", "content": prompt, "tool_calls": None, "tool_call_id": None}],
        system="You are WorkFlaskCode. Answer precisely from the material provided. Do not claim to have "
               "run anything. You cannot modify files in this mode.",
        tools=None, session_id=app.session_id, max_tokens=3000,
    )
    app.console.agent_block(target.label(), response.text)
    return response.text


def _project_context(app: App, max_chars: int = 6000) -> str:
    if app.workspace is None:
        return ""
    listing = app.executor.run("list_dir", {"path": "."}).output if app.executor else ""
    return f"Project root: {app.workspace.root}\nStack: {', '.join(app.project.stack()) or 'unknown'}\n" \
           f"Top-level entries:\n{listing}\n{app.project.brief()[:1500]}"[:max_chars]


@command("plan", "Planning", "draft a plan and record it as pending tasks: @plan <goal>")
def cmd_plan(app: App, args: str) -> None:
    if not args.strip():
        raise AppError("usage: @plan <goal>")
    text = _oneshot(app, f"{_project_context(app)}\n\nWrite a numbered implementation plan for: {args}\n"
                         "One concrete step per line, starting with its number.")
    steps = [re.sub(r"^\s*\d+[.)]\s*", "", line).strip() for line in text.splitlines()
             if re.match(r"^\s*\d+[.)]\s+", line)]
    if app.session_id and steps:
        from .utils import new_id, now_iso

        for step in steps[:30]:
            now = now_iso()
            app.state.put_task({"id": new_id("t-"), "session_id": app.session_id, "parent_id": None,
                                "description": step[:300], "status": "pending", "attempts": 0,
                                "model": None, "files": [], "validation": "", "failure": "",
                                "next_action": "", "checkpoint_id": None, "created_at": now,
                                "updated_at": now})
        app.console.info(f"recorded {min(len(steps), 30)} pending tasks; see @tasks")


@command("tasks", "Planning", "task ledger for this session")
def cmd_tasks(app: App, args: str) -> None:
    if not app.session_id:
        raise AppError("no session open")
    rows = [(t["id"], t["status"], t["description"][:60], t["validation"][:50], t["next_action"][:40])
            for t in app.state.list_tasks(app.session_id)]
    app.console.table(["id", "status", "description", "validation", "next"], rows) if rows \
        else app.console.info("no tasks yet")


@command("checkpoint", "Planning", "start a labelled checkpoint: @checkpoint [label]")
def cmd_checkpoint(app: App, args: str) -> None:
    if app.checkpoints is None:
        raise AppError("open a project first")
    cid = app.checkpoints.begin(args.strip() or "manual checkpoint")
    app.checkpoints.state.save_checkpoint(app.checkpoints.current)
    app.console.ok(f"checkpoint {cid}; agent writes from now on are grouped under it")


@command("rollback", "Planning", "undo the agent's changes in a checkpoint: @rollback [id|latest]", "undo")
def cmd_rollback(app: App, args: str) -> None:
    report = app.undo(args.strip() or "latest")
    if report.restored:
        app.console.ok("restored: " + ", ".join(report.restored))
    if report.conflicts:
        app.console.warn("left alone (changed since the agent wrote them): " + ", ".join(report.conflicts))
    if not report.restored and not report.conflicts:
        app.console.info("checkpoint contained no files")


@command("permissions", "Planning", "show or set the permission mode: @permissions [mode]")
def cmd_permissions(app: App, args: str) -> None:
    if args.strip():
        app.set_permission_mode(args.strip())
        app.console.ok(f"permission mode set to {args.strip()}")
        return
    app.console.table(["mode", "effect"], [
        ("read_only", "no writes, no commands"),
        ("approve_all", "every write, delete and command asks first"),
        ("approve_risky", "reads and known test runners run; writes allowed; other commands and deletes ask"),
        ("restricted_auto", "known-safe commands and workspace writes run; anything else is refused"),
    ])
    app.console.info(f"current: {app.permission_mode()}")


@command("compact", "Planning", "summarize older turns to free context")
def cmd_compact(app: App, args: str) -> None:
    if app.agent is None:
        raise AppError("open a project first")
    summary = app.agent.compact(force=True)
    app.console.info(summary or "nothing to compact")


@command("export", "Planning", "write the session transcript to a file: @export [path]")
def cmd_export(app: App, args: str) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(args.strip()).expanduser() if args.strip() else app.home.reports_dir / f"transcript-{stamp}.md"
    if not target.is_absolute():
        target = (app.workspace.root / target) if app.workspace else target.resolve()
    written = app.export_transcript(target.resolve())
    app.console.ok(f"transcript written to {written}")


# ---------------------------------------------------------------- code
@command("audit", "Code", "evidence-based audit: @audit [--json] [--no-tools]")
def cmd_audit(app: App, args: str) -> None:
    flags = args.split()
    report = app.audit(security_only=False, use_tools="--no-tools" not in flags)
    _print_audit(app, report, "--json" in flags)


@command("security", "Code", "audit restricted to security findings")
def cmd_security(app: App, args: str) -> None:
    report = app.audit(security_only=True, use_tools="--no-tools" not in args.split())
    _print_audit(app, report, "--json" in args.split())


def _print_audit(app: App, report: dict, as_json: bool) -> None:
    from .audit import render_text

    app.console.line(json.dumps(report, indent=2) if as_json else render_text(report))
    app.console.info(f"report saved: {report['saved_to']}")


@command("test", "Code", "run the project's test command")
def cmd_test(app: App, args: str) -> None:
    if app.executor is None:
        raise AppError("open a project first")
    result = app.executor.run("run_tests", {})
    app.console.line(result.output)


@command("todo", "Code", "list TODO and FIXME markers in the project")
def cmd_todo(app: App, args: str) -> None:
    if app.executor is None:
        raise AppError("open a project first")
    out = [app.executor.run("search_text", {"query": q}).output for q in ("TODO", "FIXME")]
    app.console.line("\n".join(o for o in out if o and not o.startswith("no matches")) or "no TODO or FIXME markers")


@command("search", "Code", "search project files: @search <text>")
def cmd_search(app: App, args: str) -> None:
    if not args.strip():
        raise AppError("usage: @search <text>")
    result = app.executor.run("search_text", {"query": args.strip()})
    app.console.line(result.output)


@command("deps", "Code", "list declared dependencies")
def cmd_deps(app: App, args: str) -> None:
    if app.project is None:
        raise AppError("open a project first")
    deps = app.project.dependencies()
    app.console.table(["source", "dependency"], deps) if deps else app.console.info("no dependency manifests found")


@command("format", "Code", "run ruff format on the project (asks first)")
def cmd_format(app: App, args: str) -> None:
    rc, out = app.run_argv(["ruff", "format", "."])
    app.console.line(out)


@command("types", "Code", "run mypy on the project if installed")
def cmd_types(app: App, args: str) -> None:
    rc, out = app.run_argv(["mypy", "."])
    app.console.line(out)


@command("deadcode", "Code", "report unused imports, redefinitions and unused locals via ruff")
def cmd_deadcode(app: App, args: str) -> None:
    rc, out = app.run_argv(["ruff", "check", "--select", "F401,F811,F841", "."])
    app.console.line(out or "no findings")


def _read_for_prompt(app: App, path: str) -> str:
    result = app.executor.run("read_file", {"path": path, "max_lines": 400})
    if not result.ok:
        raise AppError(result.output)
    return result.output


@command("explain", "Code", "explain a file or the project: @explain [path]")
def cmd_explain(app: App, args: str) -> None:
    material = _read_for_prompt(app, args.strip()) if args.strip() else _project_context(app)
    _oneshot(app, f"Explain this clearly for an engineer. What it does, how it fits, and any risks:\n\n{material}")


@command("review", "Code", "review a file, or the working-tree diff if no path is given")
def cmd_review(app: App, args: str) -> None:
    if args.strip():
        material = _read_for_prompt(app, args.strip())
    else:
        material = git.diff(app.workspace.root) if app.workspace and git.is_repo(app.workspace.root) \
            else "(no git repository; give a path)"
    _oneshot(app, f"Review this for bugs, edge cases and weak patterns. Findings first, most severe first:\n\n{material}")


@command("explain-diff", "Code", "explain the current uncommitted changes")
def cmd_explain_diff(app: App, args: str) -> None:
    if not app.workspace or not git.is_repo(app.workspace.root):
        raise AppError("not a git repository")
    _oneshot(app, f"Explain these changes, the reasoning behind them and their risks:\n\n{git.diff(app.workspace.root)}")


@command("architecture", "Code", "describe the project architecture from its files")
def cmd_architecture(app: App, args: str) -> None:
    _oneshot(app, f"Describe this project's architecture, its main components and their boundaries:\n\n"
                  f"{_project_context(app)}")


def _agentic(app: App, instruction: str) -> None:
    app.set_mode("coding")
    app.start_turn(instruction)


@command("debug", "Code", "agentic: reproduce, find root cause, fix minimally: @debug <symptom>")
def cmd_debug(app: App, args: str) -> None:
    if not args.strip():
        raise AppError("usage: @debug <symptom or failing test>")
    _agentic(app, f"Debug this. Reproduce it with the tools, find the root cause, make the smallest fix, "
                  f"then run the tests:\n{args}")


@command("refactor", "Code", "agentic: refactor without changing behaviour: @refactor <target>")
def cmd_refactor(app: App, args: str) -> None:
    if not args.strip():
        raise AppError("usage: @refactor <file or description>")
    _agentic(app, f"Refactor {args} to be clearer without changing behaviour. Keep public interfaces. "
                  f"Run the tests after the change.")


@command("generate-tests", "Code", "agentic: write tests for a file: @generate-tests <path>")
def cmd_generate_tests(app: App, args: str) -> None:
    if not args.strip():
        raise AppError("usage: @generate-tests <path>")
    _agentic(app, f"Write focused tests for {args.strip()} using the project's existing test framework and "
                  f"conventions. Then run them.")


@command("docs", "Code", "agentic: add docstrings and docs for a file: @docs <path>")
def cmd_docs(app: App, args: str) -> None:
    if not args.strip():
        raise AppError("usage: @docs <path>")
    _agentic(app, f"Add accurate docstrings to {args.strip()}. Do not change behaviour.")


# ---------------------------------------------------------------- git
@command("diff", "Git", "show uncommitted changes: @diff [--staged] [path]")
def cmd_diff(app: App, args: str) -> None:
    if app.workspace is None or not git.is_repo(app.workspace.root):
        raise AppError("not a git repository")
    parts = args.split()
    staged = "--staged" in parts
    path = next((p for p in parts if p != "--staged"), None)
    app.console.line(git.diff(app.workspace.root, staged=staged, path=path))


@command("branch", "Git", "list, create or switch branches: @branch [name | switch name]")
def cmd_branch(app: App, args: str) -> None:
    if app.workspace is None or not git.is_repo(app.workspace.root):
        raise AppError("not a git repository")
    parts = args.split()
    if not parts or parts == ["list"]:
        app.console.line(git.list_branches(app.workspace.root))
    elif parts[0] == "switch" and len(parts) == 2:
        app.console.ok(git.switch_branch(app.workspace.root, parts[1]))
    elif len(parts) == 1:
        app.console.ok(git.create_branch(app.workspace.root, parts[0]))
    else:
        raise AppError("usage: @branch [list | <name> | switch <name>]")


# ---------------------------------------------------------------- not implemented
for _name, _summary in [
    ("benchmark", "benchmark a command"), ("impact", "change-impact analysis"),
    ("compare", "compare two versions"), ("milestones", "milestone tracking"),
    ("release", "prepare a release"), ("pr", "pull request description and submission"),
    ("changelog", "changelog generation"), ("review-commit", "review a commit"),
    ("worktree", "git worktree management"), ("migrate", "migration tooling"),
    ("api-docs", "API documentation generation"), ("design-lock", "design constraints"),
    ("design-audit", "design constraint audit"), ("design-diff", "design constraint diff"),
    ("browser", "browser testing"),
]:
    _not_implemented(_name, _summary)


def dispatch(app: App, line: str) -> None:
    text = line.strip()
    if not text.startswith("@"):
        raise AppError("commands start with @; type @help")
    body = text[1:]
    name, _, args = body.partition(" ")
    name = name.lower()
    name = ALIASES.get(name, name)
    cmd = COMMANDS.get(name)
    if cmd is None:
        near = difflib.get_close_matches(name, list(COMMANDS), n=3)
        hint = f" Did you mean: {', '.join('@' + n for n in near)}?" if near else ""
        raise AppError(f"unknown command @{name}.{hint}")
    try:
        cmd.handler(app, args.strip())
    except (AppError, WorkspaceError, ProjectError) as exc:
        app.console.error(str(exc))
    except (AllTargetsFailed, ProviderError) as exc:
        app.console.error(redact(str(exc), app.creds.known_secrets()))
    except OSError as exc:
        app.console.error(redact(str(exc), app.creds.known_secrets()))


def command_names() -> list[str]:
    return sorted("@" + name for name in COMMANDS)

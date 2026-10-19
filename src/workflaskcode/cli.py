"""Command-line entry point: `workflaskcode [subcommand]`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .app import App, AppError
from .config import ConfigError
from .paths import AgentHome
from .setup import run_setup
from .ui import Console

EXIT_OK, EXIT_FAILURE, EXIT_CONFIG, EXIT_INTERRUPTED = 0, 1, 2, 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workflaskcode",
        description="WorkFlaskCode: a terminal AI coding agent. Your code. Your models. Your workflow.",
    )
    parser.add_argument("--version", action="version", version=f"workflaskcode {__version__}")
    parser.add_argument("--home", help="agent home directory (default: platform location or $WORKFLASKCODE_HOME)")
    parser.add_argument("--project", help="project directory to open (default: current directory)")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("setup", help="run the configuration wizard")
    sub.add_parser("doctor", help="diagnose configuration, dependencies and workspace")
    models = sub.add_parser("models", help="list models from the configured provider")
    models.add_argument("--refresh", action="store_true", help="ignore the cache and fetch again")
    models.add_argument("--search", default="", help="filter by id or name")
    sub.add_parser("projects", help="list registered projects")
    resume = sub.add_parser("resume", help="restore a session and open the interface")
    resume.add_argument("session_id", nargs="?")
    audit = sub.add_parser("audit", help="run an evidence-based audit and print the report")
    audit.add_argument("path", nargs="?", help="project directory (default: --project or current directory)")
    audit.add_argument("--json", action="store_true", help="print the JSON report")
    audit.add_argument("--security", action="store_true", help="security findings only")
    audit.add_argument("--no-tools", action="store_true", help="skip ruff and bandit even if installed")
    sub.add_parser("where", help="print agent and project paths")
    return parser


def _console() -> Console:
    return Console(color=not os.environ.get("NO_COLOR") and sys.stdout.isatty())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    home = AgentHome(args.home) if args.home else AgentHome()
    console = _console()

    try:
        if args.command == "setup":
            return EXIT_OK if run_setup(home) else EXIT_FAILURE

        if not home.is_configured() and args.command in (None, "resume"):
            console.info("No configuration found. Starting setup.")
            if not run_setup(home):
                return EXIT_FAILURE

        app = App(home, console=console)
        if args.command == "doctor":
            from .doctor import run_doctor

            for level, title, detail in run_doctor(app):
                console.line(f"[{level:<4}] {title}: {detail}")
            return EXIT_OK

        if args.command == "projects":
            rows = [(p["name"], p["path"], p["last_opened_at"]) for p in app.state.list_projects()]
            console.table(["name", "path", "last opened"], rows) if rows else console.info("no projects yet")
            return EXIT_OK

        if args.command == "where":
            console.line(f"agent home : {home.root}")
            console.line(f"config     : {home.config_file}")
            console.line(f"state db   : {home.db_path}")
            console.line(f"project    : {Path(args.project or os.getcwd()).resolve()}")
            return EXIT_OK

        project_dir = args.project or (args.path if args.command == "audit" and args.path else None) or os.getcwd()
        if args.command == "models":
            return _cmd_models(app, console, args)
        if args.command == "audit":
            return _cmd_audit(app, console, args, project_dir)

        app.open_project(project_dir)
        if args.command == "resume":
            count = app.resume(args.session_id)
            console.ok(f"restored session {app.session_id} with {count} messages")

        from .repl import Repl

        Repl(app).run()
        return EXIT_OK
    except ConfigError as exc:
        console.error(f"configuration problem: {exc}. Fix config.toml or run: workflaskcode setup")
        return EXIT_CONFIG
    except AppError as exc:
        console.error(str(exc))
        return EXIT_FAILURE
    except KeyboardInterrupt:
        console.line("\ninterrupted")
        return EXIT_INTERRUPTED


def _cmd_models(app: App, console: Console, args: argparse.Namespace) -> int:
    models = app.models(refresh=args.refresh)
    needle = args.search.lower()
    shown = [m for m in models if needle in m.id.lower() or needle in m.name.lower()]
    console.table(["model id", "context", "price", "tools"],
                  [(m.id, m.context_length or "?", m.price_label(),
                    "yes" if m.supports_tools else "?") for m in shown[:200]])
    console.info(f"{len(shown)} of {len(models)} models")
    return EXIT_OK


def _cmd_audit(app: App, console: Console, args: argparse.Namespace, project_dir: str) -> int:
    import json

    from .audit import render_text

    app.open_project(project_dir)
    report = app.audit(security_only=args.security, use_tools=not args.no_tools)
    console.line(json.dumps(report, indent=2) if args.json else render_text(report))
    console.info(f"report saved: {report['saved_to']}")
    high = report["counts"].get("high", 0)
    return EXIT_FAILURE if high else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

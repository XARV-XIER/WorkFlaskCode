"""Diagnostics for `workflaskcode doctor`. Each check reports ok, warn or fail with detail."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys

Row = tuple[str, str, str]


def _module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run_doctor(app) -> list[Row]:
    rows: list[Row] = []
    rows.append(("ok" if sys.version_info >= (3, 11) else "fail", "python",
                 f"{sys.version.split()[0]} (3.11+ required)"))
    writable = app.home.root.is_dir() and os.access(app.home.root, os.W_OK)
    rows.append(("ok" if writable else "fail", "agent home", str(app.home.root)))
    rows.append(("ok" if app.home.config_file.exists() else "warn", "configuration",
                 str(app.home.config_file) + ("" if app.home.config_file.exists() else " (missing; run @setup)")))
    try:
        app.state.list_projects()
        rows.append(("ok", "state database", str(app.home.db_path)))
    except Exception as exc:
        rows.append(("fail", "state database", f"cannot read: {exc}"))
    cred_file = app.home.credentials_file
    if cred_file.exists() and os.name == "posix" and cred_file.stat().st_mode & 0o077:
        rows.append(("warn", "credentials file", f"permissions too open; run: chmod 600 {cred_file}"))
    general = app.config.data["general"]
    if not general["default_provider"]:
        rows.append(("warn", "default provider", "not set; run workflaskcode setup"))
    else:
        rows.append(("ok", "default provider", general["default_provider"]))
    if not general["coding_model"]:
        rows.append(("warn", "coding model", "not set"))
    else:
        rows.append(("ok", "coding model", general["coding_model"]))
    for name in app.config.providers():
        source = app.creds.source(name)
        level = "ok" if source != "not configured" else "warn"
        rows.append((level, f"provider {name}", f"key: {source}"))
    rows.append(("ok" if shutil.which("git") else "warn", "git",
                 "found" if shutil.which("git") else "not installed (git features disabled)"))
    for module, label in (("rich", "rich (pretty output)"), ("prompt_toolkit", "prompt_toolkit (editing, history)"),
                          ("keyring", "keyring (OS credential store)")):
        rows.append(("ok" if _module(module) else "info", label, "installed" if _module(module) else "not installed (optional)"))
    for tool in ("ruff", "mypy", "bandit"):
        rows.append(("ok" if shutil.which(tool) else "info", f"{tool} (audit/types)",
                     "found" if shutil.which(tool) else "not found (optional)"))
    if app.workspace is not None:
        rows.append(("ok", "project", str(app.workspace.root)))
        rows.append(("info", "project stack", ", ".join(app.project.stack()) or "unknown"))
    rows.append(("info", "permission mode", app.permission_mode()))
    return rows

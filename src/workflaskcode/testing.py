"""Detect the project's test command. Projects may override it in .workflaskcode/state.json."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def detect_test_command(root: Path, override: list[str] | None = None) -> list[str] | None:
    if override:
        return list(override)
    package_json = root / "package.json"
    if package_json.exists():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if isinstance(scripts, dict) and "test" in scripts:
            return ["npm", "test", "--silent"]
    if (root / "Cargo.toml").exists():
        return ["cargo", "test"]
    if (root / "go.mod").exists():
        return ["go", "test", "./..."]
    tests_dir = root / "tests"
    has_py_tests = (tests_dir.is_dir() and any(tests_dir.glob("test_*.py"))) or any(root.glob("test_*.py"))
    if has_py_tests:
        if importlib.util.find_spec("pytest") is not None:
            return [sys.executable, "-m", "pytest", "-q"]
        if tests_dir.is_dir():
            return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]
        return [sys.executable, "-m", "unittest", "discover", "-q"]
    return None


def read_test_override(project_meta: Path) -> list[str] | None:
    state_file = project_meta / "state.json"
    if not state_file.exists():
        return None
    try:
        value = json.loads(state_file.read_text(encoding="utf-8")).get("test_command")
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(value, list) and all(isinstance(v, str) for v in value) and value:
        return value
    return None

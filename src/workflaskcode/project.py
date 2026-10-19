"""Project metadata (.workflaskcode/) and project scaffolding templates."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .utils import atomic_write, now_iso

META_DIR = ".workflaskcode"
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ProjectError(Exception):
    """A project operation was refused."""


class Project:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.name = self.root.name or str(self.root)

    @property
    def meta(self) -> Path:
        return self.root / META_DIR

    def ensure_metadata(self) -> Path:
        """Create missing metadata files. Existing files are never overwritten."""
        self.meta.mkdir(exist_ok=True)
        state_file = self.meta / "state.json"
        if not state_file.exists():
            atomic_write(state_file, json.dumps({"created_at": now_iso(), "test_command": None}, indent=2))
        project_md = self.root / "PROJECT.md"
        if not project_md.exists():
            atomic_write(project_md, f"# {self.name}\n\nDescribe architecture, conventions and constraints here.\n")
        handoff = self.meta / "handoff.md"
        if not handoff.exists():
            atomic_write(handoff, "# Handoff\n\nNo handoff notes yet.\n")
        return self.meta

    def brief(self) -> str:
        project_md = self.root / "PROJECT.md"
        if project_md.exists():
            return project_md.read_text(encoding="utf-8", errors="replace")[:2000]
        return ""

    def write_handoff(self, text: str) -> None:
        self.meta.mkdir(exist_ok=True)
        atomic_write(self.meta / "handoff.md", f"# Handoff ({now_iso()})\n\n{text}\n")

    def stack(self) -> list[str]:
        found = []
        if (self.root / "pyproject.toml").exists() or (self.root / "requirements.txt").exists():
            found.append("python")
        if (self.root / "package.json").exists():
            found.append("node")
        if (self.root / "Cargo.toml").exists():
            found.append("rust")
        if (self.root / "go.mod").exists():
            found.append("go")
        if (self.root / "index.html").exists():
            found.append("html")
        return found

    def dependencies(self) -> list[tuple[str, str]]:
        deps: list[tuple[str, str]] = []
        pyproject = self.root / "pyproject.toml"
        if pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                for item in data.get("project", {}).get("dependencies", []):
                    deps.append(("pyproject.toml", str(item)))
            except tomllib.TOMLDecodeError:
                deps.append(("pyproject.toml", "(unparseable)"))
        requirements = self.root / "requirements.txt"
        if requirements.exists():
            for line in requirements.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    deps.append(("requirements.txt", line))
        package_json = self.root / "package.json"
        if package_json.exists():
            try:
                data = json.loads(package_json.read_text(encoding="utf-8"))
                for name, version in (data.get("dependencies") or {}).items():
                    deps.append(("package.json", f"{name} {version}"))
            except json.JSONDecodeError:
                deps.append(("package.json", "(unparseable)"))
        return deps


TEMPLATE_FILES = {
    "python-cli": {
        "pyproject.toml": (
            "[build-system]\nrequires = [\"setuptools>=68\"]\nbuild-backend = \"setuptools.build_meta\"\n\n"
            "[project]\nname = \"{name}\"\nversion = \"0.1.0\"\nrequires-python = \">=3.11\"\ndependencies = []\n\n"
            "[project.scripts]\n{name} = \"{pkg}.cli:main\"\n\n[tool.setuptools.packages.find]\nwhere = [\"src\"]\n"
        ),
        "src/{pkg}/__init__.py": "__version__ = \"0.1.0\"\n",
        "src/{pkg}/cli.py": (
            "import argparse\nimport sys\n\n\n"
            "def main(argv: list[str] | None = None) -> int:\n"
            "    parser = argparse.ArgumentParser(prog=\"{name}\", description=\"{name} command line tool\")\n"
            "    parser.add_argument(\"who\", nargs=\"?\", default=\"world\", help=\"who to greet\")\n"
            "    args = parser.parse_args(argv)\n"
            "    print(f\"Hello, {{args.who}}!\")\n"
            "    return 0\n\n\n"
            "if __name__ == \"__main__\":\n    sys.exit(main())\n"
        ),
        "tests/test_cli.py": (
            "import pathlib\nimport sys\nimport unittest\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / \"src\"))\n\n"
            "from {pkg}.cli import main\n\n\n"
            "class CliTest(unittest.TestCase):\n"
            "    def test_default_greeting(self):\n"
            "        self.assertEqual(main([]), 0)\n\n\n"
            "if __name__ == \"__main__\":\n    unittest.main()\n"
        ),
        "README.md": "# {name}\n\nA small Python command line tool.\n\n    python -m {pkg}\n",
        ".gitignore": "__pycache__/\n*.egg-info/\nbuild/\ndist/\n.venv/\n",
    },
}


def scaffold(name: str, parent: Path, template: str = "python-cli") -> Path:
    if template not in TEMPLATE_FILES:
        raise ProjectError(f"unknown template '{template}'; available: {', '.join(TEMPLATE_FILES)}")
    if not _NAME_RE.match(name):
        raise ProjectError("project names must start with a letter and use letters, digits, '-' or '_'")
    target = (parent / name).resolve()
    if target.exists() and any(target.iterdir()):
        raise ProjectError(f"{target} already exists and is not empty")
    pkg = name.replace("-", "_").lower()
    for rel, content in TEMPLATE_FILES[template].items():
        path = target / rel.format(pkg=pkg)
        atomic_write(path, content.format(name=name, pkg=pkg))
    Project(target).ensure_metadata()
    return target

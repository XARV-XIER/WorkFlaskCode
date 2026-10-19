"""Evidence-based static audit. Findings come from concrete matches or real tool output.

This is pattern analysis, not a security assessment. Its output is labelled that way.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .credentials import mask
from .tools import IGNORED_DIRS

MAX_FILE_BYTES = 1_000_000
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
TEXT_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php", ".sh",
    ".yml", ".yaml", ".toml", ".json", ".cfg", ".ini", ".env", ".md", ".txt", ".html", ".sql",
}
TEXT_NAMES = {"Dockerfile", ".env"}
DISCLAIMER = ("Static pattern analysis only. An empty result does not mean the project is secure; "
              "it means these specific patterns were not found.")


@dataclass
class Finding:
    id: str
    category: str
    severity: str
    file: str
    line: int | None
    evidence: str
    explanation: str
    recommendation: str
    confidence: str
    verified: bool
    tool: str


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    severity: str
    pattern: re.Pattern
    explanation: str
    recommendation: str
    secret: bool = False


RULES: list[Rule] = [
    Rule("SEC-SECRET-AWS", "security", "high", re.compile(r"AKIA[0-9A-Z]{16}"),
         "A string shaped like an AWS access key ID is committed in source.",
         "Revoke the key, move it to environment or a secret manager, and purge it from history.", True),
    Rule("SEC-SECRET-APIKEY", "security", "high",
         re.compile(r"(sk-[A-Za-z0-9_\-]{20,}|sk-or-v1-[A-Za-z0-9]{20,})"),
         "A string shaped like a provider API key is committed in source.",
         "Revoke the key, load it from the environment, and add the file to .gitignore.", True),
    Rule("SEC-PRIVATE-KEY", "security", "high",
         re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
         "A private key block is present in a text file.",
         "Remove the key from the repository, rotate it, and store it outside version control.", True),
    Rule("SEC-SHELL-TRUE", "security", "medium", re.compile(r"shell\s*=\s*True"),
         "subprocess is called with shell=True, so untrusted input can reach the shell.",
         "Pass an argument list without shell=True.", False),
    Rule("SEC-EVAL-DYNAMIC", "security", "medium", re.compile(r"\b(eval|exec)\s*\(\s*[^\s'\"\)]"),
         "eval or exec is called on a non-literal value.",
         "Replace dynamic evaluation with explicit parsing or a dispatch table.", False),
    Rule("SEC-PICKLE-LOAD", "security", "medium", re.compile(r"\bpickle\.loads?\("),
         "pickle deserialization can execute code from untrusted data.",
         "Use JSON or another data-only format for untrusted input.", False),
    Rule("SEC-YAML-UNSAFE", "security", "medium", re.compile(r"yaml\.load\((?![^)]*Loader)"),
         "yaml.load without an explicit safe Loader.",
         "Use yaml.safe_load.", False),
    Rule("SEC-TLS-VERIFY-OFF", "security", "high", re.compile(r"verify\s*=\s*False"),
         "TLS certificate verification is disabled.",
         "Re-enable verification and pin or configure a CA bundle if needed.", False),
    Rule("QUAL-BARE-EXCEPT", "quality", "low", re.compile(r"^\s*except\s*:"),
         "A bare except catches SystemExit and KeyboardInterrupt along with real errors.",
         "Catch the specific exception types you expect.", False),
    Rule("QUAL-SWALLOWED-ERROR", "quality", "low", re.compile(r"except\b[^\n:]*:\s*pass\s*$"),
         "An exception is caught and silently discarded.",
         "Log the error or handle it explicitly.", False),
    Rule("MAINT-NOT-IMPLEMENTED", "maintainability", "low", re.compile(r"raise NotImplementedError"),
         "A code path is explicitly unimplemented.",
         "Implement it or remove the reachable call site.", False),
    Rule("MAINT-TODO-MARKER", "maintainability", "info", re.compile(r"\b(TODO|FIXME|XXX)\b"),
         "An unresolved work marker is present.",
         "Resolve or track it in an issue.", False),
]


def _text_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() in TEXT_SUFFIXES or name in TEXT_NAMES:
                try:
                    if path.stat().st_size <= MAX_FILE_BYTES:
                        yield path
                except OSError:
                    continue


def _read_text(path: Path) -> str | None:
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def scan_static(root: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    seen: set[tuple[str, str, int]] = set()
    scanned = 0
    for path in _text_files(root):
        text = _read_text(path)
        if text is None:
            continue
        scanned += 1
        rel = str(path.relative_to(root))
        lines = text.splitlines()
        if len(lines) > 1000:
            findings.append(Finding(
                "MAINT-LARGE-FILE", "maintainability", "info", rel, None, f"{len(lines)} lines",
                "Very large files are harder to review and test.", "Split by responsibility.",
                "high", True, "static"))
        for number, line in enumerate(lines, 1):
            for rule in RULES:
                match = rule.pattern.search(line)
                if not match:
                    continue
                key = (rule.rule_id, rel, number)
                if key in seen:
                    continue
                seen.add(key)
                evidence = line.strip()[:160]
                if rule.secret:
                    evidence = evidence.replace(match.group(0), mask(match.group(0)))
                findings.append(Finding(
                    rule.rule_id, rule.category, rule.severity, rel, number, evidence,
                    rule.explanation, rule.recommendation, "high" if rule.secret else "medium",
                    True, "static"))
    return findings, scanned


Runner = Callable[[list[str]], tuple[int, str]]


def run_ruff(runner: Runner) -> tuple[list[Finding], str]:
    rc, out = runner(["ruff", "check", "--quiet", "--output-format", "concise", "."])
    if rc == 127:
        return [], "ruff not installed (skipped)"
    findings = []
    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) == 4 and parts[1].isdigit():
            code = parts[3].strip().split(" ", 1)[0]
            findings.append(Finding(
                f"RUFF-{code}", "quality", "low", parts[0], int(parts[1]), parts[3].strip()[:160],
                "Reported by ruff.", "See the ruff rule documentation for the fix.",
                "high", True, "ruff"))
    return findings, "ruff ran"


def run_bandit(runner: Runner) -> tuple[list[Finding], str]:
    rc, out = runner(["bandit", "-r", ".", "-f", "json", "-q"])
    if rc == 127:
        return [], "bandit not installed (skipped)"
    try:
        data = json.loads(out[out.find("{"):])
    except (ValueError, json.JSONDecodeError):
        return [], "bandit output could not be parsed (skipped)"
    findings = []
    for item in data.get("results", []):
        findings.append(Finding(
            str(item.get("test_id", "BANDIT")), "security",
            str(item.get("issue_severity", "medium")).lower(),
            str(item.get("filename", "")), item.get("line_number"),
            str(item.get("code", "")).strip()[:160], str(item.get("issue_text", "")),
            "See the bandit test documentation.", str(item.get("issue_confidence", "")).lower(),
            True, "bandit"))
    return findings, "bandit ran"


def run_audit(root: Path, runner: Runner | None = None, *, security_only: bool = False,
              use_tools: bool = True) -> dict:
    findings, scanned = scan_static(root)
    notes = [f"static scan covered {scanned} text files"]
    if use_tools and runner is not None:
        for fn in (run_ruff, run_bandit):
            extra, note = fn(runner)
            findings.extend(extra)
            notes.append(note)
    if security_only:
        findings = [f for f in findings if f.category == "security"]
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.file, f.line or 0))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return {"root": str(root), "disclaimer": DISCLAIMER, "notes": notes,
            "counts": counts, "findings": [asdict(f) for f in findings]}


def render_text(report: dict) -> str:
    lines = [f"Audit of {report['root']}", DISCLAIMER, ""]
    lines += [f"- {note}" for note in report["notes"]]
    lines.append("")
    if not report["findings"]:
        lines.append("No findings from the checks that ran.")
        return "\n".join(lines)
    lines.append(f"{len(report['findings'])} findings  " + "  ".join(
        f"{sev}={n}" for sev, n in sorted(report["counts"].items(), key=lambda kv: SEVERITY_ORDER.get(kv[0], 9))))
    for f in report["findings"]:
        where = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
        lines.append(f"\n[{f['severity'].upper():<6}] {f['id']}  {where}  ({f['tool']})")
        lines.append(f"  evidence: {f['evidence']}")
        lines.append(f"  why: {f['explanation']}")
        lines.append(f"  fix: {f['recommendation']}")
    return "\n".join(lines)


def write_report(report: dict, reports_dir: Path, stamp: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"audit-{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path

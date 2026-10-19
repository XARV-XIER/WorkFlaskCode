"""Console output. Uses Rich when installed; otherwise plain text. Thread-safe."""

from __future__ import annotations

import sys
import threading
from typing import Sequence

STYLES = {
    "agent": "bold cyan", "side": "bold magenta", "tool": "dim", "file": "green",
    "test": "bold yellow", "info": "dim", "error": "bold red", "warn": "yellow",
    "ok": "green", "approval": "bold yellow", "heading": "bold",
}
LABELS = {
    "agent": "agent", "side": "side", "tool": "tool", "file": "file",
    "test": "test", "info": "info", "error": "error", "approval": "approval",
}


class Console:
    def __init__(self, color: bool = True, stream=None) -> None:
        self._lock = threading.RLock()
        self._stream = stream
        self._rich = None
        if color:
            try:
                from rich.console import Console as RichConsole

                self._rich = RichConsole(highlight=False, soft_wrap=True, file=stream)
            except ImportError:
                self._rich = None

    @property
    def rich_enabled(self) -> bool:
        return self._rich is not None

    def _out(self, text: str, style: str | None = None) -> None:
        with self._lock:
            if self._rich is not None:
                self._rich.print(text, style=style, markup=False, highlight=False)
            else:
                print(text, file=self._stream or sys.stdout, flush=True)

    def line(self, text: str = "", kind: str | None = None) -> None:
        self._out(text, STYLES.get(kind or "", None))

    def event(self, kind: str, text: str) -> None:
        label = LABELS.get(kind)
        message = f"  {label}: {text}" if label else f"  {text}"
        self._out(message, STYLES.get(kind))

    def info(self, text: str) -> None:
        self._out(text, STYLES["info"])

    def ok(self, text: str) -> None:
        self._out(text, STYLES["ok"])

    def warn(self, text: str) -> None:
        self._out(f"warning: {text}", STYLES["warn"])

    def error(self, text: str) -> None:
        self._out(f"error: {text}", STYLES["error"])

    def heading(self, text: str) -> None:
        self._out(text, STYLES["heading"])

    def agent_block(self, label: str, text: str, kind: str = "agent") -> None:
        self._out(f"[{label}]", STYLES[kind])
        self._out(text)

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
        cells = [[str(c) for c in row] for row in rows]
        if self._rich is not None:
            from rich.table import Table

            table = Table(show_header=True, header_style="bold")
            for header in headers:
                table.add_column(header)
            for row in cells:
                table.add_row(*row)
            with self._lock:
                self._rich.print(table)
            return
        widths = [len(h) for h in headers]
        for row in cells:
            for i, value in enumerate(row):
                widths[i] = min(max(widths[i], len(value)), 60)
        self._out("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
        self._out("  ".join("-" * w for w in widths))
        for row in cells:
            self._out("  ".join(v[: widths[i]].ljust(widths[i]) for i, v in enumerate(row)))

    def banner(self, lines: Sequence[str]) -> None:
        for text in lines:
            self._out(text, STYLES["heading"] if text.startswith("WorkFlaskCode") else None)

    def clear(self) -> None:
        if sys.stdout.isatty():
            self._out("\033[2J\033[H", None)

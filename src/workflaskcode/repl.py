"""Interactive loop: prompt, dispatch @commands, start background turns."""

from __future__ import annotations

import contextlib

from .app import App, AppError
from .commands import command_names, dispatch
from .credentials import redact


class Repl:
    def __init__(self, app: App) -> None:
        self.app = app
        self._session = self._make_prompt_session()

    def _make_prompt_session(self):
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.history import FileHistory
        except ImportError:
            return None
        self.app.home.history_file.parent.mkdir(parents=True, exist_ok=True)
        return PromptSession(
            history=FileHistory(str(self.app.home.history_file)),
            completer=WordCompleter(command_names(), sentence=True),
        )

    def _patch_stdout(self):
        if self._session is None:
            return contextlib.nullcontext()
        from prompt_toolkit.patch_stdout import patch_stdout

        return patch_stdout(raw=True)

    def _read(self, label: str) -> str | None:
        try:
            if self._session is not None:
                return self._session.prompt(label)
            return input(label)
        except EOFError:
            return None
        except KeyboardInterrupt:
            if self.app.busy():
                self.app.cancel_turn()
                self.app.console.warn("cancelling the main task; progress is saved")
            else:
                self.app.console.info("press Ctrl+D or type @exit to quit")
            return ""

    def _label(self) -> str:
        if self.app.pending_approval():
            return "approval> "
        return "chat> " if self.app.mode == "chat" else "code> "

    def run(self) -> None:
        for line in self.app.banner_lines():
            self.app.console.line(line, "heading" if line.startswith("WorkFlaskCode") else None)
        self.app.console.line("Type @help for commands. Ctrl+C cancels a running task.")
        with self._patch_stdout():
            while not self.app.exit_requested:
                line = self._read(self._label())
                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                while line.endswith("\\"):
                    more = self._read("... ")
                    if more is None:
                        break
                    line = line[:-1] + "\n" + more.strip()
                self._handle(line)
        self._wait_for_running_task()
        self.app.shutdown()

    def _wait_for_running_task(self) -> None:
        """End of input lets a running task finish; Ctrl+C cancels it and keeps its progress."""
        if not self.app.busy():
            return
        self.app.console.info("waiting for the running task to finish (Ctrl+C cancels it)")
        try:
            while self.app.busy():
                self.app._turn_thread.join(0.5)
        except KeyboardInterrupt:
            self.app.cancel_turn()
            self.app._turn_thread.join(5)

    def _handle(self, line: str) -> None:
        known = self.app.creds.known_secrets()
        try:
            if line.startswith("@"):
                dispatch(self.app, line)
            else:
                self.app.start_turn(line)
        except AppError as exc:
            self.app.console.error(redact(str(exc), known))
        except Exception as exc:  # the interface must survive any single failure
            self.app.console.error(redact(f"unexpected failure: {exc}", known))

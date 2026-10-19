"""@btw side conversations: read-only, separate history, cannot touch the main task."""

from __future__ import annotations

import threading
from typing import Callable

from .router import Cancelled, ModelRouter
from .state import StateStore

SIDE_SYSTEM = (
    "You are answering a quick side question while another agent works on a coding task. "
    "You are read-only: you cannot change files or run commands. Be concise and accurate."
)


class SideChat:
    def __init__(self, router: ModelRouter, state: StateStore, session_id: str,
                 emit: Callable[[str, str], None], max_concurrent: int = 2) -> None:
        self.router = router
        self.state = state
        self.session_id = session_id
        self.emit = emit
        self._slots = threading.BoundedSemaphore(max(1, max_concurrent))
        self._lock = threading.Lock()
        self._history: list[dict] = []
        self._jobs: dict[int, tuple[threading.Event, threading.Thread]] = {}
        self._counter = 0

    def ask(self, question: str, snapshot: str = "") -> int:
        with self._lock:
            self._counter += 1
            job_id = self._counter
            cancel = threading.Event()
            self._history.append({"role": "user", "content": question, "tool_calls": None,
                                  "tool_call_id": None})
            self.state.add_message(self.session_id, "btw", "user", {"content": question})
            thread = threading.Thread(
                target=self._run, args=(job_id, cancel, snapshot), daemon=True, name=f"btw-{job_id}",
            )
            self._jobs[job_id] = (cancel, thread)
        thread.start()
        return job_id

    def _run(self, job_id: int, cancel: threading.Event, snapshot: str) -> None:
        acquired = False
        try:
            while not acquired:
                if cancel.is_set():
                    raise Cancelled()
                acquired = self._slots.acquire(timeout=0.2)
            with self._lock:
                messages = list(self._history[-12:])
            system = SIDE_SYSTEM + (f"\n\nRead-only snapshot:\n{snapshot}" if snapshot else "")
            response, _ = self.router.generate("chat", messages, system=system, tools=None,
                                               cancel=cancel, session_id=self.session_id)
            with self._lock:
                self._history.append({"role": "assistant", "content": response.text, "tool_calls": None,
                                      "tool_call_id": None})
            self.state.add_message(self.session_id, "btw", "assistant", {"content": response.text})
            self.emit("side", f"[{job_id}] {response.text}")
        except Cancelled:
            self.emit("side_info", f"side question {job_id} cancelled")
        except Exception as exc:  # thread boundary: report, never crash the main task
            self.emit("side_error", f"side question {job_id} failed: {exc}")
        finally:
            if acquired:
                self._slots.release()
            with self._lock:
                self._jobs.pop(job_id, None)

    def cancel(self, job_id: int | None = None) -> int:
        with self._lock:
            targets = [job_id] if job_id is not None else list(self._jobs)
            count = 0
            for jid in targets:
                if jid in self._jobs:
                    self._jobs[jid][0].set()
                    count += 1
        return count

    def active(self) -> int:
        with self._lock:
            return len(self._jobs)

    def wait_all(self, timeout: float = 5.0) -> None:
        with self._lock:
            threads = [t for _, t in self._jobs.values()]
        for thread in threads:
            thread.join(timeout)

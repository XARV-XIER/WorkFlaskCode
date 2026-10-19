"""The bounded tool loop that drives coding and chat turns."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .providers import ToolCall
from .router import AllTargetsFailed, BudgetExceeded, Cancelled, ModelRouter, NotConfigured
from .state import StateStore
from .tools import ToolExecutor

CODING_SYSTEM = (
    "You are WorkFlaskCode, a terminal software engineering agent working in the project at {root}.\n"
    "Rules:\n"
    "- Inspect existing files before changing them, and keep changes minimal.\n"
    "- Use tools for every file or command action. Never invent file contents.\n"
    "- After changes, call run_tests when a test command exists.\n"
    "- Never claim tests passed unless a run_tests result reports PASSED.\n"
    "- Finish with a short summary: what changed, and what was verified.\n"
)
CHAT_SYSTEM = (
    "You are WorkFlaskCode in chat mode. Answer clearly and concisely. You cannot modify files or "
    "run commands in this mode. If the user asks for changes, tell them to switch with @codingagent."
)
REPEAT_LIMIT = 3
KEEP_RECENT_MESSAGES = 24


@dataclass
class TurnResult:
    status: str  # completed | cancelled | blocked | step_limit
    text: str = ""
    steps: int = 0
    tool_calls: int = 0
    error: str = ""
    changed: list[str] = field(default_factory=list)


def _serialize_calls(calls: list[ToolCall] | None) -> list[dict[str, Any]] | None:
    if not calls:
        return None
    return [{"id": c.id, "name": c.name, "arguments": c.arguments, "parse_error": c.parse_error}
            for c in calls]


class Agent:
    def __init__(
        self,
        *,
        root: Path,
        session_id: str,
        router: ModelRouter,
        executor: ToolExecutor,
        state: StateStore,
        max_steps: int,
        context_budget: int,
        emit: Callable[[str, str], None],
        mode: str = "coding",
        brief: str = "",
        on_compact: Callable[[str], None] | None = None,
    ) -> None:
        self.root = root
        self.session_id = session_id
        self.router = router
        self.executor = executor
        self.state = state
        self.max_steps = max_steps
        self.context_budget = context_budget
        self.emit = emit
        self.mode = mode
        self.brief = brief
        self.on_compact = on_compact
        self.messages: list[dict[str, Any]] = []

    # ---- message bookkeeping
    def load(self, rows: list[dict[str, Any]]) -> None:
        self.messages = []
        for row in rows:
            payload = row["payload"]
            calls = None
            if payload.get("tool_calls"):
                calls = [ToolCall(id=c["id"], name=c["name"], arguments=c["arguments"],
                                  parse_error=c.get("parse_error")) for c in payload["tool_calls"]]
            self.messages.append({"role": row["role"], "content": payload.get("content", ""),
                                  "tool_calls": calls, "tool_call_id": payload.get("tool_call_id")})

    def _add(self, role: str, content: str, tool_calls: list[ToolCall] | None = None,
             tool_call_id: str | None = None) -> None:
        self.messages.append({"role": role, "content": content, "tool_calls": tool_calls,
                              "tool_call_id": tool_call_id})
        self.state.add_message(self.session_id, "main", role, {
            "content": content, "tool_calls": _serialize_calls(tool_calls), "tool_call_id": tool_call_id,
        })

    def clear(self) -> None:
        self.messages = []

    def _system(self) -> str:
        base = CODING_SYSTEM.format(root=self.root) if self.mode == "coding" else CHAT_SYSTEM
        if self.brief:
            base += f"\nProject brief (PROJECT.md):\n{self.brief}\n"
        return base

    def _size(self) -> int:
        total = 0
        for m in self.messages:
            total += len(m.get("content") or "")
            for c in m.get("tool_calls") or []:
                total += len(json.dumps(c.arguments))
        return total

    def compact(self, force: bool = False) -> str:
        """Replace old turns with a deterministic summary. Files stay authoritative."""
        if not force and (self._size() <= self.context_budget or len(self.messages) <= KEEP_RECENT_MESSAGES):
            return ""
        cut = max(0, len(self.messages) - KEEP_RECENT_MESSAGES)
        while cut < len(self.messages) and self.messages[cut]["role"] != "user":
            cut += 1
        if cut <= 0 or cut >= len(self.messages):
            return ""
        dropped = self.messages[:cut]
        paths = sorted({c.arguments.get("path") for m in dropped for c in (m.get("tool_calls") or [])
                        if isinstance(c.arguments, dict) and c.arguments.get("path")})
        goals = [m["content"][:160] for m in dropped if m["role"] == "user"][-5:]
        summary = (
            f"[Context compacted: {len(dropped)} earlier messages removed. "
            f"Paths touched: {', '.join(paths) or 'none'}. "
            f"Recent earlier requests: {' | '.join(goals) or 'none'}. "
            "Re-read files before relying on their earlier contents.]"
        )
        self.messages = [{"role": "user", "content": summary, "tool_calls": None, "tool_call_id": None}] \
            + self.messages[cut:]
        if self.on_compact:
            self.on_compact(summary)
        self.emit("info", f"context compacted ({len(dropped)} messages summarized)")
        return summary

    def _wire(self) -> list[dict[str, Any]]:
        return list(self.messages)

    # ---- the loop
    def ask(self, text: str, cancel: threading.Event) -> TurnResult:
        self._add("user", text)
        coding = self.mode == "coding"
        role = "coding" if coding else "chat"
        tools = self.executor.schemas() if coding else None
        result = TurnResult(status="completed")
        repeats: dict[tuple[str, str], int] = {}

        while True:
            if cancel.is_set():
                result.status = "cancelled"
                return result
            if result.steps >= self.max_steps:
                result.status = "step_limit"
                result.error = f"step limit of {self.max_steps} reached; send a follow-up to continue"
                return result
            result.steps += 1
            self.compact()
            try:
                response, target = self.router.generate(
                    role, self._wire(), system=self._system(), tools=tools,
                    cancel=cancel, session_id=self.session_id,
                )
            except Cancelled:
                result.status = "cancelled"
                return result
            except (AllTargetsFailed, NotConfigured, BudgetExceeded) as exc:
                result.status = "blocked"
                result.error = str(exc)
                return result

            self._add("assistant", response.text, response.tool_calls or None)
            if not response.tool_calls:
                result.text = response.text
                return result

            for index, call in enumerate(response.tool_calls):
                if cancel.is_set():
                    for pending in response.tool_calls[index:]:
                        self._add("tool", "cancelled before execution", tool_call_id=pending.id)
                    result.status = "cancelled"
                    return result
                result.tool_calls += 1
                key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
                repeats[key] = repeats.get(key, 0) + 1
                self.emit("tool", f"{call.name} {_brief_args(call.arguments)}")
                if call.parse_error:
                    output = f"error: {call.parse_error}"
                elif repeats[key] >= REPEAT_LIMIT:
                    output = "refused: this identical call was already made several times; try a different approach"
                else:
                    tool_result = self.executor.run(call.name, call.arguments)
                    output = tool_result.output
                    for changed in tool_result.changed:
                        if changed not in result.changed:
                            result.changed.append(changed)
                        self.emit("file", changed)
                    if tool_result.test:
                        verdict = "passed" if tool_result.ok else "failed"
                        self.emit("test", f"tests {verdict}: {tool_result.test['command']}")
                self._add("tool", output, tool_call_id=call.id)


def _brief_args(arguments: dict[str, Any]) -> str:
    parts = []
    for key, value in arguments.items():
        text = str(value).replace("\n", " ")
        parts.append(f"{key}={text[:60]!r}")
    return " ".join(parts)[:160]

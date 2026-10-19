"""Durable SQLite state: projects, sessions, messages, tasks, usage, checkpoints."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .providers.base import Usage
from .utils import now_iso

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS projects (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    mode TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    role TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, channel);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_id TEXT,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    files TEXT NOT NULL DEFAULT '[]',
    validation TEXT NOT NULL DEFAULT '',
    failure TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL,
    error_kind TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id TEXT,
    label TEXT NOT NULL,
    manifest TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

TASK_COLUMNS = (
    "id", "session_id", "parent_id", "description", "status", "attempts", "model",
    "files", "validation", "failure", "next_action", "checkpoint_id", "created_at", "updated_at",
)
SESSION_FIELDS = {"mode", "provider", "model", "status"}


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self._conn.executescript(SCHEMA_V1)
                self._conn.execute("PRAGMA user_version = 1")
                self._conn.commit()

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid or 0

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # projects
    def upsert_project(self, path: str, name: str) -> None:
        now = now_iso()
        self._write(
            "INSERT INTO projects(path, name, created_at, last_opened_at) VALUES (?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET last_opened_at = excluded.last_opened_at",
            (path, name, now, now),
        )

    def list_projects(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM projects ORDER BY last_opened_at DESC")

    # sessions
    def create_session(self, sid: str, project_path: str, mode: str, provider: str, model: str) -> None:
        now = now_iso()
        self._write(
            "INSERT INTO sessions(id, project_path, mode, provider, model, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, project_path, mode, provider, model, "active", now, now),
        )

    def update_session(self, sid: str, **fields: Any) -> None:
        bad = set(fields) - SESSION_FIELDS
        if bad:
            raise ValueError(f"unknown session fields: {sorted(bad)}")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._write(
            f"UPDATE sessions SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), now_iso(), sid),
        )

    def get_session(self, sid: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM sessions WHERE id = ?", (sid,))

    def last_session(self, project_path: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM sessions WHERE project_path = ? ORDER BY updated_at DESC LIMIT 1",
            (project_path,),
        )

    def list_sessions(self, project_path: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if project_path:
            return self._rows(
                "SELECT * FROM sessions WHERE project_path = ? ORDER BY updated_at DESC LIMIT ?",
                (project_path, limit),
            )
        return self._rows("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,))

    # messages
    def add_message(self, sid: str, channel: str, role: str, payload: dict[str, Any]) -> None:
        self._write(
            "INSERT INTO messages(session_id, channel, role, payload, created_at) VALUES (?,?,?,?,?)",
            (sid, channel, role, json.dumps(payload, ensure_ascii=False), now_iso()),
        )

    def list_messages(self, sid: str, channel: str = "main") -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT role, payload, created_at FROM messages WHERE session_id = ? AND channel = ? ORDER BY id",
            (sid, channel),
        )
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows

    def search_messages(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT session_id, role, payload, created_at FROM messages "
            "WHERE channel = 'main' AND payload LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        )
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows

    # tasks
    def put_task(self, task: dict[str, Any]) -> None:
        values = []
        for column in TASK_COLUMNS:
            value = task.get(column)
            if column == "files":
                value = json.dumps(value or [])
            values.append(value)
        placeholders = ",".join("?" for _ in TASK_COLUMNS)
        self._write(
            f"INSERT OR REPLACE INTO tasks({','.join(TASK_COLUMNS)}) VALUES ({placeholders})",
            tuple(values),
        )

    def get_task(self, tid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM tasks WHERE id = ?", (tid,))
        if row:
            row["files"] = json.loads(row["files"] or "[]")
        return row

    def list_tasks(self, sid: str) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at, id", (sid,))
        for row in rows:
            row["files"] = json.loads(row["files"] or "[]")
        return rows

    # usage
    def add_usage(self, sid: str | None, provider: str, model: str, usage: Usage | None,
                  duration_ms: int, ok: bool, error_kind: str | None) -> None:
        self._write(
            "INSERT INTO usage(session_id, provider, model, input_tokens, output_tokens, cached_tokens,"
            " cost_usd, duration_ms, ok, error_kind, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, provider, model,
                usage.input_tokens if usage else 0,
                usage.output_tokens if usage else 0,
                usage.cached_tokens if usage else 0,
                usage.cost_usd if usage else None,
                duration_ms, 1 if ok else 0, error_kind, now_iso(),
            ),
        )

    def usage_summary(self) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT provider, model, COUNT(*) AS requests, SUM(ok) AS successes,"
            " SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,"
            " SUM(COALESCE(cost_usd, 0)) AS reported_cost_usd, SUM(duration_ms) AS total_ms"
            " FROM usage GROUP BY provider, model ORDER BY requests DESC"
        )

    def session_cost(self, sid: str) -> float:
        row = self._one("SELECT SUM(COALESCE(cost_usd, 0)) AS total FROM usage WHERE session_id = ?", (sid,))
        return float(row["total"] or 0.0) if row else 0.0

    # checkpoints
    def save_checkpoint(self, cp: dict[str, Any]) -> None:
        manifest = cp["manifest"] if isinstance(cp["manifest"], str) else json.dumps(cp["manifest"])
        self._write(
            "INSERT OR REPLACE INTO checkpoints(id, session_id, task_id, label, manifest, status, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (cp["id"], cp["session_id"], cp.get("task_id"), cp["label"], manifest,
             cp["status"], cp["created_at"]),
        )

    def get_checkpoint(self, cid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM checkpoints WHERE id = ?", (cid,))
        if row:
            row["manifest"] = json.loads(row["manifest"])
        return row

    def list_checkpoints(self, sid: str | None = None) -> list[dict[str, Any]]:
        if sid:
            rows = self._rows("SELECT * FROM checkpoints WHERE session_id = ? ORDER BY created_at DESC", (sid,))
        else:
            rows = self._rows("SELECT * FROM checkpoints ORDER BY created_at DESC LIMIT 50")
        for row in rows:
            row["manifest"] = json.loads(row["manifest"])
        return rows

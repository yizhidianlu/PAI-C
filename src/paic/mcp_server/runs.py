"""Cross-process run registry.

Each LangGraph run gets a row in a small SQLite table living at
``~/.paic/runs_index.sqlite``. The actual graph state is in the project's
``checkpoints.sqlite``; this table only tracks **what** runs exist, **where**
they live, and **what** their last known status was.

We use SQLite (not in-memory) because the MCP server is a separate process
from Claude Code — ``/paic-resume`` may run after the server restarts, and we
need to discover existing runs without re-traversing every project's
checkpoint DB.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Iterator, Literal, Optional, TypedDict

from paic.config import ensure_global_dirs, load_config

RunKind = Literal["ideate", "experiment", "review", "writing"]
RunStatus = Literal["running", "awaiting_input", "done", "error", "cancelled"]

_CONN: Optional[sqlite3.Connection] = None
_LOCK = RLock()


class RunRecord(TypedDict):
    run_id: str
    kind: RunKind
    project_dir: str
    thread_id: str
    status: RunStatus
    current_node: str | None
    created_at: str
    updated_at: str
    error: str | None


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    project_dir   TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    status        TEXT NOT NULL,
    current_node  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_dir);
"""


def _connection() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            return _CONN
        cfg = ensure_global_dirs()
        path = cfg.runs_index_db
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        _CONN = conn
        return conn


def reset_registry_cache() -> None:
    """Close the cached connection — used by tests after redirecting PAIC_HOME."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:  # pragma: no cover
                pass
            _CONN = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def register(
    *,
    run_id: str,
    kind: RunKind,
    project_dir: str | Path,
    thread_id: str | None = None,
    status: RunStatus = "running",
    current_node: str | None = None,
) -> RunRecord:
    """Insert a new run row."""
    row = {
        "run_id": run_id,
        "kind": kind,
        "project_dir": str(Path(project_dir).resolve()),
        "thread_id": thread_id or run_id,
        "status": status,
        "current_node": current_node,
        "created_at": _now(),
        "updated_at": _now(),
        "error": None,
    }
    with _LOCK:
        _connection().execute(
            "INSERT OR REPLACE INTO runs "
            "(run_id, kind, project_dir, thread_id, status, current_node, created_at, updated_at, error) "
            "VALUES (:run_id, :kind, :project_dir, :thread_id, :status, :current_node, :created_at, :updated_at, :error)",
            row,
        )
    return row  # type: ignore[return-value]


def update(
    run_id: str,
    *,
    status: RunStatus | None = None,
    current_node: str | None = None,
    error: str | None = None,
) -> None:
    fields = []
    values: dict[str, object] = {"run_id": run_id, "updated_at": _now()}
    if status is not None:
        fields.append("status = :status")
        values["status"] = status
    if current_node is not None:
        fields.append("current_node = :current_node")
        values["current_node"] = current_node
    if error is not None:
        fields.append("error = :error")
        values["error"] = error
    if not fields:
        return
    sql = f"UPDATE runs SET {', '.join(fields)}, updated_at = :updated_at WHERE run_id = :run_id"
    with _LOCK:
        _connection().execute(sql, values)


def get(run_id: str) -> RunRecord | None:
    with _LOCK:
        cur = _connection().execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
    return dict(row) if row else None  # type: ignore[return-value]


def list_runs(
    project_dir: str | Path | None = None,
    status_filter: RunStatus | None = None,
) -> list[RunRecord]:
    sql = "SELECT * FROM runs WHERE 1=1"
    args: list[object] = []
    if project_dir is not None:
        sql += " AND project_dir = ?"
        args.append(str(Path(project_dir).resolve()))
    if status_filter is not None:
        sql += " AND status = ?"
        args.append(status_filter)
    sql += " ORDER BY updated_at DESC"
    with _LOCK:
        cur = _connection().execute(sql, args)
        rows = cur.fetchall()
    return [dict(r) for r in rows]  # type: ignore[return-value]


def delete(run_id: str) -> None:
    with _LOCK:
        _connection().execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Group several updates into one transaction (rare; mostly for tests)."""
    with _LOCK:
        conn = _connection()
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

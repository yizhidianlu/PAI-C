"""SqliteSaver factory.

We materialize one SQLite file per project at ``<project>/.paic/state/checkpoints.sqlite``.
LangGraph's saver uses a single connection per file; opening multiple
:class:`SqliteSaver` instances against the same DB from the same process is
safe because we wrap writes in WAL mode.

Use :func:`get_checkpointer` from inside graph builders / tool handlers; it
caches an instance per project_dir to avoid re-opening the connection on every
tool call.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

from langgraph.checkpoint.sqlite import SqliteSaver

from paic.workspace.paths import ProjectPaths

_CACHE: dict[str, SqliteSaver] = {}
_LOCK = RLock()


def get_checkpointer(paths: ProjectPaths) -> SqliteSaver:
    """Return a ``SqliteSaver`` bound to ``<project>/.paic/state/checkpoints.sqlite``.

    Cached per project root for the lifetime of the process.
    """
    key = str(paths.root.resolve())
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(paths.checkpoints_db),
            check_same_thread=False,
            isolation_level=None,  # let SqliteSaver manage transactions
        )
        # WAL improves concurrency; safe even with our single-process server.
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        saver = SqliteSaver(conn)
        _CACHE[key] = saver
        return saver


def reset_checkpointer_cache() -> None:
    with _LOCK:
        for saver in _CACHE.values():
            try:
                saver.conn.close()
            except Exception:  # pragma: no cover
                pass
        _CACHE.clear()


@contextmanager
def project_checkpointer(paths: ProjectPaths) -> Iterator[SqliteSaver]:
    """Context-manager flavor for one-shot scripts/tests."""
    saver = get_checkpointer(paths)
    try:
        yield saver
    finally:
        # We don't close — the cache keeps the connection alive for reuse.
        pass

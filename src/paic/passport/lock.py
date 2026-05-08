"""Cross-platform advisory file lock for the passport ledger.

Implements ARS iron rule §9: the read-check-append sequence on the
ledger MUST hold an exclusive advisory lock for its entire duration.
Releasing between steps re-opens the double-resume race the rule
exists to prevent.

Backend: ``portalocker`` — POSIX (``fcntl``) and Windows
(``msvcrt.locking``) are wrapped behind a uniform API. Implementations
that cannot acquire OS-level exclusion within ``timeout_sec`` raise
:class:`LockTimeout`; the orchestrator must NOT silently degrade.

Default timeout: 30s. ARS spec allows up to 60s; we use 30s because
the passport append is a few-KB write + fsync — 30s is two orders of
magnitude over reasonable latency, anything beyond suggests a stuck
process.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Iterator

import portalocker

DEFAULT_TIMEOUT_SEC = 30.0


class PassportLockError(RuntimeError):
    """Base class for passport-lock failures."""


class LockTimeout(PassportLockError):
    """Lock acquisition timed out — surfaced to caller as hard error.

    ARS iron rule §9: do not retry automatically. A timeout indicates
    a stuck or crashed peer, not lock contention. Surface the error
    to the user with the absolute lock path so they can investigate.
    """


@contextlib.contextmanager
def passport_lock(
    lock_path: Path,
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> Iterator[None]:
    """Acquire an exclusive advisory lock on ``lock_path``.

    Context-manager API: caller wraps the entire read-check-append
    sequence in ``with passport_lock(...):``. The lock file is created
    if missing. POSIX: ``fcntl.LOCK_EX``. Windows: ``msvcrt.locking``
    via portalocker abstraction.

    ``timeout_sec`` defaults to 30s; the runtime sleeps in tiny
    increments (portalocker default ~0.1s) until acquisition or
    timeout. Pass ``timeout_sec=0`` for non-blocking probe (raises
    :class:`LockTimeout` immediately on contention).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(
        str(lock_path),
        mode="a+",
        timeout=max(timeout_sec, 0.001),
        flags=portalocker.LOCK_EX,
        fail_when_locked=False,
    )
    try:
        lock.acquire()
    except portalocker.LockException as exc:
        raise LockTimeout(
            f"Could not acquire passport lock at {lock_path} within "
            f"{timeout_sec}s. Another process may be holding it. "
            f"(Underlying: {exc!r})"
        ) from exc

    try:
        yield
    finally:
        try:
            lock.release()
        except (portalocker.LockException, OSError):
            # Best effort — release errors usually mean someone else
            # already cleaned up. Don't propagate; the actual write
            # already succeeded under the held lock.
            pass

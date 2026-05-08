"""Material Passport — append-only stage-boundary ledger (ARS-fusion P1-2).

Complementary to LangGraph's ``SqliteSaver`` checkpoints (which manage
intra-graph state inside one fire-and-poll run): the passport is a
**stage-level** ledger that lives across SKILL boundaries and across
Claude Code sessions, so a long pipeline run (init → search → ingest →
... → finalize) can be interrupted and resumed without replaying turns.

Append-only semantics + JCS canonical-form hashing follow ARS
``passport_as_reset_boundary.md``:

- 12-char SHA-256 hash over the JCS-canonical bytes of all prior
  entries plus the new entry with its hash placeholder
- ``boundary`` entries are emitted at FULL checkpoints when
  ``passport.enable_reset_boundary`` is True
- ``resume`` entries are appended on consumption (carrying
  ``consumes_hash``); double-resume is forbidden
- File lock (``portalocker``, cross-platform) is held for the full
  read-check-append sequence; refuse to resume on platforms that
  can't provide OS-level exclusion (ARS iron rule §9)

V1.0 default: ``enable_reset_boundary=False`` (opt-in via
``~/.paic/config.yaml`` ``passport.enable_reset_boundary: true``).
"""

from paic.passport.ledger import (
    LEDGER_FILENAME,
    LOCK_FILENAME,
    LedgerCorruption,
    append_boundary,
    append_resume,
    compute_entry_hash,
    list_entries,
    load_ledger,
)
from paic.passport.lock import (
    LockTimeout,
    PassportLockError,
    passport_lock,
)
from paic.passport.resume import (
    DoubleResumeError,
    HashMismatchError,
    PassportResumeError,
    resolve_resume,
)
from paic.passport.schema import (
    BoundaryKind,
    PassportEntry,
    PendingDecision,
    PendingDecisionOption,
)

__all__ = [
    "BoundaryKind",
    "DoubleResumeError",
    "HashMismatchError",
    "LEDGER_FILENAME",
    "LOCK_FILENAME",
    "LedgerCorruption",
    "LockTimeout",
    "PassportEntry",
    "PassportLockError",
    "PassportResumeError",
    "PendingDecision",
    "PendingDecisionOption",
    "append_boundary",
    "append_resume",
    "compute_entry_hash",
    "list_entries",
    "load_ledger",
    "passport_lock",
    "resolve_resume",
]

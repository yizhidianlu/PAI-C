"""Append-only ledger writes + JCS-canonical hash computation.

Mirrors ARS protocol (``passport_as_reset_boundary.md``):

- File format: one YAML document per entry, separated by ``---`` lines
  (PAI-C convention). Wire format chosen so concurrent processes can
  safely append by O_APPEND on POSIX and equivalent on Windows.
- Hash: SHA-256 over JCS (RFC 8785) bytes of all prior entries +
  the new entry with its ``hash`` field set to the placeholder
  ``"000000000000"``. Take lowercase hex digest, first 12 chars.
- Append-only: existing entries never mutated. Re-running a stage
  appends a new boundary; resume appends a new ``kind="resume"`` entry
  with ``consumes_hash``.

The ledger is not a YAML *list* — it's a stream of YAML documents
separated by ``---`` (see https://yaml.org/spec/1.2.2/#22-structures).
This lets two processes append independently without re-serializing
the whole file.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from paic.passport.schema import HASH_PLACEHOLDER, PassportEntry

LEDGER_FILENAME = "passport.yaml"
LOCK_FILENAME = ".passport.lock"


class LedgerCorruption(RuntimeError):
    """Ledger is unreadable — caller decides whether to abort or salvage."""


# ---------------------------------------------------- canonical hashing


def _to_jcs_bytes(payload: dict[str, Any]) -> bytes:
    """JCS-canonical (RFC 8785) bytes of one entry.

    Implementation: ``json.dumps`` with ``sort_keys=True``,
    ``separators=(",", ":")`` (no insignificant whitespace), and the
    default UTF-8 encoder; numbers in canonical form via ``allow_nan=False``
    and ``ensure_ascii=False``.

    Datetimes are serialized to ISO 8601 UTC strings before hashing —
    Python's json doesn't natively serialize datetime, so the caller
    must pre-format. We handle this in :func:`_normalize_for_hash`.
    """
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def _normalize_for_hash(entry: dict[str, Any]) -> dict[str, Any]:
    """Coerce datetime objects to ISO strings + drop None-valued keys.

    JCS treats absent and null-valued keys differently; we adopt
    "absent" for None to match how PAI-C stores tombstone-free entries
    elsewhere. Datetimes are stored as ISO 8601 UTC strings.
    """
    out: dict[str, Any] = {}
    for k, v in sorted(entry.items()):
        if v is None:
            continue
        if isinstance(v, datetime):
            out[k] = v.astimezone(UTC).isoformat(timespec="microseconds")
        elif isinstance(v, dict):
            out[k] = _normalize_for_hash(v)
        elif isinstance(v, list):
            out[k] = [_normalize_for_hash(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


def compute_entry_hash(prior_entries: Iterable[PassportEntry], new_entry: PassportEntry) -> str:
    """Return the 12-char SHA-256 hex prefix for ``new_entry``.

    Argument shapes:

    - ``prior_entries`` — every previously-finalized entry in the ledger
      (each carries its own non-placeholder hash). Ordering is the
      ledger order; never reorder for hashing.
    - ``new_entry`` — the entry whose hash we are computing. Its
      ``hash`` field is replaced with :data:`HASH_PLACEHOLDER` for the
      computation; the caller writes the returned hash back before
      appending.

    Deterministic: same prior chain + same payload → same digest. Two
    independent implementations following the JCS rules above must
    produce the same bytes.
    """
    h = hashlib.sha256()
    for prior in prior_entries:
        prior_dump = prior.model_dump(mode="python", exclude_none=True)
        h.update(_to_jcs_bytes(_normalize_for_hash(prior_dump)))
        # Single LF separator between entries; matches ARS spec.
        h.update(b"\x0a")

    new_dump = new_entry.model_dump(mode="python", exclude_none=True)
    new_dump["hash"] = HASH_PLACEHOLDER
    h.update(_to_jcs_bytes(_normalize_for_hash(new_dump)))

    return h.hexdigest()[:12]


# ---------------------------------------------------- IO


def load_ledger(ledger_path: Path) -> list[PassportEntry]:
    """Read every entry from a multi-doc YAML stream.

    Returns ``[]`` when the file does not exist (fresh project).
    """
    if not ledger_path.is_file():
        return []
    text = ledger_path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    out: list[PassportEntry] = []
    try:
        for doc in yaml.safe_load_all(text):
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise LedgerCorruption(
                    f"Non-dict entry in {ledger_path}: {type(doc).__name__}"
                )
            out.append(PassportEntry.model_validate(doc))
    except yaml.YAMLError as exc:
        raise LedgerCorruption(f"YAML parse error in {ledger_path}: {exc}") from exc
    return out


def list_entries(ledger_path: Path) -> list[dict]:
    """Public read-only snapshot for the MCP tool / SKILL renderer."""
    return [e.model_dump(mode="json") for e in load_ledger(ledger_path)]


def append_boundary(
    ledger_path: Path,
    *,
    stage: int | str,
    deliverables: list[str] | None = None,
    next_stage: int | str | None = None,
    pending_decision: dict | None = None,
    session_marker: str | None = None,
    notes: str | None = None,
    entry_id: str | None = None,
) -> PassportEntry:
    """Compute hash, then append a new ``kind="boundary"`` entry to the ledger.

    The caller must hold the passport lock when calling this — see
    :func:`paic.passport.lock.passport_lock`.

    Returns the finalized entry with its non-placeholder ``hash`` set.
    """
    from paic.passport.schema import PendingDecision

    if entry_id is None:
        from ulid import ULID
        entry_id = str(ULID())

    pending_obj = (
        PendingDecision.model_validate(pending_decision)
        if pending_decision
        else None
    )

    new_entry = PassportEntry(
        id=entry_id,
        kind="boundary",
        stage=stage,
        generated_at=datetime.now(UTC),
        session_marker=session_marker,
        deliverables=list(deliverables or []),
        next_stage=next_stage,
        pending_decision=pending_obj,
        notes=notes,
    )

    prior = load_ledger(ledger_path)
    new_entry.hash = compute_entry_hash(prior, new_entry)
    _append_yaml_doc(ledger_path, new_entry)
    return new_entry


def append_resume(
    ledger_path: Path,
    *,
    consumes_hash: str,
    stage: int | str,
    chosen_branch: str | None = None,
    user_override: dict | None = None,
    session_marker: str | None = None,
    notes: str | None = None,
    entry_id: str | None = None,
) -> PassportEntry:
    """Append a ``kind="resume"`` entry pointing back at ``consumes_hash``.

    Caller must hold the lock + must have already verified that no
    prior resume entry consumed this hash (use
    :func:`paic.passport.resume.resolve_resume` for the full pipeline).
    """
    if entry_id is None:
        from ulid import ULID
        entry_id = str(ULID())

    new_entry = PassportEntry(
        id=entry_id,
        kind="resume",
        stage=stage,
        generated_at=datetime.now(UTC),
        session_marker=session_marker,
        consumes_hash=consumes_hash,
        chosen_branch=chosen_branch,
        user_override=user_override,
        notes=notes,
    )

    prior = load_ledger(ledger_path)
    new_entry.hash = compute_entry_hash(prior, new_entry)
    _append_yaml_doc(ledger_path, new_entry)
    return new_entry


def _append_yaml_doc(ledger_path: Path, entry: PassportEntry) -> None:
    """Append one YAML document terminator + entry to the ledger file."""
    payload = entry.model_dump(mode="json", exclude_none=True)
    serialized = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        # Multi-doc YAML stream: each entry is its own document, opened
        # by ``---`` (idempotent — if the file is empty the parser still
        # accepts it).
        fh.write("---\n")
        fh.write(serialized)

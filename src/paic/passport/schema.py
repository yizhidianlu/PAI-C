"""Material Passport schema — PassportEntry + PendingDecision shapes.

Mirrors ARS Schema 9 (``passport_as_reset_boundary.md``) with simplifications:

- Single-author ledger: no compliance_history / audit_artifact
  cross-references (those are ARS systematic-review-mode only).
- ``kind`` is restricted to ``boundary`` / ``resume`` — V1.0 doesn't
  emit ``error`` or ``audit`` entries.
- ``hash`` is the canonical 12-char hex prefix of SHA-256 over JCS
  bytes; placeholder ``"000000000000"`` during hash computation.
- ``pending_decision`` carries the multi-branch routing the
  orchestrator must re-prompt for on resume (ARS iron rule §8).

Pydantic v2 with ``model_config = ConfigDict(extra="allow")`` to keep
forward compatibility — future ARS-spec fields (e.g. compliance_history
back-reference) deserialize cleanly without breaking older clients.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BoundaryKind = Literal["boundary", "resume"]
"""Ledger entry kind. V1.0 only emits these two; future versions may add
``error`` / ``audit`` per ARS Schema 9."""

# Canonical hash placeholder — must match the byte-exact value used in
# JCS-canonical hashing. Length deliberately = final hash length so the
# pre/post hash bytes have identical structure.
HASH_PLACEHOLDER = "000000000000"


class PendingDecisionOption(BaseModel):
    """One branch of a pending decision.

    Each option carries its own routing — on resume, the orchestrator
    looks up the user's chosen ``value`` and uses that option's
    ``next_stage`` / ``next_mode`` to route. The boundary entry's
    top-level ``next`` field is advisory only when ``pending_decision``
    is set (ARS iron rule §8).
    """

    model_config = ConfigDict(extra="allow")

    value: str
    """User-facing branch identifier (e.g. ``"revise"``, ``"abort"``)."""

    next_stage: int | str | None = None
    """Stage to route to when this branch is chosen. ``None`` → terminate."""

    next_mode: str | None = None
    """Optional mode override for the next stage."""

    label: str | None = None
    """Optional human-readable label shown to the user at re-prompt."""


class PendingDecision(BaseModel):
    """A user decision the orchestrator must collect on resume.

    ARS pattern: when a FULL-checkpoint also requires a multi-branch
    user decision (review accept/revise/reject; finalize format choice),
    we record the question + options on the boundary entry. On
    ``resolve_resume``, the orchestrator re-prompts and routes by the
    chosen option's ``next_stage`` (NOT the boundary's top-level ``next``).
    """

    model_config = ConfigDict(extra="allow")

    question: str
    """Prompt re-displayed to the user on resume."""

    options: list[PendingDecisionOption] = Field(default_factory=list)
    """Available branches. Each carries its own routing."""


class PassportEntry(BaseModel):
    """One entry in the append-only ledger.

    ``hash`` is computed over a JCS-serialized stream of all prior
    entries + this entry with the placeholder, then SHA-256 → first 12
    hex chars (lowercase). Never recomputed — once written it is
    immutable. See :func:`paic.passport.ledger.compute_entry_hash`.

    ``boundary`` entries are emitted at FULL checkpoints; ``resume``
    entries are appended on consumption with ``consumes_hash`` pointing
    to the boundary they resolve.

    Append-only invariant: prior entries are never deleted, reordered, or
    mutated. A boundary is "consumed" only when a resume entry with a
    matching ``consumes_hash`` follows it in the ledger.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    """ULID-keyed unique entry id (independent of the 12-char hash)."""

    kind: BoundaryKind
    stage: int | str
    generated_at: datetime
    session_marker: str | None = None
    """Opaque tag identifying the emitting session (e.g. user@host+ts)."""

    hash: str = HASH_PLACEHOLDER
    """12-char SHA-256 prefix; placeholder during compute, finalised before append."""

    deliverables: list[str] = Field(default_factory=list)
    """Stage-output artefact identifiers (paths / IDs) referenced by the entry."""

    next_stage: int | str | None = None
    """Best-guess routing for the next stage. Advisory only when
    ``pending_decision`` is set (per ARS iron rule §8)."""

    pending_decision: PendingDecision | None = None
    """When set, ``resolve_resume`` re-prompts the user before advancing."""

    # Resume-specific fields — populated on `kind="resume"` entries
    consumes_hash: str | None = None
    chosen_branch: str | None = None
    """The ``value`` of the option the user selected at re-prompt time."""

    user_override: dict | None = None
    """Snapshot of ``stage=`` / ``mode=`` overrides the user supplied at
    resume command-time (separate from the user's branch decision)."""

    notes: str | None = None

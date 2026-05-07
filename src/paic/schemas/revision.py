"""RevisionTask schema — persisted under ``.paic/revisions/`` (§quality phase 8).

Multi-agent review (``/paic-review``) currently produces a markdown
critique. Phase 8 turns each actionable critique into a discrete
RevisionTask the user can apply / mark resolved. Tasks survive across
review rounds so the user can see what's still open after round 2 vs
what was fixed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RevisionSeverity = Literal["info", "minor", "major", "blocker"]
RevisionStatus = Literal["open", "in_progress", "resolved", "wontfix"]
RevisionTargetKind = Literal[
    "section", "claim", "experiment_field", "figure", "table", "global",
]


class RevisionTask(BaseModel):
    id: str
    """ULID-keyed stable id."""

    severity: RevisionSeverity
    status: RevisionStatus = "open"

    target_kind: RevisionTargetKind
    """What the task targets: a draft section, a claim id, an experiment
    field, a figure id, a table id, or a global concern."""

    target_ref: str | None = None
    """Specific reference within the target_kind (e.g. ``"01_intro"`` for
    a section, ``"CL1"`` for a claim, ``"baselines"`` for an experiment
    field)."""

    summary: str
    """One short sentence describing the issue."""

    detail: str | None = None
    """Optional longer explanation pulled from the reviewer's critique."""

    patch_hint: str | None = None
    """If the reviewer suggested a concrete fix, capture it here. Empty
    for tasks that need user judgment."""

    source_review_round: int | None = None
    """Which review round produced this task (1-indexed). None for tasks
    created outside the review graph (e.g. quality_gate output)."""

    source_persona: str | None = None
    """Which reviewer agent surfaced this — methodology / statistics /
    domain / reviewer2 / moderator / quality_gate."""

    resolution_summary: str | None = None
    """When ``status="resolved"``, a one-line note on what changed."""

    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> "RevisionTask":
        if not isinstance(data, dict):
            raise TypeError(
                f"RevisionTask.from_yaml_dict expects a dict, got {type(data).__name__}"
            )
        return cls.model_validate(data)

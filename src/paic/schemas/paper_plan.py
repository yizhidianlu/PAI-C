"""PaperPlan — global paper-level plan persisted at ``.paic/plans/paper_plan.yaml``.

A single paper has one PaperPlan. It captures the contributions, section
intent, terminology / symbols, and figure/table/algorithm slots so downstream
compose / claim-extract / quality-gate stages can ground their output against
one consistent thesis.

Phase-1 introduces this schema. Later phases populate cross-cutting fields:
- ``ContributionEntry.claim_ids`` is filled by Phase 4 (claim ledger).
- ``FigurePlanItem.supporting_claims`` is filled by Phase 9 (claim-driven
  figures).
- ``open_todos`` is appended by Phase 10 (final quality gate) when
  unresolved issues are deferred.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContributionEntry(BaseModel):
    id: str
    """Stable id like ``C1`` / ``C2`` so claims and figures can reference it."""

    title: str
    description: str
    claim_ids: list[str] = Field(default_factory=list)
    """Filled by Phase 4 — Claim ledger. Empty under Phase-1 alone."""


class SectionPlanEntry(BaseModel):
    name: str
    """Canonical section name like ``01_intro`` / ``03_method`` (matches drafts/sections/<name>.tex)."""

    intent: str
    """One-line description of what this section is supposed to argue."""

    supports_contributions: list[str] = Field(default_factory=list)
    """List of ContributionEntry.id this section advances."""

    target_words: int | None = None


class FigurePlanItem(BaseModel):
    id: str
    """Stable id like ``F1`` / ``F2``; used as ``\\label{fig:<id>}`` seed."""

    caption_seed: str
    placement_section: str
    """Canonical section name where the figure should appear."""

    supporting_claims: list[str] = Field(default_factory=list)
    """Filled by Phase 9. Empty under Phase-1 alone."""

    no_visual_reason: str | None = None
    """Set when a contribution intentionally has no figure (e.g. theorem-only)."""


class TablePlanItem(BaseModel):
    id: str
    caption_seed: str
    placement_section: str
    supporting_claims: list[str] = Field(default_factory=list)


class AlgorithmPlanItem(BaseModel):
    id: str
    caption_seed: str
    placement_section: str
    supporting_claims: list[str] = Field(default_factory=list)


class PaperPlan(BaseModel):
    schema_version: int = 1

    thesis: str
    """One- or two-sentence statement of the paper's central claim."""

    target_venue: str | None = None
    audience: str | None = None
    """Primary readership (e.g. "ML systems researchers", "BCI practitioners")."""

    contributions: list[ContributionEntry] = Field(default_factory=list)
    section_plan: list[SectionPlanEntry] = Field(default_factory=list)

    terminology: dict[str, str] = Field(default_factory=dict)
    """``term -> short definition``. Used by compose to keep wording consistent."""

    symbols: dict[str, str] = Field(default_factory=dict)
    """``symbol -> meaning``. e.g. ``"\\theta": "model parameters"``."""

    claim_ids: list[str] = Field(default_factory=list)
    """Forward reference to Phase 4 ``claims.yaml`` ids. Empty under Phase 1."""

    figure_plan: list[FigurePlanItem] = Field(default_factory=list)
    table_plan: list[TablePlanItem] = Field(default_factory=list)
    algorithm_plan: list[AlgorithmPlanItem] = Field(default_factory=list)

    open_todos: list[str] = Field(default_factory=list)

    idea_id: str | None = None
    """Source idea card this plan was derived from."""

    experiment_id: str | None = None

    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> PaperPlan:
        """Load a possibly-future-version paper_plan dict, defaulting unknown fields.

        Idempotent: a v1 dict round-trips unchanged.
        """
        if not isinstance(data, dict):
            raise TypeError(
                f"PaperPlan.from_yaml_dict expects a dict, got {type(data).__name__}"
            )
        return cls.model_validate(data)

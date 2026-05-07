"""Claim ledger schema — persisted at ``.paic/plans/claims.yaml``.

A Claim is a discrete assertion the paper makes (e.g. "our method beats
CSP by 3.2% balanced accuracy on BCI-IV-2a"). Tracking claims explicitly
lets downstream stages catch unsupported strong assertions before
finalization, instead of relying on the LLM-pass-through of compose +
polish to silently let them through.

Phase 4 introduces this schema. Compose / polish hooks populate it
incrementally; the final quality gate (Phase 10) reads it to flag
unsupported strong claims before the draft is declared ready.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ClaimType = Literal["novelty", "comparative", "factual", "numeric", "methodological", "result"]
"""
- ``novelty``: a "first / new / never before" assertion. Needs prior-work
  evidence proving nobody has done it.
- ``comparative``: "X beats Y on Z" / "more efficient than baseline".
  Needs cited baseline + experiment result.
- ``numeric``: any specific number quoted in prose. Must trace to an
  experiment artifact.
- ``factual``: "EEG signals are noisy" — needs at most a citation.
- ``methodological``: a method-design choice. Often supported by ablation.
- ``result``: a specific experimental outcome. Must trace to experiment
  artifact + (ideally) a table / figure.
"""

ClaimStatus = Literal["supported", "needs_evidence", "todo", "rejected"]
"""
- ``supported``: at least one valid supporting_paper / supporting_experiment.
- ``needs_evidence``: claim is in the draft but lacks any cite or artifact.
- ``todo``: an explicit TODO marker is in the draft for this claim.
- ``rejected``: user / reviewer marked the claim as wrong; should not
  appear in the final paper.
"""


class Claim(BaseModel):
    id: str
    """Stable id like ``CL1`` / ``CL2``. Distinct from contribution ids
    (``C1`` / ``C2``); a single contribution typically yields multiple claims."""

    text: str
    type: ClaimType
    status: ClaimStatus = "needs_evidence"

    contribution_id: str | None = None
    """The paper_plan ContributionEntry this claim advances, if any."""

    supporting_papers: list[str] = Field(default_factory=list)
    """List of cite_keys that support this claim (must exist in
    selected.yaml)."""

    supporting_experiments: list[str] = Field(default_factory=list)
    """List of experiment_id values whose plan / metrics back this claim."""

    supporting_artifacts: list[str] = Field(default_factory=list)
    """Free-form artifact references — e.g. ``figures/method_overview.pdf``,
    ``tables/main_results.csv``. Used by Phase 9 / Phase 10."""

    appears_in_sections: list[str] = Field(default_factory=list)
    """Canonical section names this claim appears in. Helps polish /
    quality_gate trace where to fix things."""

    required_citations: list[str] = Field(default_factory=list)
    """Cite_keys the model thinks the claim *should* have — populated by
    extract; used by validate to flag missing cites."""

    supporting_chunks: list[str] = Field(default_factory=list)
    """Optional chunk_ids (``<cite_key>__c<NNN>``) the claim was extracted
    against. P0 #1: when populated, the semantic claim judge reads those
    specific passages instead of falling back to the paper's summary,
    yielding tighter verdicts. Default ``[]`` for backward compatibility
    — claims from before chunking shipped still validate via summary."""

    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> "Claim":
        if not isinstance(data, dict):
            raise TypeError(f"Claim.from_yaml_dict expects a dict, got {type(data).__name__}")
        return cls.model_validate(data)


class ClaimsLedger(BaseModel):
    """Top-level container for ``claims.yaml``.

    Kept as a separate model (rather than a bare list) so we can attach
    metadata like schema_version and last_updated_at without breaking
    backward compatibility later.
    """

    schema_version: int = 1
    claims: list[Claim] = Field(default_factory=list)
    last_updated_at: datetime | None = None

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> "ClaimsLedger":
        if not isinstance(data, dict):
            raise TypeError(
                f"ClaimsLedger.from_yaml_dict expects a dict, got {type(data).__name__}"
            )
        return cls.model_validate(data)

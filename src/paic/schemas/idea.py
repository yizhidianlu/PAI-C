"""IdeaCard schema."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

IdeaStatus = Literal["draft", "selected", "archived"]
PanelConsensus = Literal["high_agreement", "moderate", "diverged"]


class IdeaCard(BaseModel):
    id: str
    title: str
    one_liner: str = Field(..., max_length=240)
    motivation: str
    proposed_approach: str
    novelty_claim: str
    expected_contribution: str
    grounded_in: list[str] = Field(default_factory=list)
    contrasts_with: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    feasibility_score: float = Field(0.0, ge=0.0, le=1.0)
    novelty_score: float = Field(0.0, ge=0.0, le=1.0)
    impact_score: float = Field(0.0, ge=0.0, le=1.0)
    composite_score: float = Field(0.0, ge=0.0, le=1.0)
    status: IdeaStatus = "draft"
    created_at: datetime
    parent_run_id: str | None = None

    # §26 v2 fields (multi-persona panel scoring + multi-round refine)
    schema_version: int = 2
    panel_scores: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Per-persona breakdown when scored by §26 panel.

    Shape: ``{persona_name: {feasibility, novelty, impact, rationale, red_flags}}``.
    Empty dict for v1 cards or when LLM self-scoring was used.
    """

    panel_consensus: PanelConsensus | None = None
    """Per-dimension agreement signal from §26.6.2 disagreement metric.

    None for v1 cards or single-persona scoring.
    """

    red_flags: list[str] = Field(default_factory=list)
    """Aggregated red flags from all panel personas (sorted unique).

    Distinct from ``risk_factors``: the LLM-generated risks are intrinsic
    (e.g. "latent collapse"); ``red_flags`` are panel-flagged concrete
    concerns ("no public dataset", "claim relies on undefined hyperparam").
    """

    feedback_log: list[str] = Field(default_factory=list)
    """User feedback chain across §26 multi-round refine. One entry per round."""

    rounds_used: int = 1
    """How many ideate rounds produced this card. 1 for v1; 1-N for v2."""

    @classmethod
    def from_legacy(cls, data: dict[str, Any]) -> IdeaCard:
        """Load a possibly-v1 IdeaCard yaml dict, filling v2 defaults explicitly.

        Use this from every callsite that reads ``ideas/<id>.yaml`` so consumers
        get a uniform v2 shape regardless of when the card was written.
        Idempotent: a v2 dict round-trips unchanged.
        """
        if not isinstance(data, dict):
            raise TypeError(f"IdeaCard.from_legacy expects a dict, got {type(data).__name__}")
        if data.get("schema_version") == 2:
            return cls.model_validate(data)
        # v1 card — fill in v2 defaults explicitly
        return cls.model_validate({
            **data,
            "schema_version": 2,
            "panel_scores": data.get("panel_scores") or {},
            "panel_consensus": data.get("panel_consensus"),
            "red_flags": data.get("red_flags") or [],
            "feedback_log": data.get("feedback_log") or [],
            "rounds_used": data.get("rounds_used") or 1,
        })

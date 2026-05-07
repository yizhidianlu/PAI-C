"""ExperimentPlan and supporting types."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ExperimentStatus = Literal[
    "draft", "under_review", "approved", "running", "done"
]
MetricDirection = Literal["min", "max"]


class Dataset(BaseModel):
    name: str
    rationale: str
    splits: dict[str, int] | None = None
    license_note: str | None = None

    @field_validator("splits", mode="before")
    @classmethod
    def _coerce_splits(cls, v: object) -> dict[str, int] | None:
        if v is None:
            return v
        if not isinstance(v, dict):
            return None
        coerced: dict[str, int] = {}
        for k, val in v.items():
            if isinstance(val, int):
                coerced[k] = val
            elif isinstance(val, float):
                coerced[k] = int(val)
            else:
                m = re.search(r"\d+", str(val))
                if m:
                    coerced[k] = int(m.group())
        return coerced or None


class Baseline(BaseModel):
    name: str
    why: str
    paper_ref: str | None = None


class Metric(BaseModel):
    name: str
    direction: MetricDirection
    primary: bool = False
    # §quality phase 5 — quantitative success bar attached directly to
    # the metric so quality_gate (phase 10) can check that a numeric
    # claim about this metric has provenance.
    success_threshold: float | None = None
    success_threshold_unit: str | None = None
    """e.g. ``"absolute"`` / ``"relative"`` / ``"percentage points"``."""


class AblationAxis(BaseModel):
    factor: str
    levels: list[str]
    purpose: str


class ExperimentPlan(BaseModel):
    id: str
    idea_id: str
    research_questions: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    baselines: list[Baseline] = Field(default_factory=list)
    proposed_method: str
    metrics: list[Metric] = Field(default_factory=list)
    ablations: list[AblationAxis] = Field(default_factory=list)
    compute_budget: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    threats_to_validity: list[str] = Field(default_factory=list)
    timeline_weeks: int | None = None
    # §quality phase 5 — separable verification slices.
    statistical_plan: list[str] = Field(default_factory=list)
    """Pre-registration-style statistical commitments: seeds count,
    significance test, multiple-comparison correction, n per group, etc."""

    reproducibility: list[str] = Field(default_factory=list)
    """Reproducibility checklist items: random_state, config_hash, version
    pinning, hardware spec, deterministic-mode flag, etc."""

    validation_warnings: list[str] = Field(default_factory=list)
    """Soft warnings produced by the post-LLM verifier nodes (baseline
    without paper_ref, dataset without license, no primary metric, etc).
    Empty list = clean plan; non-empty = caller / SKILL should surface
    these to the user."""

    created_at: datetime
    parent_run_id: str | None = None
    status: ExperimentStatus = "draft"

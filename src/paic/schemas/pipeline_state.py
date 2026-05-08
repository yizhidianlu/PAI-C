"""PipelineState — yaml schema for /paic-pipeline orchestrator (ARS-fusion P1-4).

Stage-level state file at ``<project>/.paic/state/pipeline.yaml``. Tracks
the current stage of the 11-stage V1.0 pipeline, the user's chosen mode
per stage, FULL/SLIM/MANDATORY checkpoint history, and consecutive-continue
count for the adaptive-checkpoint downgrade rule (ARS §"Adaptive
Checkpoint System").

Pydantic v2 with ``ConfigDict(extra="allow")`` for forward compatibility.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 11-stage V1.0 pipeline (matches the Approach table in the V1.0 plan).
PipelineStage = Literal[
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
]
"""
0  INIT
1  SEARCH+INGEST
2  IDEATE
3  EXPERIMENT
4  PLAN
5  DRAFT
6  INTEGRITY-PRE   (MANDATORY)
7  REVIEW
8  REVISE
9  INTEGRITY-FINAL (MANDATORY)
10 FINALIZE        (MANDATORY)
"""

STAGE_LABELS: dict[int, str] = {
    0: "INIT",
    1: "SEARCH+INGEST",
    2: "IDEATE",
    3: "EXPERIMENT",
    4: "PLAN",
    5: "DRAFT",
    6: "INTEGRITY-PRE",
    7: "REVIEW",
    8: "REVISE",
    9: "INTEGRITY-FINAL",
    10: "FINALIZE",
}

MANDATORY_STAGES: frozenset[int] = frozenset({6, 9, 10})
"""Stages whose checkpoint cannot be auto-skipped — V1.0 design (3 only)."""


CheckpointKind = Literal["FULL", "SLIM", "MANDATORY"]
PipelineMode = Literal["greenfield", "mid_entry", "revision_only", "finalize_only"]


class StageHistoryEntry(BaseModel):
    """One transition record. Append-only; never mutated after creation."""

    model_config = ConfigDict(extra="allow")

    from_stage: int | None = None
    """``None`` when this is the entry into the pipeline (e.g. INIT)."""

    to_stage: int
    transitioned_at: datetime
    checkpoint_kind: CheckpointKind
    verdict: str | None = None
    """User-supplied verdict for stages that branch (REVIEW outcome,
    INTEGRITY pass/fail, FINALIZE format)."""

    deliverables: list[str] = Field(default_factory=list)
    notes: str | None = None
    passport_hash: str | None = None
    """Optional reference to the Material Passport boundary that was
    emitted at this transition (when passport.enable_reset_boundary)."""


class PipelineState(BaseModel):
    """Top-level pipeline.yaml document — singleton per project."""

    model_config = ConfigDict(extra="allow")

    project_dir: str
    current_stage: int
    """0-10. Initial value is the entry stage; advances per
    paic_pipeline_advance."""

    mode: PipelineMode = "greenfield"
    """How the user entered the pipeline. ``mid_entry`` skips earlier
    stages but cannot skip stage 6 INTEGRITY-PRE (ARS iron rule)."""

    consecutive_continues: int = 0
    """How many times in a row the user said "just continue" at a
    non-MANDATORY checkpoint. Drives the FULL → SLIM → forced-FULL
    downgrade rule (ARS adaptive checkpoint, P2-1)."""

    awaiting_user: bool = False
    """True when the orchestrator is paused at a checkpoint."""

    last_checkpoint_kind: CheckpointKind | None = None
    last_advanced_at: datetime | None = None

    stage_history: list[StageHistoryEntry] = Field(default_factory=list)
    """Append-only audit trail. Inspectable via paic_pipeline_state."""

    notes: list[str] = Field(default_factory=list)
    """Free-form orchestrator notes (e.g. observation / warnings)."""


def is_mandatory(stage: int) -> bool:
    """Whether the stage's checkpoint must be MANDATORY (cannot auto-skip)."""
    return stage in MANDATORY_STAGES


def stage_label(stage: int) -> str:
    return STAGE_LABELS.get(stage, f"STAGE_{stage}")

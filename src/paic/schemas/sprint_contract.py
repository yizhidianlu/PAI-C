"""Sprint Contract — writer / evaluator agreement for the 4-call compose pipeline.

Borrowed from ARS v3.6.6 ``shared/sprint_contract.schema.json`` and
simplified for PAI-C's compose use case (single-author writer, single
evaluator). The contract is the load-bearing artefact that physically
separates writer Phase 4a (planning) from writer Phase 4b (execution)
and from evaluator Phases 6a/6b — the writer commits to acceptance
criteria + claim list BEFORE seeing the actual paper context, then the
evaluator scores against those same criteria BEFORE seeing the writer's
output.

Pydantic v2; ``ConfigDict(extra="allow")`` for forward compatibility.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Default 5 acceptance dimensions the writer commits to up-front and the
# evaluator scores against. ARS uses 7; PAI-C V1.0 starts with 5 to match
# our existing review-graph dimension count.
DEFAULT_DIMENSIONS: tuple[str, ...] = (
    "originality",
    "methodological_rigor",
    "evidence_sufficiency",
    "argument_coherence",
    "writing_quality",
)


class AcceptanceCriterion(BaseModel):
    """One dimension the writer commits to in Phase 4a."""

    model_config = ConfigDict(extra="allow")

    dimension: str
    """Dimension id (e.g. ``originality``) — must be in the contract's
    dimensions list."""

    target_threshold: int = Field(ge=0, le=100, default=70)
    """Self-imposed pass bar on the 0-100 scale. Evaluator's score
    against this dimension must >= target_threshold."""

    success_signals: list[str] = Field(default_factory=list)
    """Concrete signals the writer commits to producing (e.g. "section
    cites at least 3 contrast papers from related-work cluster A")."""


class WriterCommitment(BaseModel):
    """Phase 4a output — writer's pre-commitment, paper-blind.

    Produced WITHOUT seeing the actual draft / library content. The
    writer commits to:
    1. Acceptance criteria they intend to satisfy
    2. Claims they intend to make
    3. Structure they intend to follow
    4. Citations they intend to use (cite_keys, not the citation text)

    Phase 4b receives this commitment + the actual context and writes
    the LaTeX. Phase 6a/6b verify Phase 4b honoured the commitment.
    """

    model_config = ConfigDict(extra="allow")

    section_name: str
    target_words: int
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    intended_claims: list[str] = Field(default_factory=list)
    """Claim summaries the writer commits to making in this section."""

    intended_structure: list[str] = Field(default_factory=list)
    """Sub-section / paragraph plan (e.g. ["motivation", "method
    overview", "key novelty"]). Empty when the section type is short."""

    intended_cite_keys: list[str] = Field(default_factory=list)
    """Cite keys the writer commits to using. May be a subset of
    library_cite_keys."""

    notes: str | None = None


class EvaluatorRubric(BaseModel):
    """Phase 6a output — evaluator's scoring rubric, paper-blind.

    Produced WITHOUT seeing the writer's Phase 4b output. The evaluator
    pre-commits to:
    1. What signals would warrant high vs low scores per dimension
    2. What patterns would trigger BLOCK (mandatory revision)
    3. What would trigger WARN (minor revision suggested)

    Phase 6b receives this rubric + the writer's output and scores
    against the pre-committed signals — defeating "read the paper, then
    rationalise the standard" silent quality drift.
    """

    model_config = ConfigDict(extra="allow")

    contract_paraphrase: str
    """One-paragraph restatement of the contract — proves the evaluator
    actually parsed it (not skipped to the writer output)."""

    per_dimension_criteria: dict[str, dict] = Field(default_factory=dict)
    """``{dimension_id: {what_to_look_for, what_triggers_block,
    what_triggers_warn}}`` per ARS Phase 6a lint rule."""


class WriterDecision(BaseModel):
    """Phase 4b output — writer's draft + self-scoring."""

    model_config = ConfigDict(extra="allow")

    composed_text: str
    """Full LaTeX (or plain) draft of the section."""

    self_dimension_scores: dict[str, int] = Field(default_factory=dict)
    """Writer's own scores per dimension. Used as a sanity check
    against Phase 6b — large divergence flags potential drift."""

    cited_keys: list[str] = Field(default_factory=list)
    """Cite keys the writer actually used (may differ from
    intended_cite_keys when the writer encounters context that pulls
    them elsewhere; the divergence is captured for the evaluator)."""

    failure_condition_checks: dict[str, str] = Field(default_factory=dict)
    """Self-flagged failure modes. Empty dict = nothing surfaced."""


class EvaluatorDecision(BaseModel):
    """Phase 6b output — evaluator's score + must-fix list."""

    model_config = ConfigDict(extra="allow")

    dimension_scores: dict[str, int] = Field(default_factory=dict)
    failure_condition_checks: dict[str, str] = Field(default_factory=dict)
    review_body: str
    decision: Literal["accept", "accept_with_dissent", "minor_revision", "major_revision"]
    must_fix: list[str] = Field(default_factory=list)
    nice_to_fix: list[str] = Field(default_factory=list)


class SprintContract(BaseModel):
    """Frozen baseline that drives all 4 phases.

    Constructed before Phase 4a; never mutated after. Each phase
    receives the same contract verbatim — physical separation comes
    from each phase being a separate LLMClient.complete_isolated call
    that doesn't share conversation history.
    """

    model_config = ConfigDict(extra="allow")

    section_name: str
    section_type: str
    """e.g. ``intro`` / ``related`` / ``method`` / ``experiments``."""

    target_words: int
    dimensions: list[str] = Field(default_factory=lambda: list(DEFAULT_DIMENSIONS))
    library_cite_keys: list[str] = Field(default_factory=list)
    """Allowed cite keys for this compose call — writer must pick from
    this whitelist; evaluator rejects any cite outside it."""

    paper_plan_excerpt: dict | None = None
    idea_excerpt: dict | None = None
    experiment_excerpt: dict | None = None
    instruction: str | None = None

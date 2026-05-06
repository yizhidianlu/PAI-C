"""Schema unit tests — Phase 1."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from paic.schemas import (
    AblationAxis,
    Baseline,
    Critique,
    Dataset,
    DraftSection,
    ExperimentPlan,
    IdeaCard,
    Metric,
    PaperRef,
    PaperSummary,
    ReviewTranscript,
    ReviewVerdict,
)


def test_paper_ref_requires_an_id_or_title():
    with pytest.raises(ValidationError):
        PaperRef(authors=["A"])


def test_paper_ref_primary_id_prefers_arxiv():
    ref = PaperRef(arxiv_id="2401.12345", doi="10.x/y", title="Foo")
    assert ref.primary_id() == "2401.12345"


def test_paper_ref_accepts_external_source():
    """§18: source='external' with platform metadata."""
    ref = PaperRef(
        doi="10.1101/2024.01.01.000001",
        title="A bioRxiv preprint",
        source="external",
        platform="biorxiv",
        external_ids={"pmid": "12345"},
    )
    assert ref.source == "external"
    assert ref.platform == "biorxiv"
    assert ref.external_ids == {"pmid": "12345"}


def test_paper_ref_external_fields_round_trip():
    ref = PaperRef(
        doi="10.1234/abc",
        title="X",
        source="external",
        platform="openalex",
        external_ids={"openalex": "W123", "pmid": "456"},
    )
    re_loaded = PaperRef.model_validate(ref.model_dump())
    assert re_loaded.platform == "openalex"
    assert re_loaded.external_ids == {"openalex": "W123", "pmid": "456"}


def test_paper_ref_default_external_ids_is_empty_dict():
    ref = PaperRef(arxiv_id="2401.12345", title="Foo")
    assert ref.external_ids == {}
    assert ref.platform is None


def test_paper_summary_round_trip():
    ref = PaperRef(arxiv_id="2401.12345", title="Foo")
    summary = PaperSummary(
        paper=ref,
        problem="P",
        method="M",
        key_results=["r1"],
        limitations=["l1"],
        techniques=["transformer"],
        summarized_at=datetime.now(UTC),
        summarizer_model="claude-opus-4-7",
    )
    dumped = summary.model_dump()
    re_loaded = PaperSummary.model_validate(dumped)
    assert re_loaded.paper.arxiv_id == "2401.12345"


def test_idea_card_score_range():
    with pytest.raises(ValidationError):
        IdeaCard(
            id="01",
            title="t",
            one_liner="x",
            motivation="m",
            proposed_approach="a",
            novelty_claim="n",
            expected_contribution="c",
            feasibility_score=2.0,  # out of range
            created_at=datetime.now(UTC),
        )


def test_experiment_plan_round_trip():
    plan = ExperimentPlan(
        id="exp01",
        idea_id="idea01",
        research_questions=["RQ1"],
        hypotheses=["H1"],
        datasets=[Dataset(name="ds", rationale="r")],
        baselines=[Baseline(name="b", why="w")],
        proposed_method="method",
        metrics=[Metric(name="acc", direction="max", primary=True)],
        ablations=[AblationAxis(factor="lr", levels=["1e-3", "1e-4"], purpose="p")],
        created_at=datetime.now(UTC),
    )
    re_loaded = ExperimentPlan.model_validate(plan.model_dump())
    assert re_loaded.metrics[0].primary is True


def test_review_transcript_with_critique_and_verdict():
    transcript = ReviewTranscript(
        run_id="run01",
        experiment_id="exp01",
        rounds_completed=1,
        critiques=[
            Critique(
                persona="methodology",
                round=1,
                severity="major",
                category="soundness",
                issue="missing baseline",
                suggestion="add X",
                created_at=datetime.now(UTC),
            )
        ],
        verdict=ReviewVerdict(
            decision="major_revision",
            rationale="significant gaps",
            must_fix=["add baseline X"],
        ),
        started_at=datetime.now(UTC),
    )
    re_loaded = ReviewTranscript.model_validate(transcript.model_dump())
    assert re_loaded.verdict.decision == "major_revision"
    assert re_loaded.critiques[0].persona == "methodology"


def test_critique_invalid_persona_rejected():
    with pytest.raises(ValidationError):
        Critique(
            persona="random",
            round=1,
            severity="minor",
            category="x",
            issue="i",
            suggestion="s",
            created_at=datetime.now(UTC),
        )


def test_draft_section_defaults():
    section = DraftSection(
        id="s1", section_name="introduction", updated_at=datetime.now(UTC)
    )
    assert section.status == "empty"
    assert section.text == ""

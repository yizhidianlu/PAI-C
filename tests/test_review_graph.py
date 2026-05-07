"""Multi-agent review graph tests — Phase 6 (the core)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.mcp_server.tools.experiment import experiment_start
from paic.mcp_server.tools.review import review_start, review_status, review_step
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.idea import IdeaCard


class _StubExperimentLLM:
    model = "stub-experiment"

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        from paic.graphs.experiment_graph import _DesignFields
        from paic.schemas.experiment import AblationAxis, Baseline, Dataset, Metric

        assert schema is _DesignFields
        return _DesignFields(
            research_questions=["RQ1"],
            hypotheses=["H1"],
            datasets=[Dataset(name="DS", rationale="r")],
            baselines=[Baseline(name="B1", why="w")],
            proposed_method="M",
            metrics=[Metric(name="acc", direction="max", primary=True)],
            ablations=[AblationAxis(factor="lr", levels=["a", "b"], purpose="p")],
            compute_budget="cb",
            success_criteria=["s"],
            threats_to_validity=["t"],
            timeline_weeks=2,
        )


class _StubReviewLLM:
    """Returns canned outputs based on the schema requested.

    Records calls so we can assert the persona prompts were invoked.
    """

    model = "stub-review"

    def __init__(self):
        self.calls: list[dict] = []

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        from paic.graphs.review_graph import (
            _ModeratorOutput,
            _PersonaCritique,
            _PersonaCritiqueOutput,
            _VerdictOutput,
        )

        # Identify which prompt this is by a phrase in the system message
        marker = "?"
        if "Methodology Reviewer" in system:
            marker = "methodology"
        elif "Statistics & Data Reviewer" in system:
            marker = "statistics"
        elif "Domain Expert Reviewer" in system:
            marker = "domain"
        elif "Reviewer 2" in system:
            marker = "reviewer2"
        elif "moderator" in system.lower() or "chair / moderator" in system.lower():
            marker = "moderator"
        elif "final verdict" in system.lower() or "panel chair" in system.lower():
            marker = "verdict"
        self.calls.append({"persona": marker, "schema": schema.__name__, "user": user})

        if schema is _PersonaCritiqueOutput:
            return _PersonaCritiqueOutput(
                critiques=[
                    _PersonaCritique(
                        severity="major",
                        category=f"{marker}_issue",
                        issue=f"{marker} flagged a problem",
                        suggestion=f"do something about it ({marker})",
                        cited_papers=[],
                    )
                ]
            )
        if schema is _ModeratorOutput:
            return _ModeratorOutput(
                issues=[
                    {
                        "rank": 1,
                        "severity": "major",
                        "title": "Top issue",
                        "description": "synthesized description",
                        "raised_by": ["methodology", "statistics"],
                        "suggested_resolution": "add more baselines",
                    }
                ],
                open_questions_for_author=["Use baseline X or Y?"],
                panel_summary="Mostly sound but needs another baseline.",
            )
        if schema is _VerdictOutput:
            return _VerdictOutput(
                decision="minor_revision",
                rationale="Issues are addressable.",
                must_fix=["Add baseline X"],
                nice_to_fix=["Polish prose"],
            )
        raise AssertionError(f"Unexpected schema: {schema}")


@pytest.fixture
def project_with_experiment(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    from paic.graphs.checkpointer import reset_checkpointer_cache
    from paic.mcp_server.runs import reset_registry_cache

    reset_config_cache()
    reset_checkpointer_cache()
    reset_registry_cache()

    p = tmp_path / "p"
    workspace_init(p)
    idea = IdeaCard(
        id="idea_test",
        title="t", one_liner="x", motivation="m",
        proposed_approach="a", novelty_claim="n",
        expected_contribution="c",
        grounded_in=["2401.12345"],
        feasibility_score=0.6, novelty_score=0.6, impact_score=0.6, composite_score=0.6,
        created_at=datetime.now(UTC),
    )
    from paic.workspace.store import save_yaml

    save_yaml(p / ".paic/ideas/idea_test.yaml", idea.model_dump(mode="json"))

    exp_out = experiment_start(str(p), "idea_test", llm=_StubExperimentLLM())
    return p, exp_out["experiment_id"]


def test_review_pauses_after_first_round(project_with_experiment):
    p, exp_id = project_with_experiment
    stub = _StubReviewLLM()
    out = review_start(str(p), exp_id, rounds=2, llm=stub)
    assert out["status"] == "awaiting_input"
    assert out["round"] == 1
    assert out["panel"] is not None
    assert out["panel"]["round"] == 1
    # All 4 personas should have been called once + 1 moderator call.
    persona_calls = [c["persona"] for c in stub.calls]
    assert persona_calls.count("methodology") == 1
    assert persona_calls.count("statistics") == 1
    assert persona_calls.count("domain") == 1
    assert persona_calls.count("reviewer2") == 1
    assert persona_calls.count("moderator") == 1


def test_review_advances_through_rounds_to_verdict(project_with_experiment):
    p, exp_id = project_with_experiment
    stub = _StubReviewLLM()
    start = review_start(str(p), exp_id, rounds=2, llm=stub)
    run_id = start["run_id"]

    # Round 1 -> Round 2
    after1 = review_step(str(p), run_id, rebuttal="Will add baseline X.", llm=stub)
    assert after1["status"] == "awaiting_input"
    assert after1["round"] == 2

    # Round 2 -> verdict
    after2 = review_step(str(p), run_id, rebuttal="Final response.", llm=stub)
    assert after2["status"] == "done"
    assert after2["verdict"]["decision"] == "minor_revision"

    # Transcript artifacts should be on disk
    review_dir = p / ".paic/reviews" / exp_id
    assert (review_dir / "transcript.yaml").is_file()
    assert (review_dir / "verdict.yaml").is_file()
    assert (review_dir / "round_1.md").is_file()
    assert (review_dir / "round_2.md").is_file()


def test_round1_persona_prompt_has_no_previous_round_context(project_with_experiment):
    """Round 1 personas should NOT see PREVIOUS ROUND context (none exists)."""
    p, exp_id = project_with_experiment
    stub = _StubReviewLLM()
    review_start(str(p), exp_id, rounds=2, llm=stub)
    # Round 1 calls only — review_start pauses before round 2.
    persona_calls = [c for c in stub.calls if c["persona"] in {"methodology", "statistics", "domain", "reviewer2"}]
    assert len(persona_calls) == 4
    for call in persona_calls:
        assert "PREVIOUS ROUND" not in (call["user"] or ""), (
            f"Round 1 {call['persona']} prompt unexpectedly contains PREVIOUS ROUND segment"
        )


def test_round2_persona_prompts_include_previous_round_context(project_with_experiment):
    """Round 2 personas + moderator must see prior moderator summary + author rebuttal."""
    p, exp_id = project_with_experiment
    stub = _StubReviewLLM()
    start = review_start(str(p), exp_id, rounds=2, llm=stub)
    run_id = start["run_id"]
    rebuttal_text = "Will add LongBench baseline; bumping seeds to 5."
    review_step(str(p), run_id, rebuttal=rebuttal_text, llm=stub)

    # Slice out only round 2 calls — they come AFTER the round 1 set
    # (4 personas + 1 moderator = 5 entries before round 2).
    round2_calls = stub.calls[5:]
    persona_calls_r2 = [c for c in round2_calls if c["persona"] in {"methodology", "statistics", "domain", "reviewer2"}]
    moderator_calls_r2 = [c for c in round2_calls if c["persona"] == "moderator"]

    assert len(persona_calls_r2) == 4
    assert len(moderator_calls_r2) == 1

    # Each round-2 persona prompt must include both segments.
    for call in persona_calls_r2:
        msg = call["user"] or ""
        assert "PREVIOUS ROUND PANEL SUMMARY" in msg, (
            f"Round 2 {call['persona']} missing prior panel summary"
        )
        assert "PREVIOUS ROUND AUTHOR REBUTTAL" in msg, (
            f"Round 2 {call['persona']} missing prior rebuttal"
        )
        assert rebuttal_text in msg, (
            f"Round 2 {call['persona']} prompt does not contain the actual rebuttal text"
        )

    # The moderator in round 2 should also see prior context for continuity.
    msg = moderator_calls_r2[0]["user"] or ""
    assert "PREVIOUS ROUND PANEL SUMMARY" in msg, (
        "Round 2 moderator missing prior panel summary"
    )


def test_review_skip_to_verdict(project_with_experiment):
    p, exp_id = project_with_experiment
    stub = _StubReviewLLM()
    start = review_start(str(p), exp_id, rounds=3, llm=stub)
    out = review_step(str(p), run_id=start["run_id"], skip_to_verdict=True, llm=stub)
    assert out["status"] == "done"
    assert out["round"] == 1


def test_review_status_readonly(project_with_experiment):
    p, exp_id = project_with_experiment
    stub = _StubReviewLLM()
    start = review_start(str(p), exp_id, rounds=1, llm=stub)
    s = review_status(str(p), start["run_id"])
    assert s["round"] == 1
    assert s["verdict"] is None  # paused before verdict


def test_review_unknown_persona_rejected(project_with_experiment):
    p, exp_id = project_with_experiment
    out = review_start(str(p), exp_id, personas=["random"], llm=_StubReviewLLM())
    assert out["error"] == "unknown_personas"


def test_review_subset_of_personas(project_with_experiment):
    p, exp_id = project_with_experiment
    stub = _StubReviewLLM()
    out = review_start(
        str(p), exp_id, personas=["methodology", "statistics"], rounds=1, llm=stub
    )
    assert out["status"] == "awaiting_input"
    persona_calls = [c["persona"] for c in stub.calls]
    assert persona_calls.count("methodology") == 1
    assert persona_calls.count("statistics") == 1
    assert persona_calls.count("domain") == 0
    assert persona_calls.count("reviewer2") == 0


# ----------------------------------- experiment YAML schema validation
def test_review_start_returns_experiment_not_found_when_missing(project_with_experiment):
    p, _ = project_with_experiment
    out = review_start(str(p), "exp_does_not_exist", llm=_StubReviewLLM())
    assert out["error"] == "experiment_not_found"
    assert out["experiment_id"] == "exp_does_not_exist"


def test_review_start_returns_schema_invalid_for_dict_metrics(project_with_experiment):
    """Hand-written YAML with metrics-as-dict instead of list-of-dicts must surface
    ``experiment_schema_invalid`` (not crash inside the graph as graph_failed).
    """
    import yaml

    p, _ = project_with_experiment
    bad_path = p / ".paic/experiments/exp_handwritten_bad.yaml"
    bad_path.write_text(
        yaml.safe_dump(
            {
                "id": "exp_handwritten_bad",
                "idea_id": "idea_test",
                "proposed_method": "Some method.",
                "created_at": "2026-05-05T00:00:00Z",
                # ❌ dict instead of list[dict] — the bug we're guarding against
                "metrics": {"name": "acc", "direction": "max", "primary": True},
            }
        ),
        encoding="utf-8",
    )

    out = review_start(str(p), "exp_handwritten_bad", llm=_StubReviewLLM())
    assert out["error"] == "experiment_schema_invalid"
    assert out["experiment_id"] == "exp_handwritten_bad"
    assert isinstance(out["detail"], list)
    # Must mention metrics in some pydantic error
    assert any("metrics" in str(err.get("loc", [])) for err in out["detail"])
    assert "metrics" in out["hint"].lower()


def test_review_start_passes_well_formed_yaml(project_with_experiment):
    """Sanity check: hand-written but valid YAML still flows through."""
    import yaml

    p, _ = project_with_experiment
    good_path = p / ".paic/experiments/exp_handwritten_good.yaml"
    good_path.write_text(
        yaml.safe_dump(
            {
                "id": "exp_handwritten_good",
                "idea_id": "idea_test",
                "proposed_method": "Some method.",
                "created_at": "2026-05-05T00:00:00Z",
                "metrics": [
                    {"name": "acc", "direction": "max", "primary": True}
                ],
                "research_questions": ["RQ1"],
            }
        ),
        encoding="utf-8",
    )

    out = review_start(
        str(p), "exp_handwritten_good", rounds=1, llm=_StubReviewLLM()
    )
    # Either awaiting_input (round 1 panel) or done — both mean we got past validation.
    assert out.get("error") is None
    assert out["status"] in ("awaiting_input", "done")

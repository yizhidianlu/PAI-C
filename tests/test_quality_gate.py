"""Tests for §quality phase 10 — final paper-level quality gate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.latex.quality_gate import (
    check_contribution_consistency,
    check_duplicate_paragraphs,
    check_figure_coverage,
    check_numeric_provenance,
    check_section_length_balance,
    check_undefined_cites_refs,
    check_unresolved_todos,
    check_unsupported_claims,
    run_quality_gate,
)
from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.quality_gate import quality_gate_run_tool
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    p = tmp_path / "p"
    workspace_init(p)
    return p


def _write_section(project_dir, name, text):
    paths = resolve_project(str(project_dir))
    sec_dir = paths.drafts_dir / "sections"
    sec_dir.mkdir(parents=True, exist_ok=True)
    (sec_dir / f"{name}.tex").write_text(text, encoding="utf-8")


def _write_paper_plan(project_dir, contributions, section_plan=None):
    paths = resolve_project(str(project_dir))
    save_yaml(paths.paper_plan_yaml, {
        "schema_version": 1,
        "thesis": "T",
        "contributions": contributions,
        "section_plan": section_plan or [],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    })


def _write_claims(project_dir, claims):
    paths = resolve_project(str(project_dir))
    save_yaml(paths.claims_yaml, {
        "schema_version": 1, "claims": claims,
    })


def _write_experiment(project_dir, exp_id: str, *, results: list[dict] | None = None):
    """Write a minimal experiment yaml with optional ``results[]`` entries.

    Used by numeric_provenance tests so the gate can actually load results,
    not just rely on existence checks.
    """
    paths = resolve_project(str(project_dir))
    paths.experiments_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "id": exp_id,
        "idea_id": "idea_x",
        "research_questions": ["q?"],
        "hypotheses": ["h"],
        "datasets": [{"name": "ds1", "rationale": "x"}],
        "baselines": [{"name": "b1", "why": "y"}],
        "proposed_method": "m",
        "metrics": [{"name": "accuracy", "direction": "max", "primary": True}],
        "ablations": [],
        "compute_budget": "1xA100",
        "success_criteria": ["+3%"],
        "threats_to_validity": [],
        "created_at": datetime.now(UTC).isoformat(),
        "status": "draft",
        "results": results or [],
    }
    save_yaml(paths.experiments_dir / f"{exp_id}.yaml", raw)


# ----------------------------------------------------- check_undefined_cites_refs


def test_undefined_cite_is_blocker():
    sections = {
        "01_intro": "We rely on prior work \\cite{unknown_paper}.",
    }
    library = {"arxiv_p1"}
    issues = check_undefined_cites_refs(sections, library)
    assert any(i.kind == "undefined_cites_refs" and i.severity == "blocker" for i in issues)


def test_known_cite_no_issue():
    sections = {
        "01_intro": "Known paper \\cite{arxiv_p1}.",
    }
    issues = check_undefined_cites_refs(sections, {"arxiv_p1"})
    assert issues == []


def test_undefined_ref_is_major():
    sections = {
        "03_method": "See Figure \\ref{fig:method} for the pipeline.",
    }
    issues = check_undefined_cites_refs(sections, set())
    assert any(i.kind == "undefined_cites_refs" and i.severity == "major" for i in issues)


def test_ref_resolves_via_cross_section_label():
    sections = {
        "03_method": "See Figure \\ref{fig:method}.",
        "04_experiments": "\\begin{figure}\\label{fig:method}\\end{figure}",
    }
    issues = check_undefined_cites_refs(sections, set())
    assert all(i.severity != "major" or "ref" not in i.detail.lower() for i in issues)


# ----------------------------------------------------- check_unresolved_todos


def test_todo_macro_flagged():
    sections = {
        "01_intro": "We propose X. \\todo{add citation}",
    }
    issues = check_unresolved_todos(sections)
    assert len(issues) == 1
    assert issues[0].kind == "unresolved_todos"


def test_todo_comment_flagged():
    sections = {
        "01_intro": "We propose X.\n% TODO: rewrite this paragraph",
    }
    issues = check_unresolved_todos(sections)
    assert len(issues) == 1


def test_no_todos_no_issue():
    issues = check_unresolved_todos({"01_intro": "We propose X."})
    assert issues == []


# ----------------------------------------------------- check_duplicate_paragraphs


def test_duplicate_paragraphs_flagged():
    paragraph = "We propose a novel channel pruning method based on Fisher score for cross-subject EEG decoding under small-sample conditions which significantly improves balanced accuracy. " * 2
    sections = {
        "01_intro": paragraph,
        "06_conclusion": paragraph,
    }
    issues = check_duplicate_paragraphs(sections, threshold=85)
    assert any(i.kind == "duplicate_paragraphs" for i in issues)


def test_distinct_paragraphs_no_issue():
    sections = {
        "01_intro": "We propose channel pruning for EEG decoding under small-sample conditions which improves accuracy by a wide margin in cross-subject benchmarks.",
        "06_conclusion": "Our experiments demonstrate scalable inference times on consumer hardware while preserving end-to-end latency requirements for online use.",
    }
    issues = check_duplicate_paragraphs(sections)
    assert issues == []


# ----------------------------------------------------- check_contribution_consistency


def test_contribution_count_mismatch_flagged():
    sections = {
        "00_abstract": "We make three contributions: \\item A. \\item B. \\item C.",
        "01_intro": "Two contributions: \\item A. \\item B.",
        "06_conclusion": "Four contributions: \\item A. \\item B. \\item C. \\item D.",
    }
    paper_plan = {"contributions": [{"id": "C1"}, {"id": "C2"}, {"id": "C3"}]}
    issues = check_contribution_consistency(sections, paper_plan)
    assert any(i.kind == "contribution_consistency" for i in issues)


def test_contribution_consistent_no_issue():
    sections = {
        "00_abstract": "Three contributions: \\item A. \\item B. \\item C.",
        "01_intro": "Three contributions: \\item A. \\item B. \\item C.",
    }
    paper_plan = {"contributions": [{"id": "C1"}, {"id": "C2"}, {"id": "C3"}]}
    issues = check_contribution_consistency(sections, paper_plan)
    assert issues == []


# ----------------------------------------------------- check_section_length_balance


def test_section_too_short_flagged():
    sections = {"01_intro": "We propose X."}  # < 5 words
    paper_plan = {"section_plan": [{"name": "01_intro", "target_words": 1000}]}
    issues = check_section_length_balance(sections, paper_plan)
    assert any(i.kind == "section_length_balance" for i in issues)


def test_section_too_long_flagged():
    long = "word " * 3000  # 3000 words
    sections = {"01_intro": long}
    paper_plan = {"section_plan": [{"name": "01_intro", "target_words": 500}]}
    issues = check_section_length_balance(sections, paper_plan)
    assert any(i.kind == "section_length_balance" for i in issues)


def test_no_paper_plan_no_length_check():
    sections = {"01_intro": "Short."}
    issues = check_section_length_balance(sections, paper_plan=None)
    assert issues == []


# ----------------------------------------------------- check_unsupported_claims


def test_unsupported_strong_claim_flagged(project):
    _write_claims(project, [{
        "id": "CL1", "type": "novelty", "status": "needs_evidence",
        "text": "Novel method.",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_unsupported_claims(paths)
    assert len(issues) == 1
    assert issues[0].kind == "unsupported_claims"


def test_supported_claim_no_issue(project):
    _write_claims(project, [{
        "id": "CL1", "type": "novelty", "status": "supported",
        "text": "Novel method.", "supporting_papers": ["arxiv_p1"],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_unsupported_claims(paths)
    assert issues == []


def test_factual_needs_evidence_no_issue(project):
    """Factual claims aren't 'strong' — they're allowed to lack evidence."""
    _write_claims(project, [{
        "id": "CL1", "type": "factual", "status": "needs_evidence",
        "text": "EEG is noisy.",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_unsupported_claims(paths)
    assert issues == []


# ----------------------------------------------------- check_numeric_provenance


def test_numeric_claim_without_provenance_flagged(project):
    _write_claims(project, [{
        "id": "CL1", "type": "numeric", "status": "needs_evidence",
        "text": "We achieve 78.4% accuracy.",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_numeric_provenance(paths, experiment_ids=set())
    assert any(i.kind == "numeric_provenance" for i in issues)


def test_numeric_claim_with_experiment_results_match_no_issue(project):
    """Numeric claim whose value matches a recorded ExperimentResult passes."""
    _write_experiment(project, "exp_1", results=[
        {"metric_name": "accuracy", "value": 78.4, "unit": "%", "run_id": "r1"},
    ])
    _write_claims(project, [{
        "id": "CL1", "type": "numeric", "status": "supported",
        "text": "78.4% accuracy.",
        "supporting_experiments": ["exp_1"],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_numeric_provenance(paths, experiment_ids={"exp_1"})
    assert issues == []


def test_numeric_claim_with_experiment_but_no_results_flagged_major(project):
    """Experiment exists but ``results[]`` empty → ``numeric_no_results_recorded``
    (major, not blocker — backwards-compatible with v0.1 projects)."""
    _write_experiment(project, "exp_1", results=[])
    _write_claims(project, [{
        "id": "CL1", "type": "numeric", "status": "supported",
        "text": "78.4% accuracy.",
        "supporting_experiments": ["exp_1"],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_numeric_provenance(paths, experiment_ids={"exp_1"})
    assert any(
        i.kind == "numeric_no_results_recorded" and i.severity == "major"
        for i in issues
    )


def test_numeric_claim_unmatched_result_is_blocker(project):
    """Claim says 4.2% but recorded result is 4.0% → ``numeric_unmatched`` blocker."""
    _write_experiment(project, "exp_1", results=[
        {"metric_name": "accuracy", "value": 4.0, "unit": "%", "run_id": "r1"},
    ])
    _write_claims(project, [{
        "id": "CL1", "type": "numeric", "status": "supported",
        "text": "We achieve 4.2% gain.",
        "supporting_experiments": ["exp_1"],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_numeric_provenance(paths, experiment_ids={"exp_1"})
    assert any(
        i.kind == "numeric_unmatched" and i.severity == "blocker"
        for i in issues
    )


def test_numeric_claim_decimal_result_matches_percent_claim(project):
    """results.value=0.923 should match a claim that says 92.3% (auto scaling)."""
    _write_experiment(project, "exp_1", results=[
        {"metric_name": "accuracy", "value": 0.923, "unit": "", "run_id": "r1"},
    ])
    _write_claims(project, [{
        "id": "CL1", "type": "numeric", "status": "supported",
        "text": "92.3% accuracy on the held-out split.",
        "supporting_experiments": ["exp_1"],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_numeric_provenance(paths, experiment_ids={"exp_1"})
    assert issues == []


def test_numeric_claim_blocker_cannot_be_overridden_via_gate(project):
    """End-to-end: claim says 4.2% but result is 4.0%; user adds the kind to
    overrides; the run_quality_gate output keeps the blocker and reports
    overrides_rejected (item 1 + item 5 working together).
    """
    _write_experiment(project, "exp_1", results=[
        {"metric_name": "accuracy", "value": 4.0, "unit": "%", "run_id": "r1"},
    ])
    _write_claims(project, [{
        "id": "CL1", "type": "numeric", "status": "supported",
        "text": "We achieve 4.2% gain.",
        "supporting_experiments": ["exp_1"],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    res = run_quality_gate(paths, overrides=["numeric_unmatched"])
    assert res.passed is False
    assert any(
        i.kind == "numeric_unmatched" and i.severity == "blocker"
        for i in res.issues
    )
    assert any(
        r["kind"] == "numeric_unmatched" and r["severity"] == "blocker"
        for r in res.overrides_rejected
    )


def test_numeric_claim_with_external_cite_no_issue(project):
    """External numeric (e.g. quoting another paper's number) is OK with cite."""
    _write_claims(project, [{
        "id": "CL1", "type": "numeric", "status": "supported",
        "text": "Prior work reports 73.2%.",
        "required_citations": ["arxiv_priorwork"],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    issues = check_numeric_provenance(paths, experiment_ids=set())
    assert issues == []


# ----------------------------------------------------- run_quality_gate


def test_clean_project_passes(project):
    library_add_tool(str(project), [
        {"arxiv_id": "p1", "title": "Lib paper", "authors": ["A"]},
    ])
    _write_section(project, "01_intro",
        "We propose X. Prior work \\cite{arxiv_p1}.")
    _write_paper_plan(project, contributions=[{"id": "C1", "title": "x", "description": "y"}])
    paths = resolve_project(str(project))
    res = run_quality_gate(paths)
    assert res.passed is True


def test_dirty_project_fails(project):
    """Project with unknown cite + TODO + unsupported claim should fail."""
    _write_section(project, "01_intro",
        "We propose X \\cite{unknown}. \\todo{add baseline}.")
    _write_claims(project, [{
        "id": "CL1", "type": "novelty", "status": "needs_evidence",
        "text": "Novel.",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }])
    paths = resolve_project(str(project))
    res = run_quality_gate(paths)
    assert res.passed is False
    kinds = {i.kind for i in res.issues}
    assert "undefined_cites_refs" in kinds
    assert "unresolved_todos" in kinds
    assert "unsupported_claims" in kinds


def test_overrides_drop_kind(project):
    _write_section(project, "01_intro", "X. \\todo{fix me}")
    paths = resolve_project(str(project))
    res = run_quality_gate(paths, overrides=["unresolved_todos"])
    kinds = {i.kind for i in res.issues}
    assert "unresolved_todos" not in kinds
    assert res.overrides == ["unresolved_todos"]
    assert res.overrides_rejected == []  # major-severity overrides take effect


def test_overrides_cannot_drop_blocker(project):
    """Blocker-severity issues stay regardless of overrides; rejection is surfaced."""
    # An undefined cite is the canonical blocker (no library entry for `nonexistent`).
    _write_section(project, "01_intro", "X. \\cite{nonexistent}")
    paths = resolve_project(str(project))
    res = run_quality_gate(paths, overrides=["undefined_cites_refs"])
    kinds = {i.kind for i in res.issues}
    # Blocker stays in the kept list…
    assert "undefined_cites_refs" in kinds
    # …and is reported as rejected.
    assert any(
        r["kind"] == "undefined_cites_refs" and r["severity"] == "blocker"
        for r in res.overrides_rejected
    )
    # passed must stay False because a blocker survived.
    assert res.passed is False


def test_strict_mode_ignores_overrides(project):
    """strict=True ignores overrides entirely — every issue counts toward passed."""
    _write_section(project, "01_intro", "X. \\todo{fix me}")
    paths = resolve_project(str(project))
    res = run_quality_gate(paths, overrides=["unresolved_todos"], strict=True)
    kinds = {i.kind for i in res.issues}
    # The TODO survives despite the override.
    assert "unresolved_todos" in kinds
    assert res.strict is True
    # passed=False because the major issue is still there.
    assert res.passed is False


def test_strict_mode_passes_clean_project(project):
    """strict=True still passes a project with zero issues."""
    library_add_tool(str(project), [
        {"arxiv_id": "clean1", "title": "x", "authors": ["A"]},
    ])
    _write_section(project, "01_intro", "We propose X. \\cite{arxiv_clean1}.")
    paths = resolve_project(str(project))
    res = run_quality_gate(paths, strict=True)
    assert res.passed is True
    assert res.strict is True
    assert res.overrides_rejected == []


def test_tool_returns_serializable_payload(project):
    library_add_tool(str(project), [
        {"arxiv_id": "p1", "title": "x", "authors": ["A"]},
    ])
    _write_section(project, "01_intro", "We propose X. \\cite{arxiv_p1}.")
    res = quality_gate_run_tool(str(project))
    assert res.get("error") is None
    assert "passed" in res
    assert "issues" in res
    assert isinstance(res["issues"], list)


def test_tool_uninitialized_project_errors(tmp_path):
    res = quality_gate_run_tool(str(tmp_path / "nope"))
    assert res["error"] == "project_not_initialized"


# ----------------------------------------------------- check_figure_coverage


def _write_figure_plan(project_dir, slots: list[dict]):
    """Write a minimal _plan.yaml with the supplied slot dicts."""
    paths = resolve_project(str(project_dir))
    paths.figures_dir.mkdir(parents=True, exist_ok=True)
    save_yaml(
        paths.figures_dir / "_plan.yaml",
        {
            "plan_id": "test_plan",
            "draft_path": None,
            "created_at": datetime.now(UTC).isoformat(),
            "slots": slots,
            "coverage_warnings": [],
        },
    )


def _write_figure_meta(project_dir, slot: str, brief: dict):
    """Write a minimal meta.yaml for a generated figure with one version entry."""
    paths = resolve_project(str(project_dir))
    sdir = paths.figures_dir / slot
    sdir.mkdir(parents=True, exist_ok=True)
    save_yaml(
        sdir / "meta.yaml",
        {
            "slot": slot,
            "versions": [
                {
                    "version": "v1",
                    "kind": "generate",
                    "model": "gpt-image-2",
                    "prompt": "stub prompt",
                    "parent_version": None,
                    "created_at": datetime.now(UTC).isoformat(),
                    "brief": brief,
                }
            ],
        },
    )


def test_figure_coverage_no_plan_returns_empty(project):
    paths = resolve_project(str(project))
    assert check_figure_coverage(paths) == []


def test_figure_coverage_uncovered_contribution_flagged(project):
    """Contribution with no figure slot binding produces a major issue."""
    _write_paper_plan(project, [{"id": "C1", "title": "Speed", "description": "Fast."}])
    _write_figure_plan(project, [
        {
            "slot": "teaser",
            "kind": "teaser",
            "section_hint": "01_intro",
            "scene_description": "...",
            "supporting_claims": [],  # no binding
            "primary_claim_id": None,
        },
    ])
    paths = resolve_project(str(project))
    issues = check_figure_coverage(paths)
    uncovered = [i for i in issues if i.kind == "figure_contribution_uncovered"]
    assert len(uncovered) == 1
    assert uncovered[0].severity == "major"
    assert uncovered[0].target == "C1"


def test_figure_coverage_contribution_covered_no_issue(project):
    _write_paper_plan(project, [{"id": "C1", "title": "Speed", "description": "Fast."}])
    _write_figure_plan(project, [
        {
            "slot": "teaser",
            "kind": "teaser",
            "section_hint": "01_intro",
            "scene_description": "...",
            "supporting_claims": ["C1"],
            "primary_claim_id": "C1",
        },
    ])
    paths = resolve_project(str(project))
    issues = check_figure_coverage(paths)
    assert not any(i.kind == "figure_contribution_uncovered" for i in issues)


def test_figure_coverage_no_visual_reason_counts_as_covered(project):
    _write_paper_plan(project, [{"id": "C1", "title": "Theorem", "description": "..."}])
    _write_figure_plan(project, [
        {
            "slot": "_skipped",
            "kind": "concept",
            "section_hint": "03_method",
            "scene_description": "(none)",
            "supporting_claims": ["C1"],
            "no_visual_reason": "equation-only contribution",
        },
    ])
    paths = resolve_project(str(project))
    issues = check_figure_coverage(paths)
    assert not any(i.kind == "figure_contribution_uncovered" for i in issues)


def test_figure_coverage_dangling_claim_id_flagged(project):
    """supporting_claims pointing at undefined ids → figure_dangling_claim major."""
    _write_paper_plan(project, [{"id": "C1", "title": "X", "description": "..."}])
    _write_claims(project, [
        {"id": "CL1", "type": "factual", "status": "supported",
         "text": "x", "contribution_id": "C1",
         "created_at": datetime.now(UTC).isoformat(),
         "updated_at": datetime.now(UTC).isoformat()},
    ])
    _write_figure_plan(project, [
        {
            "slot": "teaser",
            "kind": "teaser",
            "section_hint": "01_intro",
            "scene_description": "...",
            "supporting_claims": ["CL1", "CL_GHOST"],  # CL_GHOST not in claims.yaml
            "primary_claim_id": "CL1",
        },
    ])
    paths = resolve_project(str(project))
    issues = check_figure_coverage(paths)
    dangling = [i for i in issues if i.kind == "figure_dangling_claim"]
    assert len(dangling) == 1
    assert dangling[0].severity == "major"
    assert "CL_GHOST" in dangling[0].target


def test_figure_coverage_brief_drift_flagged_as_minor(project):
    """meta.yaml brief snapshot != current plan supporting_claims → drift minor."""
    _write_paper_plan(project, [{"id": "C1", "title": "X", "description": "..."}])
    _write_claims(project, [
        {"id": "CL1", "type": "factual", "status": "supported",
         "text": "x", "contribution_id": "C1",
         "created_at": datetime.now(UTC).isoformat(),
         "updated_at": datetime.now(UTC).isoformat()},
        {"id": "CL2", "type": "factual", "status": "supported",
         "text": "y", "contribution_id": "C1",
         "created_at": datetime.now(UTC).isoformat(),
         "updated_at": datetime.now(UTC).isoformat()},
    ])
    _write_figure_plan(project, [
        {
            "slot": "teaser",
            "kind": "teaser",
            "section_hint": "01_intro",
            "scene_description": "...",
            "supporting_claims": ["CL1", "CL2"],  # current plan
            "primary_claim_id": "CL1",
        },
    ])
    # generated against just CL1 — plan was edited after the fact
    _write_figure_meta(project, "teaser", brief={
        "scene_description": "...",
        "supporting_claims": ["CL1"],
        "primary_claim_id": "CL1",
    })
    paths = resolve_project(str(project))
    issues = check_figure_coverage(paths)
    drift = [i for i in issues if i.kind == "figure_brief_drift"]
    assert len(drift) == 1
    assert drift[0].severity == "minor"
    assert drift[0].target == "teaser"


def test_figure_coverage_brief_match_no_drift(project):
    _write_paper_plan(project, [{"id": "C1", "title": "X", "description": "..."}])
    _write_claims(project, [
        {"id": "CL1", "type": "factual", "status": "supported",
         "text": "x", "contribution_id": "C1",
         "created_at": datetime.now(UTC).isoformat(),
         "updated_at": datetime.now(UTC).isoformat()},
    ])
    _write_figure_plan(project, [
        {
            "slot": "teaser",
            "kind": "teaser",
            "section_hint": "01_intro",
            "scene_description": "...",
            "supporting_claims": ["CL1"],
            "primary_claim_id": "CL1",
        },
    ])
    _write_figure_meta(project, "teaser", brief={
        "scene_description": "...",
        "supporting_claims": ["CL1"],
        "primary_claim_id": "CL1",
    })
    paths = resolve_project(str(project))
    issues = check_figure_coverage(paths)
    assert not any(i.kind == "figure_brief_drift" for i in issues)


def test_figure_coverage_overrides_drop_minor_drift(project):
    """run_quality_gate honors overrides=['figure_brief_drift'] for the minor drift kind."""
    _write_paper_plan(project, [{"id": "C1", "title": "X", "description": "..."}])
    _write_claims(project, [
        {"id": "CL1", "type": "factual", "status": "supported",
         "text": "x", "contribution_id": "C1",
         "created_at": datetime.now(UTC).isoformat(),
         "updated_at": datetime.now(UTC).isoformat()},
    ])
    _write_figure_plan(project, [
        {"slot": "teaser", "kind": "teaser", "section_hint": "01_intro",
         "scene_description": "...", "supporting_claims": ["CL1"],
         "primary_claim_id": "CL1"},
    ])
    _write_figure_meta(project, "teaser", brief={
        "scene_description": "...",
        "supporting_claims": [],  # drift on purpose
        "primary_claim_id": None,
    })
    # Need to satisfy ANTHROPIC_API_KEY check or run_quality_gate may error
    # — but it doesn't, run_quality_gate only reads project state. Good.
    paths = resolve_project(str(project))
    res = run_quality_gate(paths, overrides=["figure_brief_drift"])
    # drift suppressed — but contribution still uncovered (major) so passed=False
    assert not any(i.kind == "figure_brief_drift" for i in res.issues)

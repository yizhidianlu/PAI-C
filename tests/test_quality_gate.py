"""Tests for §quality phase 10 — final paper-level quality gate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.latex.quality_gate import (
    check_contribution_consistency,
    check_duplicate_paragraphs,
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


def test_numeric_claim_with_experiment_no_issue(project):
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

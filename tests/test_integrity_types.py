"""Schema-level tests for integrity issue / result / WebSearch types."""

from __future__ import annotations

from paic.integrity.types import (
    AI_FAILURE_MODE_KINDS,
    DEFAULT_MANDATORY_MODES,
    HALLUCINATION_KINDS,
    AIFailurePrompt,
    IntegrityIssue,
    IntegrityResult,
    WebSearchPending,
)


def test_hallucination_kinds_disjoint_from_quality_gate():
    """Issue kinds must not collide with paic.latex.quality_gate's check kinds."""
    quality_gate_kinds = {
        "undefined_cites_refs", "unresolved_todos", "duplicate_paragraphs",
        "contribution_consistency", "section_length_balance",
        "unsupported_claims", "numeric_provenance", "figure_coverage",
        "latex_compile_warnings",
    }
    integrity_kinds = set(HALLUCINATION_KINDS) | set(AI_FAILURE_MODE_KINDS)
    assert quality_gate_kinds.isdisjoint(integrity_kinds)


def test_default_mandatory_modes_matches_v1_design():
    # V1.0 fusion plan §决策 4 (folroum decision): 1/3/5/6 mandatory
    assert DEFAULT_MANDATORY_MODES == (1, 3, 5, 6)


def test_integrity_issue_to_dict_round_trip():
    issue = IntegrityIssue(
        kind="TF",
        severity="blocker",
        target="lin_2020_qa",
        detail="No evidence the paper exists.",
        actionable_fix="Remove the citation.",
        evidence_url=["https://example.com/search?q=foo"],
        suggested_correction={"title": "Real Title"},
    )
    d = issue.to_dict()
    assert d["kind"] == "TF"
    assert d["severity"] == "blocker"
    assert d["target"] == "lin_2020_qa"
    assert d["evidence_url"] == ["https://example.com/search?q=foo"]
    assert d["suggested_correction"] == {"title": "Real Title"}


def test_integrity_issue_to_dict_omits_empty_optional_fields():
    issue = IntegrityIssue(
        kind="SH",
        severity="minor",
        target=None,
        detail="x",
        actionable_fix="y",
    )
    d = issue.to_dict()
    assert "evidence_url" not in d
    assert "suggested_correction" not in d


def test_integrity_result_passes_when_only_minor_issues():
    result = IntegrityResult(
        passed=False,  # will be recomputed by runner; we just check has_pending_work / to_dict
        mode="pre_review",
        issues=[
            IntegrityIssue(kind="SH", severity="minor", target="x", detail="d", actionable_fix="f"),
        ],
    )
    d = result.to_dict()
    assert d["mode"] == "pre_review"
    assert d["issue_count"] == 1
    assert d["mandatory_modes"] == [1, 3, 5, 6]
    assert d["has_pending_work"] is False


def test_integrity_result_has_pending_work_flag():
    result = IntegrityResult(
        passed=True,
        mode="pre_review",
        issues=[],
        pending_websearch=[
            WebSearchPending(cite_key="x", title="T", authors=["A"]),
        ],
    )
    assert result.has_pending_work is True
    assert result.to_dict()["has_pending_work"] is True


def test_websearch_pending_to_dict():
    p = WebSearchPending(
        cite_key="lin_2020_qa",
        title="Quality Assurance",
        authors=["Y. H. Lin"],
        year=2020,
        expected_doi="10.1007/foo",
    )
    d = p.to_dict()
    assert d["cite_key"] == "lin_2020_qa"
    assert d["authors"] == ["Y. H. Lin"]
    assert d["year"] == 2020
    assert d["expected_doi"] == "10.1007/foo"
    assert d["s2_attempted"] is True
    assert d["reason"] == "s2_no_match"


def test_ai_failure_prompt_skipped_emits_zero_inputs():
    p = AIFailurePrompt(
        mode=3,
        kind="AI_FAIL_M3",
        label="Hallucinated results",
        inputs={},
        skipped_reason="paper_kind=theoretical",
    )
    d = p.to_dict()
    assert d["mode"] == 3
    assert d["skipped_reason"] == "paper_kind=theoretical"
    assert d["inputs"] == {}

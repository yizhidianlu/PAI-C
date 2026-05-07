"""Tests for claim ledger — schema, init, extract, validate, persistence."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.library.claims import (
    _ExtractedClaim,
    _ExtractFields,
    _extract_inline_cites,
    extract_claims_from_section,
    init_claims_from_paper_plan,
    load_ledger,
    merge_claims,
    save_ledger,
    validate_claim,
    validate_ledger,
)
from paic.mcp_server.tools.claims import (
    claims_extract_tool,
    claims_init_tool,
    claims_list_tool,
    claims_validate_tool,
)
from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.paper_plan import paper_plan_create_tool, _PlanFields
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.claim import Claim, ClaimsLedger
from paic.schemas.paper_plan import ContributionEntry, PaperPlan
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


class _StubLLM:
    """Returns a fixed _ExtractFields response."""

    model = "stub-model"

    def __init__(self, response: _ExtractFields):
        self.response = response

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        assert schema is _ExtractFields
        return self.response


class _StubPlanLLM:
    """Returns a fixed _PlanFields for paper_plan_create."""

    model = "stub-plan"

    def __init__(self, response: _PlanFields):
        self.response = response

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        assert schema is _PlanFields
        return self.response


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    paths = resolve_project(str(project_dir))
    # Seed an idea + experiment so paper_plan_create works.
    save_yaml(paths.ideas_dir / "idea_1.yaml", {
        "id": "idea_1",
        "title": "Test idea",
        "one_liner": "Test paper.",
        "motivation": "x", "proposed_approach": "y",
        "novelty_claim": "z", "expected_contribution": "w",
        "grounded_in": [], "status": "selected",
        "created_at": datetime.now(UTC).isoformat(),
    })
    save_yaml(paths.experiments_dir / "exp_1.yaml", {
        "id": "exp_1", "idea_id": "idea_1",
        "research_questions": ["q?"], "hypotheses": ["h"],
        "datasets": [{"name": "ds1", "rationale": "x"}],
        "baselines": [{"name": "b1", "why": "y"}],
        "proposed_method": "method", "metrics": [{"name": "acc", "direction": "max", "primary": True}],
        "ablations": [], "compute_budget": "1xA100", "success_criteria": ["+3%"],
        "threats_to_validity": [], "created_at": datetime.now(UTC).isoformat(), "status": "draft",
    })
    library_add_tool(str(project_dir), [
        {"arxiv_id": "p1", "title": "Paper One", "authors": ["A"]},
        {"arxiv_id": "p2", "title": "Paper Two", "authors": ["B"]},
    ])
    return project_dir


def _seed_paper_plan(project_dir):
    """Run paper_plan_create_tool with a deterministic stub LLM."""
    fields = _PlanFields.model_validate({
        "thesis": "T",
        "contributions": [
            {"id": "C1", "title": "First contribution", "description": "Novel algorithm X."},
            {"id": "C2", "title": "Empirical study", "description": "Benchmark across N datasets."},
        ],
        "section_plan": [],
        "terminology": {}, "symbols": {},
        "figure_plan": [], "table_plan": [], "algorithm_plan": [],
        "open_todos": [],
    })
    llm = _StubPlanLLM(fields)
    paper_plan_create_tool(
        str(project_dir), idea_id="idea_1", experiment_id="exp_1", llm=llm
    )


# ----------------------------------------------------- schema


def test_claim_schema_round_trip():
    now = datetime.now(UTC)
    c = Claim(id="CL1", text="x", type="novelty", created_at=now, updated_at=now)
    dumped = c.model_dump(mode="json")
    reloaded = Claim.from_yaml_dict(dumped)
    assert reloaded.id == "CL1"
    assert reloaded.type == "novelty"


def test_claim_default_status_is_needs_evidence():
    now = datetime.now(UTC)
    c = Claim(id="CL1", text="x", type="numeric", created_at=now, updated_at=now)
    assert c.status == "needs_evidence"


def test_ledger_round_trip():
    now = datetime.now(UTC)
    ledger = ClaimsLedger(claims=[
        Claim(id="CL1", text="a", type="novelty", created_at=now, updated_at=now),
    ])
    reloaded = ClaimsLedger.from_yaml_dict(ledger.model_dump(mode="json"))
    assert len(reloaded.claims) == 1


# ----------------------------------------------------- init


def test_init_claims_from_paper_plan():
    now = datetime.now(UTC)
    plan = PaperPlan(
        thesis="t",
        contributions=[
            ContributionEntry(id="C1", title="One", description="First."),
            ContributionEntry(id="C2", title="Two", description="Second."),
        ],
        created_at=now, updated_at=now,
    )
    claims = init_claims_from_paper_plan(plan)
    assert len(claims) == 2
    assert all(c.type == "novelty" for c in claims)
    assert all(c.status == "needs_evidence" for c in claims)
    assert claims[0].contribution_id == "C1"
    assert claims[0].text == "First."


def test_init_claims_handles_dict_contributions():
    """Compatibility with raw yaml-loaded plans (dict, not pydantic)."""
    now = datetime.now(UTC)
    raw_plan = PaperPlan.from_yaml_dict({
        "thesis": "t",
        "contributions": [{"id": "C1", "title": "x", "description": "y"}],
        "created_at": now.isoformat(), "updated_at": now.isoformat(),
    })
    claims = init_claims_from_paper_plan(raw_plan)
    assert len(claims) == 1
    assert claims[0].text == "y"


# ----------------------------------------------------- extract


def test_extract_claims_picks_up_inline_cites():
    section_text = (
        "Our method outperforms baselines \\cite{paper_a, paper_b} on BCI-IV-2a "
        "by 3.2 percentage points."
    )
    response = _ExtractFields(claims=[
        _ExtractedClaim(
            text="Our method beats baselines on BCI-IV-2a by 3.2 percentage points.",
            type="comparative",
            status="supported",
            required_citations=["paper_a"],
        ),
    ])
    llm = _StubLLM(response)
    out = extract_claims_from_section(section_text, "04_experiments", llm=llm)
    assert len(out) == 1
    # Both inline cites are merged in.
    assert "paper_a" in out[0].required_citations
    assert "paper_b" in out[0].required_citations
    assert out[0].appears_in_sections == ["04_experiments"]


def test_extract_empty_text_returns_empty():
    llm = _StubLLM(_ExtractFields(claims=[]))
    out = extract_claims_from_section("", "01_intro", llm=llm)
    assert out == []


def test_extract_inline_cites_dedups():
    text = "\\cite{a, b} and \\cite{a} again \\citet{c}."
    assert _extract_inline_cites(text) == ["a", "b", "c"]


def test_extract_inline_cites_handles_no_cites():
    assert _extract_inline_cites("plain prose") == []


# ----------------------------------------------------- merge


def test_merge_dedupes_by_text():
    now = datetime.now(UTC)
    existing = [
        Claim(id="A", text="x is y", type="factual",
              created_at=now, updated_at=now,
              appears_in_sections=["01_intro"]),
    ]
    new = [
        Claim(id="B", text="X is Y", type="factual",  # case-insensitive match
              created_at=now, updated_at=now,
              appears_in_sections=["02_related"],
              required_citations=["c1"]),
    ]
    merged = merge_claims(existing, new)
    assert len(merged) == 1
    assert sorted(merged[0].appears_in_sections) == ["01_intro", "02_related"]
    assert merged[0].required_citations == ["c1"]
    # Original id preserved (existing wins).
    assert merged[0].id == "A"


def test_merge_keeps_distinct_claims():
    now = datetime.now(UTC)
    existing = [Claim(id="A", text="claim one", type="factual",
                      created_at=now, updated_at=now)]
    new = [Claim(id="B", text="claim two", type="factual",
                 created_at=now, updated_at=now)]
    merged = merge_claims(existing, new)
    assert len(merged) == 2


# ----------------------------------------------------- validate


def test_validate_flags_missing_cite():
    now = datetime.now(UTC)
    claim = Claim(
        id="CL1", text="x", type="comparative",
        required_citations=["unknown_paper"],
        created_at=now, updated_at=now,
    )
    issues = validate_claim(
        claim, library_cite_keys={"arxiv_p1"}, experiment_ids=set(),
    )
    assert any(i.kind == "missing_cite" for i in issues)


def test_validate_flags_unknown_experiment():
    now = datetime.now(UTC)
    claim = Claim(
        id="CL1", text="x", type="result",
        supporting_experiments=["exp_missing"],
        created_at=now, updated_at=now,
    )
    issues = validate_claim(
        claim, library_cite_keys=set(), experiment_ids={"exp_1"},
    )
    assert any(i.kind == "unknown_experiment" for i in issues)


def test_validate_flags_unsupported_strong_claim():
    now = datetime.now(UTC)
    claim = Claim(
        id="CL1", text="novel", type="novelty",
        created_at=now, updated_at=now,
    )
    issues = validate_claim(
        claim, library_cite_keys=set(), experiment_ids=set(),
    )
    assert any(i.kind == "unsupported_strong_claim" for i in issues)


def test_validate_does_not_flag_factual_without_support():
    """Factual claims aren't 'strong' — they're allowed to lack evidence."""
    now = datetime.now(UTC)
    claim = Claim(
        id="CL1", text="EEG is noisy", type="factual",
        created_at=now, updated_at=now,
    )
    issues = validate_claim(
        claim, library_cite_keys=set(), experiment_ids=set(),
    )
    assert all(i.kind != "unsupported_strong_claim" for i in issues)


def test_validate_supported_claim_no_issues():
    now = datetime.now(UTC)
    claim = Claim(
        id="CL1", text="x", type="comparative",
        status="supported",
        supporting_papers=["arxiv_p1"],
        required_citations=["arxiv_p1"],
        created_at=now, updated_at=now,
    )
    issues = validate_claim(
        claim, library_cite_keys={"arxiv_p1"}, experiment_ids=set(),
    )
    assert issues == []


# ----------------------------------------------------- tools


def test_init_tool_seeds_from_paper_plan(project):
    _seed_paper_plan(project)
    res = claims_init_tool(str(project))
    assert res.get("error") is None
    assert res["claims_count"] == 2
    assert res["added"] == 2

    # Idempotent — running again merges and adds nothing.
    res2 = claims_init_tool(str(project))
    assert res2["added"] == 0


def test_init_tool_errors_without_paper_plan(project):
    res = claims_init_tool(str(project))
    assert res["error"] == "paper_plan_not_found"


def test_extract_tool_merges_into_ledger(project, monkeypatch):
    _seed_paper_plan(project)
    claims_init_tool(str(project))

    response = _ExtractFields(claims=[
        _ExtractedClaim(
            text="Our method beats CSP by 3.2%.",
            type="comparative",
            status="needs_evidence",
        ),
    ])
    llm = _StubLLM(response)
    res = claims_extract_tool(
        str(project),
        section_name="04_experiments",
        section_text="Our method beats CSP by 3.2 percent.",
        llm=llm,
    )
    assert res.get("error") is None
    assert res["extracted_count"] == 1
    # Strong claim (comparative) without support → flagged in needs_evidence_strong.
    assert len(res["needs_evidence_strong"]) == 1
    assert res["claims_count_total"] == 3  # 2 contribution claims + 1 extracted


def test_validate_tool_round_trip(project):
    _seed_paper_plan(project)
    claims_init_tool(str(project))
    res = claims_validate_tool(str(project))
    assert res.get("error") is None
    # Two contribution claims, each novelty + needs_evidence + no support → 2 strong-claim issues.
    assert res["issues_count"] == 2
    assert res["ok"] is False
    assert all(i["kind"] == "unsupported_strong_claim" for i in res["issues"])


def test_list_tool_filter_by_status(project):
    _seed_paper_plan(project)
    claims_init_tool(str(project))
    all_claims = claims_list_tool(str(project))
    assert all_claims["claims_count"] == 2
    needs = claims_list_tool(str(project), status_filter="needs_evidence")
    assert needs["claims_count"] == 2
    supported = claims_list_tool(str(project), status_filter="supported")
    assert supported["claims_count"] == 0


# ----------------------------------------------------- persistence


def test_load_ledger_returns_empty_when_missing(project):
    paths = resolve_project(str(project))
    ledger = load_ledger(paths)
    assert ledger.claims == []


def test_save_and_reload_ledger(project):
    paths = resolve_project(str(project))
    now = datetime.now(UTC)
    ledger = ClaimsLedger(claims=[
        Claim(id="CL1", text="x", type="factual", created_at=now, updated_at=now),
    ])
    save_ledger(paths, ledger)
    reloaded = load_ledger(paths)
    assert len(reloaded.claims) == 1
    assert reloaded.last_updated_at is not None


def test_validate_ledger_against_real_project(project):
    """End-to-end: real selected.yaml + experiments dir → validate sees them."""
    _seed_paper_plan(project)
    claims_init_tool(str(project))
    paths = resolve_project(str(project))
    ledger = load_ledger(paths)
    # Promote both claims to "supported" with a real cite to demonstrate
    # the validator accepts known cite_keys / experiment_ids.
    for claim in ledger.claims:
        claim.status = "supported"
        claim.required_citations = ["arxiv_p1"]
        claim.supporting_experiments = ["exp_1"]
    save_ledger(paths, ledger)
    result = validate_ledger(load_ledger(paths), paths)
    assert result.issues == []

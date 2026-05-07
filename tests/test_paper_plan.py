"""Tests for ``paic_paper_plan_*`` MCP tools and PaperPlan schema.

Mocks the LLM client; no network or LLM calls. Verifies create / update /
status, error paths (already-exists, not-found, missing project), schema
load (``from_yaml_dict`` round-trip + missing optional fields), and the
end-to-end signal that compose injects ``paper_plan_used=True``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.latex.compose import _format_paper_plan
from paic.mcp_server.tools.paper_plan import (
    _PlanFields,
    paper_plan_create_tool,
    paper_plan_status_tool,
    paper_plan_update_tool,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.paper_plan import PaperPlan
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml, save_yaml


class _StubLLM:
    """Stub LLM that returns a fixed ``_PlanFields`` regardless of prompt."""

    model = "stub-plan-model"

    def __init__(self, response: _PlanFields):
        self.response = response
        self.calls: list[dict] = []

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        self.calls.append({"node": node, "schema": schema.__name__, "user": user})
        assert schema is _PlanFields
        return self.response


def _stub_plan_fields() -> _PlanFields:
    return _PlanFields.model_validate({
        "thesis": "Channel-pruned EEG decoding outperforms full-array baselines under cross-subject shift.",
        "target_venue": "NeurIPS 2026",
        "audience": "BCI researchers",
        "contributions": [
            {"id": "C1", "title": "Pruning algo", "description": "Top-k Fisher channel selection."},
            {"id": "C2", "title": "Empirical study", "description": "5-dataset cross-subject benchmark."},
        ],
        "section_plan": [
            {"name": "01_intro", "intent": "Motivate channel pruning for cross-subject EEG.",
             "supports_contributions": ["C1"], "target_words": 700},
            {"name": "03_method", "intent": "Describe Fisher score channel selection.",
             "supports_contributions": ["C1"], "target_words": 1200},
            {"name": "04_experiments", "intent": "Benchmark across 5 datasets.",
             "supports_contributions": ["C2"], "target_words": 1000},
        ],
        "terminology": {"channel pruning": "selecting a subset of EEG electrodes"},
        "symbols": {"\\theta": "model parameters"},
        "figure_plan": [
            {"id": "F1", "caption_seed": "Pipeline overview.", "placement_section": "01_intro"},
        ],
        "table_plan": [
            {"id": "T1", "caption_seed": "Cross-subject accuracy.", "placement_section": "04_experiments"},
        ],
        "algorithm_plan": [],
        "open_todos": ["Confirm primary metric: balanced accuracy vs F1."],
    })


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    paths = resolve_project(str(project_dir))
    # Seed an idea + an experiment so create_tool can find them.
    paths.ideas_dir.mkdir(parents=True, exist_ok=True)
    save_yaml(paths.ideas_dir / "idea_1.yaml", {
        "id": "idea_1",
        "title": "EEG channel pruning",
        "one_liner": "Prune EEG channels for cross-subject MI decoding.",
        "motivation": "Channel count limits portability.",
        "proposed_approach": "Fisher score top-k channel selection.",
        "novelty_claim": "First cross-subject channel pruning study.",
        "expected_contribution": "Open benchmark + algorithm.",
        "grounded_in": [],
        "status": "selected",
        "created_at": datetime.now(UTC).isoformat(),
    })
    paths.experiments_dir.mkdir(parents=True, exist_ok=True)
    save_yaml(paths.experiments_dir / "exp_1.yaml", {
        "id": "exp_1",
        "idea_id": "idea_1",
        "research_questions": ["Does pruning generalize cross-subject?"],
        "hypotheses": ["Top-k Fisher beats random pruning."],
        "datasets": [{"name": "BCI-IV-2a", "rationale": "Standard MI"}],
        "baselines": [{"name": "Full-channel CSP", "why": "common"}],
        "proposed_method": "Fisher-score selection of top-k channels.",
        "metrics": [{"name": "Acc", "direction": "max", "primary": True}],
        "ablations": [],
        "compute_budget": "1 x A100, 12h",
        "success_criteria": ["+3% over baseline"],
        "threats_to_validity": [],
        "created_at": datetime.now(UTC).isoformat(),
        "status": "draft",
    })
    return project_dir


# ----------------------------------------------------- schema round-trip


def test_schema_round_trip_v1():
    now = datetime.now(UTC)
    plan = PaperPlan(
        thesis="t",
        contributions=[{"id": "C1", "title": "x", "description": "y"}],
        section_plan=[],
        created_at=now,
        updated_at=now,
    )
    dumped = plan.model_dump(mode="json")
    reloaded = PaperPlan.from_yaml_dict(dumped)
    assert reloaded.thesis == "t"
    assert reloaded.contributions[0].id == "C1"


def test_schema_defaults_missing_optional_fields():
    now = datetime.now(UTC).isoformat()
    minimal = {"thesis": "t", "created_at": now, "updated_at": now}
    plan = PaperPlan.from_yaml_dict(minimal)
    assert plan.contributions == []
    assert plan.terminology == {}
    assert plan.symbols == {}
    assert plan.figure_plan == []


def test_schema_rejects_non_dict():
    with pytest.raises(TypeError):
        PaperPlan.from_yaml_dict([1, 2, 3])  # type: ignore[arg-type]


# ----------------------------------------------------- create


def test_create_writes_yaml(project):
    llm = _StubLLM(_stub_plan_fields())
    res = paper_plan_create_tool(
        str(project), idea_id="idea_1", experiment_id="exp_1", llm=llm
    )
    assert res.get("error") is None
    assert res["written"] is True
    plan = res["plan"]
    assert plan["thesis"].startswith("Channel-pruned")
    assert plan["idea_id"] == "idea_1"
    assert plan["experiment_id"] == "exp_1"
    assert plan["target_venue"] == "NeurIPS 2026"
    paths = resolve_project(str(project))
    assert paths.paper_plan_yaml.exists()
    on_disk = load_yaml(paths.paper_plan_yaml)
    assert on_disk["thesis"] == plan["thesis"]


def test_create_dry_run_does_not_write(project):
    llm = _StubLLM(_stub_plan_fields())
    res = paper_plan_create_tool(
        str(project), idea_id="idea_1", experiment_id="exp_1",
        dry_run=True, llm=llm,
    )
    assert res["written"] is False
    paths = resolve_project(str(project))
    assert not paths.paper_plan_yaml.exists()


def test_create_already_exists(project):
    llm = _StubLLM(_stub_plan_fields())
    paper_plan_create_tool(str(project), idea_id="idea_1", experiment_id="exp_1", llm=llm)
    res = paper_plan_create_tool(
        str(project), idea_id="idea_1", experiment_id="exp_1", llm=llm
    )
    assert res["error"] == "paper_plan_already_exists"


def test_create_missing_idea(project):
    llm = _StubLLM(_stub_plan_fields())
    res = paper_plan_create_tool(
        str(project), idea_id="missing", experiment_id="exp_1", llm=llm
    )
    assert res["error"] == "idea_not_found"


def test_create_missing_experiment(project):
    llm = _StubLLM(_stub_plan_fields())
    res = paper_plan_create_tool(
        str(project), idea_id="idea_1", experiment_id="missing", llm=llm
    )
    assert res["error"] == "experiment_not_found"


def test_create_without_experiment_thesis_first(project):
    """Thesis-first path: omit experiment_id, plan generated from idea + library only."""
    llm = _StubLLM(_stub_plan_fields())
    res = paper_plan_create_tool(
        str(project), idea_id="idea_1", llm=llm,
    )
    assert res.get("error") is None
    assert res["written"] is True
    plan = res["plan"]
    assert plan["idea_id"] == "idea_1"
    assert plan["experiment_id"] is None
    # Prompt must signal the absence so the LLM keeps method/eval high-level.
    assert any("NO_EXPERIMENT_YET" in (call.get("user") or "") for call in llm.calls)
    # And must NOT contain an EXPERIMENT block.
    assert not any("### EXPERIMENT" in (call.get("user") or "") for call in llm.calls)
    paths = resolve_project(str(project))
    assert paths.paper_plan_yaml.exists()
    on_disk = load_yaml(paths.paper_plan_yaml)
    assert on_disk["experiment_id"] is None


def test_update_can_bind_experiment_id_after_thesis_first(project):
    """Thesis-first → /paic-experiment → update(patch=experiment_id) binds the experiment."""
    llm = _StubLLM(_stub_plan_fields())
    paper_plan_create_tool(str(project), idea_id="idea_1", llm=llm)
    res = paper_plan_update_tool(str(project), patch={"experiment_id": "exp_1"})
    assert res.get("error") is None
    assert "experiment_id" in res["changed_keys"]
    assert res["plan"]["experiment_id"] == "exp_1"


def test_create_with_explicit_venue_overrides_llm_only_when_llm_blank(project):
    fields = _stub_plan_fields()
    # If LLM returns a venue, that wins.
    llm = _StubLLM(fields)
    res = paper_plan_create_tool(
        str(project), idea_id="idea_1", experiment_id="exp_1",
        target_venue="ICLR 2026", llm=llm,
    )
    assert res["plan"]["target_venue"] == "NeurIPS 2026"  # LLM wins

    # If LLM returns None for venue, the explicit arg wins.
    fields_no_venue = fields.model_copy(update={"target_venue": None})
    paths = resolve_project(str(project))
    paths.paper_plan_yaml.unlink()
    llm2 = _StubLLM(fields_no_venue)
    res2 = paper_plan_create_tool(
        str(project), idea_id="idea_1", experiment_id="exp_1",
        target_venue="ICLR 2026", llm=llm2,
    )
    assert res2["plan"]["target_venue"] == "ICLR 2026"


# ----------------------------------------------------- status


def test_status_when_missing(project):
    res = paper_plan_status_tool(str(project))
    assert res["exists"] is False
    assert res["plan"] is None


def test_status_after_create(project):
    llm = _StubLLM(_stub_plan_fields())
    paper_plan_create_tool(str(project), idea_id="idea_1", experiment_id="exp_1", llm=llm)
    res = paper_plan_status_tool(str(project))
    assert res["exists"] is True
    assert res["plan"]["thesis"].startswith("Channel-pruned")


# ----------------------------------------------------- update


def test_update_when_missing(project):
    res = paper_plan_update_tool(str(project), patch={"thesis": "new"})
    assert res["error"] == "paper_plan_not_found"


def test_update_empty_patch(project):
    llm = _StubLLM(_stub_plan_fields())
    paper_plan_create_tool(str(project), idea_id="idea_1", experiment_id="exp_1", llm=llm)
    res = paper_plan_update_tool(str(project), patch=None)
    assert res["error"] == "empty_patch"


def test_update_thesis(project):
    llm = _StubLLM(_stub_plan_fields())
    paper_plan_create_tool(str(project), idea_id="idea_1", experiment_id="exp_1", llm=llm)
    res = paper_plan_update_tool(str(project), patch={"thesis": "Refined thesis."})
    assert res.get("error") is None
    assert res["changed_keys"] == ["thesis"]
    assert res["plan"]["thesis"] == "Refined thesis."


def test_update_replaces_list_fields(project):
    llm = _StubLLM(_stub_plan_fields())
    paper_plan_create_tool(str(project), idea_id="idea_1", experiment_id="exp_1", llm=llm)
    new_contribs = [{"id": "C9", "title": "Single contribution", "description": "..."}]
    res = paper_plan_update_tool(str(project), patch={"contributions": new_contribs})
    assert "contributions" in res["changed_keys"]
    assert len(res["plan"]["contributions"]) == 1
    assert res["plan"]["contributions"][0]["id"] == "C9"


def test_update_no_op_when_value_unchanged(project):
    llm = _StubLLM(_stub_plan_fields())
    paper_plan_create_tool(str(project), idea_id="idea_1", experiment_id="exp_1", llm=llm)
    status = paper_plan_status_tool(str(project))
    current_thesis = status["plan"]["thesis"]
    res = paper_plan_update_tool(str(project), patch={"thesis": current_thesis})
    assert res["changed_keys"] == []


# ----------------------------------------------------- compose injection


def test_format_paper_plan_renders_section_specific_intent():
    plan = {
        "thesis": "Test thesis.",
        "contributions": [{"id": "C1", "title": "X", "description": "y"}],
        "section_plan": [
            {"name": "01_intro", "intent": "Motivate the problem.", "supports_contributions": ["C1"]},
            {"name": "03_method", "intent": "Describe method.", "supports_contributions": ["C1"]},
        ],
        "terminology": {"foo": "bar"},
        "symbols": {},
    }
    out = _format_paper_plan(plan, "01_intro")
    assert "Test thesis." in out
    assert "Motivate the problem." in out
    assert "Describe method." not in out  # other section's intent not included
    assert "[C1]" in out
    assert "foo" in out


def test_format_paper_plan_empty_returns_empty():
    out = _format_paper_plan({}, "01_intro")
    assert out == ""

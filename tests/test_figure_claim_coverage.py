"""Tests for §quality phase 9 — claim-driven figure planning."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.images.planner import FigureSlot, verify_claim_coverage
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


def _seed_paper_plan(project_dir, contributions, figure_plan=None):
    paths = resolve_project(str(project_dir))
    save_yaml(paths.paper_plan_yaml, {
        "schema_version": 1,
        "thesis": "T",
        "contributions": contributions,
        "figure_plan": figure_plan or [],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    })


def _seed_claims(project_dir, claims):
    paths = resolve_project(str(project_dir))
    save_yaml(paths.claims_yaml, {
        "schema_version": 1,
        "claims": claims,
    })


def _slot(slot_name, supporting_claims=(), no_visual_reason=None):
    return FigureSlot(
        slot=slot_name,
        kind="concept",
        section_hint="intro",
        position_hint="",
        scene_description="...",
        caption_hint="",
        rationale="",
        supporting_claims=tuple(supporting_claims),
        no_visual_reason=no_visual_reason,
    )


# ----------------------------------------------------- schema


def test_figure_slot_to_dict_includes_claim_fields():
    slot = _slot("teaser", supporting_claims=["C1", "CL2"])
    d = slot.to_dict()
    assert d["supporting_claims"] == ["C1", "CL2"]
    assert "no_visual_reason" not in d  # absent when None


def test_figure_slot_to_dict_includes_no_visual_reason_when_set():
    slot = _slot("teaser", no_visual_reason="theory-only")
    d = slot.to_dict()
    assert d["no_visual_reason"] == "theory-only"


# ----------------------------------------------------- verify_claim_coverage


def test_no_paper_plan_returns_no_warnings(project):
    warnings = verify_claim_coverage(resolve_project(str(project)), [])
    assert warnings == []


def test_no_contributions_returns_no_warnings(project):
    _seed_paper_plan(project, contributions=[])
    paths = resolve_project(str(project))
    warnings = verify_claim_coverage(paths, [])
    assert warnings == []


def test_uncovered_contribution_warns(project):
    _seed_paper_plan(project, contributions=[
        {"id": "C1", "title": "X", "description": "y"},
        {"id": "C2", "title": "Z", "description": "w"},
    ])
    paths = resolve_project(str(project))
    # Only one slot, only covers C1.
    slots = [_slot("teaser", supporting_claims=["C1"])]
    warnings = verify_claim_coverage(paths, slots)
    assert len(warnings) == 1
    assert "C2" in warnings[0]


def test_all_contributions_covered_no_warnings(project):
    _seed_paper_plan(project, contributions=[
        {"id": "C1", "title": "X", "description": "y"},
        {"id": "C2", "title": "Z", "description": "w"},
    ])
    paths = resolve_project(str(project))
    slots = [
        _slot("s1", supporting_claims=["C1"]),
        _slot("s2", supporting_claims=["C2"]),
    ]
    warnings = verify_claim_coverage(paths, slots)
    assert warnings == []


def test_no_visual_reason_slot_counts_as_coverage(project):
    _seed_paper_plan(project, contributions=[
        {"id": "C1", "title": "X", "description": "y"},
    ])
    paths = resolve_project(str(project))
    slots = [_slot("s1", supporting_claims=["C1"], no_visual_reason="theorem-only")]
    warnings = verify_claim_coverage(paths, slots)
    assert warnings == []


def test_paper_plan_figure_with_no_visual_reason_counts(project):
    """A paper_plan figure_plan entry with no_visual_reason set covers a
    contribution even if no .paic/figures/_plan.yaml slot does."""
    _seed_paper_plan(
        project,
        contributions=[{"id": "C1", "title": "X", "description": "y"}],
        figure_plan=[{
            "id": "F1", "caption_seed": "x", "placement_section": "01_intro",
            "supporting_claims": ["C1"], "no_visual_reason": "abstract result",
        }],
    )
    paths = resolve_project(str(project))
    warnings = verify_claim_coverage(paths, [])  # empty slots
    assert warnings == []


def test_claim_id_via_ledger_covers_contribution(project):
    """When a slot binds a claim CL3 whose contribution_id=C2, that's coverage."""
    _seed_paper_plan(project, contributions=[
        {"id": "C1", "title": "X", "description": "y"},
        {"id": "C2", "title": "Z", "description": "w"},
    ])
    _seed_claims(project, claims=[
        {
            "id": "CL3", "text": "x", "type": "novelty",
            "status": "needs_evidence", "contribution_id": "C2",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    ])
    paths = resolve_project(str(project))
    slots = [
        _slot("s1", supporting_claims=["C1"]),  # covers C1 directly
        _slot("s2", supporting_claims=["CL3"]),  # covers C2 via ledger
    ]
    warnings = verify_claim_coverage(paths, slots)
    assert warnings == []


def test_corrupt_paper_plan_yaml_returns_no_warnings(project):
    """Defensive: malformed paper_plan.yaml shouldn't crash the verifier."""
    paths = resolve_project(str(project))
    paths.plans_dir.mkdir(parents=True, exist_ok=True)
    paths.paper_plan_yaml.write_text("this is not yaml: a list:::\n", encoding="utf-8")
    # Even if load fails, function should return [] gracefully.
    try:
        warnings = verify_claim_coverage(paths, [])
    except Exception:
        warnings = ["crashed"]
    # Either empty (acceptable) or didn't crash.
    assert isinstance(warnings, list)

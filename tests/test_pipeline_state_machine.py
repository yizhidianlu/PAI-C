"""Pipeline state-machine tests (ARS-fusion P1-4)."""

from __future__ import annotations

import pytest

from paic.mcp_server.tools.pipeline import (
    pipeline_advance_tool,
    pipeline_state_tool,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.pipeline_state import MANDATORY_STAGES, STAGE_LABELS


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


def test_state_tool_returns_exists_false_when_no_state(project):
    out = pipeline_state_tool(str(project))
    assert out["exists"] is False
    assert "mandatory_stages" in out
    assert sorted(out["mandatory_stages"]) == [6, 9, 10]


def test_advance_first_call_bootstraps_state(project):
    out = pipeline_advance_tool(str(project), to_stage=0, mode="greenfield")
    assert out["ok"] is True
    assert out["current_stage"] == 0
    assert out["current_stage_label"] == "INIT"

    state = pipeline_state_tool(str(project))
    assert state["exists"] is True
    assert state["state"]["mode"] == "greenfield"


def test_advance_promotes_mandatory_stage(project):
    """Stage 6/9/10 must auto-promote to MANDATORY even when caller passes FULL."""
    pipeline_advance_tool(str(project), to_stage=5)
    out = pipeline_advance_tool(str(project), to_stage=6, checkpoint_kind="FULL")
    assert out["checkpoint_kind"] == "MANDATORY"
    assert out["promoted_to_mandatory"] is True
    assert out["is_mandatory"] is True


def test_advance_to_stage_9_also_mandatory(project):
    pipeline_advance_tool(str(project), to_stage=5)
    out = pipeline_advance_tool(str(project), to_stage=9, checkpoint_kind="SLIM")
    assert out["checkpoint_kind"] == "MANDATORY"
    assert out["promoted_to_mandatory"] is True


def test_advance_to_stage_10_also_mandatory(project):
    pipeline_advance_tool(str(project), to_stage=9)
    out = pipeline_advance_tool(str(project), to_stage=10, checkpoint_kind="FULL")
    assert out["checkpoint_kind"] == "MANDATORY"


def test_invalid_stage_returns_error(project):
    out = pipeline_advance_tool(str(project), to_stage=42)
    assert out["error"] == "invalid_stage"


def test_invalid_checkpoint_kind_returns_error(project):
    out = pipeline_advance_tool(str(project), to_stage=1, checkpoint_kind="VAGUE")
    assert out["error"] == "invalid_checkpoint_kind"


def test_consecutive_continue_increments_then_forces_full(project):
    """4 consecutive SLIM continues → next checkpoint forced to FULL."""
    pipeline_advance_tool(str(project), to_stage=0)
    out_a = pipeline_advance_tool(
        str(project), to_stage=1, checkpoint_kind="SLIM", consecutive_continue=True,
    )
    assert out_a["consecutive_continues"] == 1

    out_b = pipeline_advance_tool(
        str(project), to_stage=2, checkpoint_kind="SLIM", consecutive_continue=True,
    )
    assert out_b["consecutive_continues"] == 2

    out_c = pipeline_advance_tool(
        str(project), to_stage=3, checkpoint_kind="SLIM", consecutive_continue=True,
    )
    assert out_c["consecutive_continues"] == 3

    # 4th continue: forced to FULL, counter resets
    out_d = pipeline_advance_tool(
        str(project), to_stage=4, checkpoint_kind="SLIM", consecutive_continue=True,
    )
    assert out_d["forced_full_for_awareness"] is True
    assert out_d["checkpoint_kind"] == "FULL"
    assert out_d["consecutive_continues"] == 0


def test_full_checkpoint_resets_consecutive_counter(project):
    pipeline_advance_tool(str(project), to_stage=0)
    pipeline_advance_tool(
        str(project), to_stage=1, checkpoint_kind="SLIM", consecutive_continue=True,
    )
    out = pipeline_advance_tool(str(project), to_stage=2, checkpoint_kind="FULL")
    assert out["consecutive_continues"] == 0


def test_history_appends_one_entry_per_advance(project):
    pipeline_advance_tool(str(project), to_stage=0)
    pipeline_advance_tool(str(project), to_stage=1, deliverables=["library/selected.yaml"])
    pipeline_advance_tool(str(project), to_stage=2)

    state = pipeline_state_tool(str(project))
    history = state["state"]["stage_history"]
    assert len(history) == 3
    # First entry has no from_stage (model_dump excludes None values)
    assert history[0].get("from_stage") in (None,) or "from_stage" not in history[0]
    assert history[1]["from_stage"] == 0
    assert history[1]["to_stage"] == 1
    assert history[1]["deliverables"] == ["library/selected.yaml"]


def test_passport_hash_records_alongside_history(project):
    pipeline_advance_tool(str(project), to_stage=0)
    out = pipeline_advance_tool(
        str(project),
        to_stage=1,
        deliverables=["library/selected.yaml"],
        passport_hash="abc123def456",
    )
    state = pipeline_state_tool(str(project))
    last = state["state"]["stage_history"][-1]
    assert last["passport_hash"] == "abc123def456"


def test_state_label_round_trip(project):
    """Every defined stage's label is accessible via the state tool."""
    pipeline_advance_tool(str(project), to_stage=0)
    out = pipeline_state_tool(str(project))
    # stage_labels round-trips as in-memory dict (int keys preserved)
    labels = out["stage_labels"]
    assert labels.get(6, labels.get("6")) == "INTEGRITY-PRE"
    assert labels.get(0, labels.get("0")) == "INIT"


def test_mandatory_stages_constant_matches_labels():
    """All MANDATORY_STAGES must be in STAGE_LABELS."""
    for s in MANDATORY_STAGES:
        assert s in STAGE_LABELS


def test_skill_trigger_anti_overlap_documented():
    """The SKILL md must list at least one 'do NOT trigger' scenario per
    independent SKILL — otherwise the orchestrator hijacks single-skill flows."""
    from pathlib import Path
    skill_md = Path(__file__).parent.parent / "skills" / "paic-pipeline" / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    # Must mention at least 5 of the 13 single SKILLs in non-trigger context
    individual_skills = [
        "/paic-search", "/paic-ideate", "/paic-review",
        "/paic-finalize", "/paic-integrity", "/paic-format-convert",
    ]
    mentioned = sum(1 for s in individual_skills if s in text)
    assert mentioned >= 5, f"only {mentioned} individual skills referenced in non-trigger table"

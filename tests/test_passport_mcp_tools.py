"""Tests for the paic_passport_* MCP tools (ARS-fusion P1-2)."""

from __future__ import annotations

import pytest
import yaml

from paic.mcp_server.tools.passport import (
    passport_emit_tool,
    passport_list_tool,
    passport_resume_tool,
)
from paic.mcp_server.tools.workspace import workspace_init


@pytest.fixture
def project_with_passport(tmp_path, monkeypatch):
    """Project with passport.enable_reset_boundary=true."""
    home = tmp_path / ".paic_home"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "providers": {"anthropic": {"mode": "api_key", "api_key_env": "X"}},
            "passport": {"enable_reset_boundary": True, "lock_timeout_sec": 5},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("PAIC_HOME", str(home))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


@pytest.fixture
def project_without_passport(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home_default"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p2"
    workspace_init(project_dir)
    return project_dir


def test_emit_returns_passport_disabled_when_not_opt_in(project_without_passport):
    out = passport_emit_tool(str(project_without_passport), stage=1)
    assert out["error"] == "passport_disabled"


def test_resume_returns_passport_disabled_when_not_opt_in(project_without_passport):
    out = passport_resume_tool(str(project_without_passport), hash="deadbeef0000")
    assert out["error"] == "passport_disabled"


def test_emit_writes_boundary_and_returns_resume_command(project_with_passport):
    out = passport_emit_tool(
        str(project_with_passport),
        stage=2,
        deliverables=["draft/main.tex", "claims.yaml"],
        next_stage=3,
    )
    assert out["ok"] is True
    assert "resume_command" in out
    h = out["entry"]["hash"]
    assert len(h) == 12
    assert out["resume_command"] == f"resume_from_passport={h}"


def test_emit_then_resume_round_trip(project_with_passport):
    emit = passport_emit_tool(str(project_with_passport), stage=2, next_stage=3)
    h = emit["entry"]["hash"]
    out = passport_resume_tool(str(project_with_passport), hash=h)
    assert out["ok"] is True
    assert out["next_stage"] == 3
    assert out["used_pending_decision"] is False


def test_resume_double_consumed_returns_error(project_with_passport):
    emit = passport_emit_tool(str(project_with_passport), stage=2, next_stage=3)
    h = emit["entry"]["hash"]
    passport_resume_tool(str(project_with_passport), hash=h)
    out2 = passport_resume_tool(str(project_with_passport), hash=h)
    assert out2["error"] == "double_resume"


def test_resume_pending_decision_required(project_with_passport):
    emit = passport_emit_tool(
        str(project_with_passport),
        stage=3,
        pending_decision={
            "question": "Accept review verdict?",
            "options": [
                {"value": "accept", "next_stage": 5},
                {"value": "revise", "next_stage": 4},
            ],
        },
    )
    h = emit["entry"]["hash"]
    out = passport_resume_tool(str(project_with_passport), hash=h)
    assert out["error"] == "pending_decision_required"


def test_resume_with_branch(project_with_passport):
    emit = passport_emit_tool(
        str(project_with_passport),
        stage=3,
        pending_decision={
            "question": "?",
            "options": [
                {"value": "accept", "next_stage": 5},
                {"value": "revise", "next_stage": 4, "next_mode": "tight"},
            ],
        },
    )
    out = passport_resume_tool(
        str(project_with_passport),
        hash=emit["entry"]["hash"],
        chosen_branch="revise",
    )
    assert out["ok"] is True
    assert out["next_stage"] == 4
    assert out["next_mode"] == "tight"


def test_list_reports_awaiting_resume(project_with_passport):
    e1 = passport_emit_tool(str(project_with_passport), stage=1)
    e2 = passport_emit_tool(str(project_with_passport), stage=2)

    listed = passport_list_tool(str(project_with_passport))
    assert listed["enabled"] is True
    assert listed["boundary_count"] == 2
    assert listed["resume_count"] == 0
    assert len(listed["awaiting_resume"]) == 2

    # Consume the first boundary
    passport_resume_tool(str(project_with_passport), hash=e1["entry"]["hash"])
    listed2 = passport_list_tool(str(project_with_passport))
    assert listed2["resume_count"] == 1
    awaiting = [b["hash"] for b in listed2["awaiting_resume"]]
    assert e2["entry"]["hash"] in awaiting
    assert e1["entry"]["hash"] not in awaiting


def test_list_reports_disabled_when_not_opt_in(project_without_passport):
    listed = passport_list_tool(str(project_without_passport))
    assert listed["enabled"] is False
    # Even without enable, reading is allowed (returns empty entries)
    assert listed["entries"] == []


def test_resume_unknown_hash(project_with_passport):
    out = passport_resume_tool(str(project_with_passport), hash="aaaaaaaaaaaa")
    assert out["error"] == "passport_no_ledger"  # ledger not yet created
    # After emitting, unknown hash should return hash_not_found
    passport_emit_tool(str(project_with_passport), stage=1)
    out2 = passport_resume_tool(str(project_with_passport), hash="aaaaaaaaaaaa")
    assert out2["error"] == "hash_not_found"

"""Tests for the 7-mode AI failure checklist prompt builder + judgment applier."""

from __future__ import annotations

import pytest

from paic.integrity.ai_failure_modes import apply_judgments, build_ai_failure_prompts
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return resolve_project(str(project_dir))


def test_build_prompts_skips_m1_by_default(project):
    prompts = build_ai_failure_prompts(project)
    modes = [p.mode for p in prompts]
    assert 1 not in modes
    assert sorted(modes) == [2, 3, 4, 5, 6, 7]


def test_build_prompts_includes_m1_when_requested(project):
    prompts = build_ai_failure_prompts(project, skip_mode_1=False)
    assert 1 in [p.mode for p in prompts]


def test_m3_skipped_when_paper_kind_theoretical(project):
    save_yaml(project.paper_plan_yaml, {
        "thesis": "x",
        "paper_kind": "theoretical",
        "contributions": [],
    })
    prompts = build_ai_failure_prompts(project)
    m3 = next(p for p in prompts if p.mode == 3)
    assert m3.skipped_reason is not None
    assert "theoretical" in m3.skipped_reason


def test_m4_skipped_when_no_experiments(project):
    prompts = build_ai_failure_prompts(project)
    m4 = next(p for p in prompts if p.mode == 4)
    assert m4.skipped_reason is not None


def test_m6_runs_with_only_paper_plan(project):
    save_yaml(project.paper_plan_yaml, {
        "thesis": "Some thesis",
        "contributions": [{"id": "C1", "title": "X", "description": "Y"}],
    })
    prompts = build_ai_failure_prompts(project)
    m6 = next(p for p in prompts if p.mode == 6)
    assert m6.skipped_reason is None
    assert m6.inputs["thesis"] == "Some thesis"


def test_apply_judgments_promotes_mandatory_to_blocker():
    judgments = [
        {
            "mode": 3,
            "status": "SUSPECTED",
            "reasoning": "no recorded results",
            "evidence": ["claim:C2"],
        },
        {
            "mode": 7,
            "status": "SUSPECTED",
            "reasoning": "anomalous result",
        },
    ]
    issues = apply_judgments(judgments, mandatory_modes=[1, 3, 5, 6])
    by_kind = {i.kind: i for i in issues}
    assert by_kind["AI_FAIL_M3"].severity == "blocker"
    # M7 advisory → minor (apply_judgments alone gives minor; runner promotes mandatory)
    assert by_kind["AI_FAIL_M7"].severity == "minor"


def test_apply_judgments_drops_verified_and_insufficient():
    judgments = [
        {"mode": 5, "status": "VERIFIED"},
        {"mode": 6, "status": "INSUFFICIENT_EVIDENCE"},
    ]
    issues = apply_judgments(judgments)
    assert issues == []


def test_apply_judgments_ignores_invalid_status():
    judgments = [{"mode": 1, "status": "MAYBE"}]
    issues = apply_judgments(judgments)
    assert issues == []

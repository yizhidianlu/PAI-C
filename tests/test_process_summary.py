"""Tests for /paic-process-summary (ARS-fusion P3-2)."""

from __future__ import annotations

import pytest

from paic.mcp_server.tools.passport import passport_emit_tool
from paic.mcp_server.tools.pipeline import pipeline_advance_tool
from paic.mcp_server.tools.process_summary import process_summary_tool
from paic.mcp_server.tools.workspace import workspace_init


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


@pytest.fixture
def project_with_passport(tmp_path, monkeypatch):
    """Project with passport.enable_reset_boundary=true so emit works."""
    import yaml
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


def test_process_summary_works_with_no_state(project):
    """Even an empty project produces a useful report (placeholders)."""
    out = process_summary_tool(str(project))
    assert out["ok"] is True
    assert "Paper Creation Process Record" in out["content"]
    # All four sections present (with placeholders for missing data)
    assert "1. Pipeline timeline" in out["content"]
    assert "2. Material Passport" in out["content"]
    assert "3. LangGraph runs" in out["content"]
    # Integrity section not added when integrity_report.yaml missing
    # (and include_integrity defaults true; missing source → not present)
    assert out["sections_present"] == []


def test_process_summary_reflects_pipeline_history(project):
    pipeline_advance_tool(str(project), to_stage=0, mode="greenfield")
    pipeline_advance_tool(str(project), to_stage=1, deliverables=["selected.yaml"])
    pipeline_advance_tool(str(project), to_stage=5, verdict="continue")

    out = process_summary_tool(str(project))
    assert "pipeline.yaml" in out["sections_present"]
    content = out["content"]
    assert "INIT" in content
    assert "SEARCH+INGEST" in content
    assert "DRAFT" in content
    assert "selected.yaml" in content or "Deliverables" in content


def test_process_summary_reflects_passport_entries(project_with_passport):
    e1 = passport_emit_tool(str(project_with_passport), stage=2)
    e2 = passport_emit_tool(str(project_with_passport), stage=5)

    out = process_summary_tool(str(project_with_passport))
    assert "passport.yaml" in out["sections_present"]
    content = out["content"]
    # Both hashes appear
    assert e1["entry"]["hash"] in content
    assert e2["entry"]["hash"] in content
    # Both flagged as awaiting (no resume yet)
    assert "Awaiting resume" in content


def test_process_summary_writes_to_disk(project, tmp_path):
    pipeline_advance_tool(str(project), to_stage=0)
    out = process_summary_tool(
        str(project),
        write_to="drafts/process_summary.md",
    )
    assert "written_to" in out
    written = out["written_to"]
    from pathlib import Path
    assert Path(written).is_file()
    text = Path(written).read_text(encoding="utf-8")
    assert "Paper Creation Process Record" in text


def test_process_summary_handles_missing_project(tmp_path):
    out = process_summary_tool(str(tmp_path / "no_project"))
    assert out["error"] == "project_not_initialized"


def test_process_summary_includes_integrity_report(project):
    """When integrity_report.yaml exists it should be summarized."""
    from paic.workspace.paths import resolve_project
    from paic.workspace.store import save_yaml
    paths = resolve_project(str(project))
    save_yaml(paths.state_dir / "integrity_report.yaml", {
        "passed": False,
        "mode": "pre_review",
        "issue_count": 3,
        "issues": [
            {"kind": "TF", "severity": "blocker", "target": "fake_2024", "detail": "...", "actionable_fix": "..."},
            {"kind": "AI_FAIL_M3", "severity": "blocker", "target": None, "detail": "...", "actionable_fix": "..."},
            {"kind": "SH", "severity": "minor", "target": "real_2020", "detail": "...", "actionable_fix": "..."},
        ],
        "overrides": [],
        "overrides_rejected": [],
        "notes": ["AI_FAIL_M2 skipped — no methodological claims"],
    })

    out = process_summary_tool(str(project))
    assert "state/integrity_report.yaml" in out["sections_present"]
    content = out["content"]
    assert "4. Latest integrity report" in content
    assert "TF" in content
    assert "AI_FAIL_M3" in content
    assert "blocker: 2" in content
    assert "AI_FAIL_M2 skipped" in content


def test_process_summary_can_skip_integrity_section(project):
    from paic.workspace.paths import resolve_project
    from paic.workspace.store import save_yaml
    paths = resolve_project(str(project))
    save_yaml(paths.state_dir / "integrity_report.yaml", {
        "passed": True, "mode": "pre_review", "issue_count": 0,
        "issues": [], "overrides": [], "overrides_rejected": [], "notes": [],
    })

    out = process_summary_tool(str(project), include_integrity=False)
    # When include_integrity=False the integrity section is not rendered
    # AND its source is not added to sections_present
    assert "state/integrity_report.yaml" not in out["sections_present"]
    assert "4. Latest integrity report" not in out["content"]


def test_process_summary_footer_lists_sources_present(project):
    pipeline_advance_tool(str(project), to_stage=0)
    out = process_summary_tool(str(project))
    assert "State files read" in out["content"]
    assert "pipeline.yaml" in out["content"]

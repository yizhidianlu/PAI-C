"""End-to-end runner tests: structural + S2 batch + judgment promotion + overrides."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from paic.integrity.runner import apply_persisted_results, run_integrity_check
from paic.integrity.types import IntegrityIssue
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


@dataclass
class _StubResult:
    papers: list


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    paths = resolve_project(str(project_dir))
    save_yaml(paths.selected_yaml, {
        "papers": [
            {
                "arxiv_id": "2401.12345",
                "title": "Real Paper",
                "authors": ["Alice"],
                "year": 2024,
            },
        ],
    })
    return paths


def test_run_integrity_check_returns_pending_in_host_mode(project):
    """When no inline_judge_fn is provided, AI prompts surface as pending OR notes.

    The fixture has no paper_plan / claims / experiments, so all 6 mode-2..7
    prompts skip and surface as ``notes`` rather than ``pending_ai_judge``.
    pending_websearch is populated because the fixture has 1 library entry
    that the stubbed S2 doesn't recognize.
    """
    result = run_integrity_check(
        project,
        s2_search_fn=lambda q, limit=5: _StubResult(papers=[]),
    )
    assert result.s2_failed_count == 1
    assert len(result.pending_websearch) == 1
    # Either pending judge prompts OR skipped notes — both prove the AI
    # failure mode prompt builder ran.
    assert result.pending_ai_judge or result.notes
    assert result.has_pending_work is True


def test_run_integrity_check_includes_pending_ai_judge_when_inputs_present(
    project, tmp_path,
):
    """When paper plan / claims / experiments exist, mode prompts run."""
    save_yaml(project.paper_plan_yaml, {
        "thesis": "T",
        "contributions": [{"id": "C1", "title": "X", "description": "Y"}],
    })
    save_yaml(project.claims_yaml, {
        "claims": [
            {"id": "C1", "text": "We achieve 95% accuracy.", "type": "numeric"},
        ],
    })
    result = run_integrity_check(
        project,
        s2_search_fn=lambda q, limit=5: _StubResult(papers=[]),
    )
    assert result.pending_ai_judge, "expected at least one mode prompt to be runnable"


def test_run_integrity_check_with_inline_judge(project):
    """Inline mode → AI prompts run via injected fn, no pending_ai_judge."""
    def stub_judge(prompt):
        return {
            "mode": prompt.mode,
            "status": "VERIFIED",
            "reasoning": "ok",
        }

    result = run_integrity_check(
        project,
        inline_judge_fn=stub_judge,
        s2_search_fn=lambda q, limit=5: _StubResult(papers=[]),
    )
    assert result.pending_ai_judge == []  # all judged inline
    assert all(not i.kind.startswith("AI_FAIL") for i in result.issues)  # all VERIFIED → no issues


def test_apply_persisted_results_writes_report(project):
    result = apply_persisted_results(
        project,
        web_search_results=[{
            "cite_key": "arxiv_2401_12345",
            "verdict": "VERIFIED",
            "evidence_url": ["https://x"],
        }],
        ai_judge_results=[{"mode": 5, "status": "VERIFIED"}],
        write_report=True,
    )
    assert result.passed is True
    report_path = project.state_dir / "integrity_report.yaml"
    assert report_path.is_file()


def test_apply_persisted_results_promotes_m3_to_blocker(project):
    result = apply_persisted_results(
        project,
        ai_judge_results=[
            {"mode": 3, "status": "SUSPECTED", "reasoning": "no backing"},
        ],
        mandatory_modes=[1, 3, 5, 6],
    )
    assert result.passed is False
    assert any(i.kind == "AI_FAIL_M3" and i.severity == "blocker" for i in result.issues)


def test_apply_persisted_results_overrides_blocker_rejected(project):
    """Blocker severity cannot be overridden — same semantics as quality_gate."""
    result = apply_persisted_results(
        project,
        web_search_results=[{
            "cite_key": "arxiv_2401_12345",
            "verdict": "NOT_FOUND",
            "evidence_url": [],
        }],
        overrides=["TF"],
    )
    assert result.passed is False
    # TF blocker is kept; rejection echoed
    assert any(i.kind == "TF" for i in result.issues)
    assert any(r["kind"] == "TF" for r in result.overrides_rejected)


def test_apply_persisted_results_overrides_minor_dropped(project):
    structural = [IntegrityIssue(
        kind="SH",
        severity="minor",
        target="arxiv_2401_12345",
        detail="year drift",
        actionable_fix="update yaml",
    )]
    result = apply_persisted_results(
        project,
        structural_issues=structural,
        overrides=["SH"],
    )
    assert result.passed is True
    assert all(i.kind != "SH" for i in result.issues)
    assert result.overrides_rejected == []


def test_run_integrity_check_caches_s2_results(project):
    call_count = {"n": 0}

    def s2_stub(q, limit=5):
        call_count["n"] += 1
        return _StubResult(papers=[])

    run_integrity_check(project, s2_search_fn=s2_stub)
    initial = call_count["n"]

    # Second run hits cache for that pending entry → no extra S2 call.
    run_integrity_check(project, s2_search_fn=s2_stub)
    # Pending entries are cached too (verdict=PENDING), so no fresh call expected.
    assert call_count["n"] == initial


def test_from_scratch_invalidates_cache(project):
    call_count = {"n": 0}

    def s2_stub(q, limit=5):
        call_count["n"] += 1
        return _StubResult(papers=[])

    run_integrity_check(project, s2_search_fn=s2_stub)
    initial = call_count["n"]

    run_integrity_check(project, s2_search_fn=s2_stub, from_scratch=True)
    assert call_count["n"] > initial

"""Tests for §quality phase 8 — revision queue."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.library.revisions import (
    _ExtractedTask,
    _ExtractFields,
    extract_tasks_from_review,
    find_task,
    list_tasks,
    mark_in_progress,
    resolve_task,
    save_tasks,
    save_task,
)
from paic.mcp_server.tools.revisions import (
    revision_apply_tool,
    revision_extract_tool,
    revision_list_tool,
    revision_resolve_tool,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.revision import RevisionTask
from paic.workspace.paths import resolve_project


class _ExtractStubLLM:
    model = "stub-extract"

    def __init__(self, response: _ExtractFields):
        self.response = response

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        assert schema is _ExtractFields
        return self.response


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    p = tmp_path / "p"
    workspace_init(p)
    return p


def _seed_tasks(paths, count=3, **overrides):
    """Create N tasks with mixed severity for filter tests."""
    now = datetime.now(UTC)
    severities = ["minor", "major", "blocker"]
    tasks = []
    for i in range(count):
        defaults = dict(
            id=f"task_{i}",
            severity=severities[i % len(severities)],
            target_kind="section",
            target_ref="01_intro",
            summary=f"Summary {i}",
            created_at=now,
            updated_at=now,
        )
        defaults.update(overrides)
        tasks.append(RevisionTask(**defaults))
    save_tasks(paths, tasks)
    return tasks


# ----------------------------------------------------- schema


def test_revision_task_round_trip():
    now = datetime.now(UTC)
    t = RevisionTask(
        id="t1", severity="major", target_kind="claim",
        target_ref="CL3", summary="x",
        created_at=now, updated_at=now,
    )
    reloaded = RevisionTask.from_yaml_dict(t.model_dump(mode="json"))
    assert reloaded.id == "t1"
    assert reloaded.severity == "major"


def test_revision_task_default_status_is_open():
    now = datetime.now(UTC)
    t = RevisionTask(
        id="t1", severity="minor", target_kind="section",
        summary="x", created_at=now, updated_at=now,
    )
    assert t.status == "open"


def test_revision_task_invalid_severity_rejected():
    now = datetime.now(UTC)
    with pytest.raises(Exception):
        RevisionTask(
            id="t1", severity="emergency",  # type: ignore[arg-type]
            target_kind="section", summary="x",
            created_at=now, updated_at=now,
        )


# ----------------------------------------------------- extract


def test_extract_returns_empty_for_empty_payload(project):
    paths = resolve_project(str(project))
    llm = _ExtractStubLLM(_ExtractFields(tasks=[]))
    tasks = extract_tasks_from_review({}, llm=llm)
    assert tasks == []


def test_extract_calls_llm_and_assigns_round(project):
    paths = resolve_project(str(project))
    response = _ExtractFields(tasks=[
        _ExtractedTask(severity="major", target_kind="section",
                       target_ref="01_intro", summary="x"),
        _ExtractedTask(severity="minor", target_kind="claim",
                       target_ref="CL2", summary="y"),
    ])
    llm = _ExtractStubLLM(response)
    tasks = extract_tasks_from_review(
        {"moderator": "Synthesis text", "critiques": {"methodology": "Comment"}},
        llm=llm, round_num=2,
    )
    assert len(tasks) == 2
    assert all(t.source_review_round == 2 for t in tasks)
    assert all(t.status == "open" for t in tasks)
    # Each task gets a unique id.
    assert len({t.id for t in tasks}) == 2


# ----------------------------------------------------- persistence


def test_save_and_list(project):
    paths = resolve_project(str(project))
    _seed_tasks(paths, count=3)
    tasks = list_tasks(paths)
    assert len(tasks) == 3


def test_list_filters_by_status(project):
    paths = resolve_project(str(project))
    tasks = _seed_tasks(paths, count=2)
    # Resolve one of them.
    resolve_task(paths, tasks[0].id, "fixed")
    open_tasks = list_tasks(paths, status="open")
    assert len(open_tasks) == 1
    resolved = list_tasks(paths, status="resolved")
    assert len(resolved) == 1
    assert resolved[0].resolution_summary == "fixed"


def test_list_filters_by_severity(project):
    paths = resolve_project(str(project))
    _seed_tasks(paths, count=3)  # severities cycle minor / major / blocker
    blockers = list_tasks(paths, severity="blocker")
    assert len(blockers) == 1
    assert blockers[0].severity == "blocker"


def test_list_filters_by_round(project):
    paths = resolve_project(str(project))
    now = datetime.now(UTC)
    save_tasks(paths, [
        RevisionTask(id="r1", severity="major", target_kind="global",
                     summary="r1", source_review_round=1,
                     created_at=now, updated_at=now),
        RevisionTask(id="r2", severity="major", target_kind="global",
                     summary="r2", source_review_round=2,
                     created_at=now, updated_at=now),
    ])
    round1 = list_tasks(paths, round_num=1)
    assert len(round1) == 1
    assert round1[0].id == "r1"


def test_find_task(project):
    paths = resolve_project(str(project))
    tasks = _seed_tasks(paths, count=2)
    found = find_task(paths, tasks[0].id)
    assert found is not None
    assert found.id == tasks[0].id


def test_find_task_not_present(project):
    paths = resolve_project(str(project))
    assert find_task(paths, "missing_id") is None


# ----------------------------------------------------- apply / resolve


def test_mark_in_progress(project):
    paths = resolve_project(str(project))
    tasks = _seed_tasks(paths, count=1)
    out = mark_in_progress(paths, tasks[0].id)
    assert out is not None
    assert out.status == "in_progress"


def test_resolve_sets_summary_and_timestamp(project):
    paths = resolve_project(str(project))
    tasks = _seed_tasks(paths, count=1)
    out = resolve_task(paths, tasks[0].id, "fixed via X")
    assert out is not None
    assert out.status == "resolved"
    assert out.resolution_summary == "fixed via X"
    assert out.resolved_at is not None


# ----------------------------------------------------- tools


def test_extract_tool_persists(project):
    response = _ExtractFields(tasks=[
        _ExtractedTask(severity="major", target_kind="claim",
                       target_ref="CL1", summary="weak claim"),
    ])
    llm = _ExtractStubLLM(response)
    res = revision_extract_tool(
        str(project),
        {"moderator": "synth"},
        round_num=1,
        llm=llm,
    )
    assert res.get("error") is None
    assert res["extracted_count"] == 1
    paths = resolve_project(str(project))
    assert len(list_tasks(paths)) == 1


def test_list_tool_sort_order(project):
    paths = resolve_project(str(project))
    _seed_tasks(paths, count=3)
    res = revision_list_tool(str(project))
    # Sorted by severity (blocker > major > minor).
    assert res["count"] == 3
    severities = [t["severity"] for t in res["tasks"]]
    assert severities == ["blocker", "major", "minor"]


def test_list_tool_invalid_filter(project):
    res = revision_list_tool(str(project), status="not_a_status")
    assert res["error"] == "invalid_status"


def test_apply_tool_round_trip(project):
    paths = resolve_project(str(project))
    tasks = _seed_tasks(paths, count=1)
    res = revision_apply_tool(str(project), tasks[0].id)
    assert res.get("error") is None
    assert res["status"] == "in_progress"


def test_apply_tool_unknown(project):
    res = revision_apply_tool(str(project), "missing")
    assert res["error"] == "task_not_found"


def test_resolve_tool_requires_summary(project):
    paths = resolve_project(str(project))
    tasks = _seed_tasks(paths, count=1)
    res = revision_resolve_tool(str(project), tasks[0].id, "")
    assert res["error"] == "resolution_summary_required"


def test_resolve_tool_persists(project):
    paths = resolve_project(str(project))
    tasks = _seed_tasks(paths, count=1)
    res = revision_resolve_tool(str(project), tasks[0].id, "swapped baseline reference")
    assert res.get("error") is None
    assert res["status"] == "resolved"
    found = find_task(paths, tasks[0].id)
    assert found.status == "resolved"
    assert found.resolution_summary == "swapped baseline reference"

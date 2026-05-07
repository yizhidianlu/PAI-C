"""``paic_revision_*`` MCP tools — extract / list / apply / resolve."""

from __future__ import annotations

from typing import Any

from paic.library.revisions import (
    extract_tasks_from_review,
    list_tasks,
    mark_in_progress,
    resolve_task,
    save_tasks,
)
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.workspace.paths import resolve_project


def revision_extract_tool(
    project_dir: str,
    review_payload: dict[str, Any],
    round_num: int | None = None,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Extract RevisionTasks from a review payload and persist them.

    ``review_payload`` is the structured output from ``/paic-review`` —
    typically ``{moderator: ..., critiques: {...}}`` or similar shapes.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    client = llm or get_default_client()
    try:
        tasks = extract_tasks_from_review(
            review_payload, llm=client, round_num=round_num,
        )
    except LLMUnavailable as exc:
        return {"error": "llm_unavailable", "detail": str(exc)}

    paths_written = save_tasks(paths, tasks)
    return {
        "extracted_count": len(tasks),
        "round": round_num,
        "tasks": [t.model_dump(mode="json") for t in tasks],
        "paths": paths_written,
    }


def revision_list_tool(
    project_dir: str,
    status: str | None = None,
    severity: str | None = None,
    round_num: int | None = None,
) -> dict[str, Any]:
    """List RevisionTasks. All filters are optional."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    valid_statuses = {"open", "in_progress", "resolved", "wontfix", None}
    valid_severities = {"info", "minor", "major", "blocker", None}
    if status not in valid_statuses:
        return {"error": "invalid_status", "got": status, "valid": list(valid_statuses - {None})}
    if severity not in valid_severities:
        return {"error": "invalid_severity", "got": severity, "valid": list(valid_severities - {None})}

    tasks = list_tasks(
        paths,
        status=status,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        round_num=round_num,
    )
    # Sort by (severity bucket desc, round_num desc, created_at).
    severity_rank = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
    tasks.sort(
        key=lambda t: (
            severity_rank.get(t.severity, 99),
            -(t.source_review_round or 0),
            t.created_at,
        )
    )
    return {
        "count": len(tasks),
        "tasks": [t.model_dump(mode="json") for t in tasks],
    }


def revision_apply_tool(project_dir: str, task_id: str) -> dict[str, Any]:
    """Mark a task ``in_progress``. The actual edit is up to the user / SKILL."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    task = mark_in_progress(paths, task_id)
    if task is None:
        return {"error": "task_not_found", "task_id": task_id}
    return {
        "task": task.model_dump(mode="json"),
        "status": "in_progress",
    }


def revision_resolve_tool(
    project_dir: str,
    task_id: str,
    resolution_summary: str,
) -> dict[str, Any]:
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    if not resolution_summary or not resolution_summary.strip():
        return {
            "error": "resolution_summary_required",
            "hint": "Provide a one-line summary of what changed.",
        }
    task = resolve_task(paths, task_id, resolution_summary.strip())
    if task is None:
        return {"error": "task_not_found", "task_id": task_id}
    return {
        "task": task.model_dump(mode="json"),
        "status": "resolved",
    }

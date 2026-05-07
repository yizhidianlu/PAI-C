"""``paic_revision_*`` MCP tools — extract / list / apply / resolve."""

from __future__ import annotations

from typing import Any

from paic.library.revisions import (
    _ExtractFields,
    _dump_text,
    extract_tasks_from_review,
    list_tasks,
    mark_in_progress,
    resolve_task,
    save_tasks,
)
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.llm.host import build_host_directive
from paic.llm.router import LLMRouter
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

    if not isinstance(review_payload, dict) or not review_payload:
        return {
            "extracted_count": 0,
            "round": round_num,
            "tasks": [],
            "paths": [],
        }

    from paic.config import load_config
    cfg = load_config()
    router = LLMRouter(cfg)
    if router.is_host_orchestrated("revision_extract"):
        parts: list[str] = []
        if round_num is not None:
            parts.append(f"### REVIEW ROUND: {round_num}")
        moderator = review_payload.get("moderator") or review_payload.get("synthesis")
        if moderator:
            parts.append("### MODERATOR SYNTHESIS")
            parts.append(_dump_text(moderator))
        critiques = review_payload.get("critiques") or review_payload.get("personas")
        if critiques:
            parts.append("### PER-PERSONA CRITIQUES")
            if isinstance(critiques, dict):
                for persona, text in critiques.items():
                    parts.append(f"--- {persona} ---")
                    parts.append(_dump_text(text))
            elif isinstance(critiques, list):
                for entry in critiques:
                    if isinstance(entry, dict):
                        name = entry.get("persona") or entry.get("name") or "?"
                        parts.append(f"--- {name} ---")
                        parts.append(_dump_text(entry))
                    else:
                        parts.append(_dump_text(entry))
        if not parts:
            parts.append(_dump_text(review_payload))
        return build_host_directive(
            node="revision_extract",
            instructions=(
                "Convert the review critiques in `user_prompt` into "
                "RevisionTasks matching `schema_hint`. Then call "
                "mcp__paic__paic_revision_extract_persist with `extracted=<your JSON>` "
                "and the same `round_num` from `metadata`."
            ),
            user_prompt="\n\n".join(parts),
            schema_hint=_ExtractFields.model_json_schema(),
            next_tool="mcp__paic__paic_revision_extract_persist",
            metadata={"round_num": round_num},
        ).to_dict()

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


def revision_extract_persist_tool(
    project_dir: str,
    extracted: dict[str, Any],
    round_num: int | None = None,
) -> dict[str, Any]:
    """Persist host-generated revision tasks (LLM-free)."""
    from datetime import UTC, datetime
    from ulid import ULID
    from pydantic import ValidationError
    from paic.schemas.revision import RevisionTask

    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    try:
        fields = _ExtractFields.model_validate(extracted)
    except ValidationError as exc:
        return {"error": "schema_validation_failed", "detail": exc.errors()}

    now = datetime.now(UTC)
    tasks: list[RevisionTask] = []
    for raw in fields.tasks:
        tasks.append(RevisionTask(
            id=str(ULID()),
            severity=raw.severity,
            status="open",
            target_kind=raw.target_kind,
            target_ref=raw.target_ref,
            summary=raw.summary,
            detail=raw.detail,
            patch_hint=raw.patch_hint,
            source_persona=raw.source_persona,
            source_review_round=round_num,
            created_at=now,
            updated_at=now,
        ))
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

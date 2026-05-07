"""Revision queue helpers (§quality phase 8).

Three-stage flow:

1. ``extract_tasks_from_review`` — LLM converts a review's critique
   into a list of RevisionTask. One LLM call per review round.
2. ``apply_revision_task`` — programmatic helper that marks a task
   ``in_progress`` (the actual edit is done by the user / SKILL).
3. ``resolve_revision_task`` — user marks done with a one-line summary.

Persistence: one yaml file per task at
``<project>/.paic/revisions/<round>_<id>.yaml``. Listing scans the dir.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from ulid import ULID

from paic.llm.client import LLMClient
from paic.llm.prompts import load_prompt
from paic.schemas.revision import (
    RevisionSeverity,
    RevisionStatus,
    RevisionTargetKind,
    RevisionTask,
)
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, save_yaml


# --- LLM-facing extraction schema -----------------------------------------


class _ExtractedTask(BaseModel):
    severity: RevisionSeverity = "minor"
    target_kind: RevisionTargetKind = "global"
    target_ref: str | None = None
    summary: str
    detail: str | None = None
    patch_hint: str | None = None
    source_persona: str | None = None


class _ExtractFields(BaseModel):
    tasks: list[_ExtractedTask] = Field(default_factory=list)


# --- Extract --------------------------------------------------------------


def extract_tasks_from_review(
    review_payload: dict[str, Any],
    *,
    llm: LLMClient,
    round_num: int | None = None,
) -> list[RevisionTask]:
    """Call the LLM to convert a review payload into RevisionTasks.

    ``review_payload`` is whatever the review graph produces — typically
    a dict containing the moderator's synthesis plus per-persona
    critiques. We serialize what we can and let the LLM extract.
    """
    if not isinstance(review_payload, dict) or not review_payload:
        return []

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
        # Last resort: dump the whole payload.
        parts.append(_dump_text(review_payload))

    fields = llm.complete_json(
        system=load_prompt("revision_extract"),
        user="\n\n".join(parts),
        schema=_ExtractFields,
        max_tokens=2400,
        temperature=0.2,
        node="revision_extract",
    )
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
    return tasks


def _dump_text(obj: Any) -> str:
    """Best-effort string render of nested critique data."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return "\n".join(f"{k}: {_dump_text(v)}" for k, v in obj.items())
    if isinstance(obj, list):
        return "\n".join(f"- {_dump_text(x)}" for x in obj)
    return str(obj)


# --- Persistence ----------------------------------------------------------


def task_path(paths: ProjectPaths, task: RevisionTask) -> Path:
    """Stable per-task yaml path: ``<round>_<id>.yaml`` (round=000 when None)."""
    round_str = (
        f"{task.source_review_round:03d}"
        if task.source_review_round is not None
        else "000"
    )
    return paths.revisions_dir / f"{round_str}_{task.id}.yaml"


def save_task(paths: ProjectPaths, task: RevisionTask) -> str:
    """Write one task to disk. Returns the path as a string."""
    paths.revisions_dir.mkdir(parents=True, exist_ok=True)
    path = task_path(paths, task)
    save_yaml(path, task.model_dump(mode="json"))
    return str(path)


def save_tasks(paths: ProjectPaths, tasks: list[RevisionTask]) -> list[str]:
    return [save_task(paths, t) for t in tasks]


def list_tasks(
    paths: ProjectPaths,
    status: RevisionStatus | None = None,
    severity: RevisionSeverity | None = None,
    round_num: int | None = None,
) -> list[RevisionTask]:
    """Scan ``.paic/revisions/`` and return tasks matching the optional filters."""
    if not paths.revisions_dir.is_dir():
        return []
    tasks: list[RevisionTask] = []
    for path in sorted(paths.revisions_dir.iterdir()):
        if not (path.is_file() and path.suffix == ".yaml"):
            continue
        raw = load_yaml(path)
        if not isinstance(raw, dict):
            continue
        try:
            task = RevisionTask.from_yaml_dict(raw)
        except Exception:
            continue
        if status and task.status != status:
            continue
        if severity and task.severity != severity:
            continue
        if round_num is not None and task.source_review_round != round_num:
            continue
        tasks.append(task)
    return tasks


def find_task(paths: ProjectPaths, task_id: str) -> RevisionTask | None:
    if not paths.revisions_dir.is_dir():
        return None
    for path in paths.revisions_dir.iterdir():
        if path.is_file() and path.suffix == ".yaml":
            raw = load_yaml(path)
            if not isinstance(raw, dict):
                continue
            if raw.get("id") == task_id:
                return RevisionTask.from_yaml_dict(raw)
    return None


def update_task(paths: ProjectPaths, task: RevisionTask) -> str:
    """Persist updated task fields back to disk under the same path."""
    task.updated_at = datetime.now(UTC)
    return save_task(paths, task)


# --- Apply / resolve ------------------------------------------------------


def mark_in_progress(paths: ProjectPaths, task_id: str) -> RevisionTask | None:
    task = find_task(paths, task_id)
    if task is None:
        return None
    task.status = "in_progress"
    update_task(paths, task)
    return task


def resolve_task(
    paths: ProjectPaths,
    task_id: str,
    resolution_summary: str,
) -> RevisionTask | None:
    task = find_task(paths, task_id)
    if task is None:
        return None
    task.status = "resolved"
    task.resolution_summary = resolution_summary
    task.resolved_at = datetime.now(UTC)
    update_task(paths, task)
    return task

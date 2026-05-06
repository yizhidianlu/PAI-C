"""Run management tools: ``paic_runs_list``, ``paic_runs_resume``, ``paic_runs_cancel``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paic.mcp_server import runs as runs_registry


def runs_list_tool(
    project_dir: str | None = None,
    status_filter: str | None = None,
) -> dict[str, Any]:
    rows = runs_registry.list_runs(
        project_dir=project_dir,
        status_filter=status_filter,  # type: ignore[arg-type]
    )
    return {"runs": rows, "count": len(rows)}


def runs_resume_tool(run_id: str, **inputs: Any) -> dict[str, Any]:
    """Generic resume — dispatches to the right graph based on run kind.

    For Phase 5 we only know about ``ideate``; later phases extend this.
    """
    record = runs_registry.get(run_id)
    if record is None:
        return {"error": "run_not_found", "run_id": run_id}

    kind = record["kind"]
    project_dir = record["project_dir"]

    if kind == "ideate":
        from paic.mcp_server.tools.ideate import ideate_step

        return ideate_step(
            project_dir,
            run_id,
            keep=inputs.get("keep"),
            feedback=inputs.get("feedback"),
        )
    if kind == "review":
        from paic.mcp_server.tools.review import review_step

        return review_step(
            project_dir,
            run_id,
            rebuttal=inputs.get("rebuttal"),
            plan_diff=inputs.get("plan_diff"),
            skip_to_verdict=bool(inputs.get("skip_to_verdict")),
        )
    if kind == "experiment":
        # Experiment graph is linear & non-interruptible in MVP — nothing to resume.
        return {
            "error": "experiment_runs_have_no_resume_state",
            "hint": "Re-run paic_experiment_start to redesign the plan.",
        }
    return {"error": "unknown_kind", "kind": kind}


def runs_cancel_tool(run_id: str) -> dict[str, Any]:
    record = runs_registry.get(run_id)
    if record is None:
        return {"error": "run_not_found", "run_id": run_id}
    runs_registry.update(run_id, status="cancelled")
    return {"run_id": run_id, "status": "cancelled"}

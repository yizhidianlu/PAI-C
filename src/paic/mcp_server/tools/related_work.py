"""``paic_related_work_*`` MCP tools — cluster + status."""

from __future__ import annotations

from typing import Any

from paic.library.clustering import (
    cluster_related_work,
    load_related_work_plan,
    save_related_work_plan,
)
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.schemas.related_work import RelatedWorkPlan
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml


def related_work_cluster_tool(
    project_dir: str,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Cluster the project library into related-work groups.

    Reads ``selected.yaml`` + each paper's structured summary (when
    present) + ``paper_plan.yaml`` (for thesis context), runs a single
    LLM clustering call, persists the result to
    ``.paic/plans/related_work_clusters.yaml``, and returns the cluster
    list.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    plan: dict[str, Any] | None = None
    if paths.paper_plan_yaml.is_file():
        loaded = load_yaml(paths.paper_plan_yaml)
        if isinstance(loaded, dict):
            plan = loaded

    client = llm or get_default_client()
    try:
        clusters = cluster_related_work(paths, plan, llm=client)
    except LLMUnavailable as exc:
        return {"error": "llm_unavailable", "detail": str(exc)}

    if not clusters:
        return {
            "error": "library_empty",
            "hint": "Run /paic-search and /paic-ingest before clustering.",
        }

    plan_obj = RelatedWorkPlan(clusters=clusters)
    save_related_work_plan(paths, plan_obj)

    return {
        "cluster_count": len(clusters),
        "total_members": sum(len(c.members) for c in clusters),
        "clusters": [c.model_dump(mode="json") for c in clusters],
        "path": str(paths.related_work_yaml),
    }


def related_work_status_tool(project_dir: str) -> dict[str, Any]:
    """Read existing related-work clusters; report ``exists=False`` if not yet clustered."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    if not paths.related_work_yaml.is_file():
        return {"exists": False, "path": str(paths.related_work_yaml), "plan": None}
    plan = load_related_work_plan(paths)
    return {
        "exists": True,
        "path": str(paths.related_work_yaml),
        "cluster_count": len(plan.clusters),
        "plan": plan.model_dump(mode="json"),
    }

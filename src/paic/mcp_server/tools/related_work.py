"""``paic_related_work_*`` MCP tools — cluster + status."""

from __future__ import annotations

from typing import Any

from paic.library.clustering import (
    _ClusterFields,
    _format_library_for_clustering,
    cluster_related_work,
    load_related_work_plan,
    save_related_work_plan,
)
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.llm.host import build_host_directive
from paic.llm.router import LLMRouter
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

    from paic.config import load_config
    cfg = load_config()
    router = LLMRouter(cfg)
    if router.is_host_orchestrated("relwork_cluster"):
        library_md = _format_library_for_clustering(paths)
        if not library_md:
            return {
                "error": "library_empty",
                "hint": "Run /paic-search and /paic-ingest before clustering.",
            }
        parts = ["### LIBRARY", library_md]
        if plan:
            parts.append("")
            parts.append("### PROPOSED PAPER")
            if thesis := plan.get("thesis"):
                parts.append(f"thesis: {thesis}")
            for c in (plan.get("contributions") or []):
                if isinstance(c, dict):
                    parts.append(
                        f"contribution {c.get('id', '?')}: {c.get('title', '')} — {c.get('description', '')}"
                    )
        return build_host_directive(
            node="relwork_cluster",
            instructions=(
                "Cluster the library into related-work groups matching "
                "`schema_hint`. Then call "
                "mcp__paic__paic_relwork_cluster_persist with `clusters=<your JSON>`."
            ),
            user_prompt="\n".join(parts),
            schema_hint=_ClusterFields.model_json_schema(),
            next_tool="mcp__paic__paic_relwork_cluster_persist",
        ).to_dict()

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


def related_work_cluster_persist_tool(
    project_dir: str,
    clusters: dict[str, Any],
) -> dict[str, Any]:
    """Persist host-generated related-work clusters (LLM-free)."""
    from pydantic import ValidationError

    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    try:
        fields = _ClusterFields.model_validate(clusters)
    except ValidationError as exc:
        return {"error": "schema_validation_failed", "detail": exc.errors()}

    if not fields.clusters:
        return {
            "error": "library_empty",
            "hint": "Host returned an empty cluster list.",
        }

    plan_obj = RelatedWorkPlan(clusters=list(fields.clusters))
    save_related_work_plan(paths, plan_obj)
    return {
        "cluster_count": len(fields.clusters),
        "total_members": sum(len(c.members) for c in fields.clusters),
        "clusters": [c.model_dump(mode="json") for c in fields.clusters],
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

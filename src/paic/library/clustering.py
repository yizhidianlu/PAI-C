"""Related-work clustering helpers (§quality phase 7).

Single LLM call that groups the project library into 3-5 clusters by
method family / dataset / task / limitation / contribution type, with an
explicit contrast point against the proposed method. The resulting
clusters drive related-work paragraph generation in paragraph_compose.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from paic.latex.filler import _cite_key
from paic.llm.client import LLMClient
from paic.llm.prompts import load_prompt
from paic.schemas.related_work import RelatedWorkCluster, RelatedWorkPlan
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, save_yaml


class _ClusterFields(BaseModel):
    clusters: list[RelatedWorkCluster] = Field(default_factory=list)


def _format_library_for_clustering(paths: ProjectPaths, max_papers: int = 80) -> str:
    """Build a markdown bullet list of papers + their method/contribution
    notes for the LLM to cluster."""
    selected = load_yaml(paths.selected_yaml) or {}
    if not isinstance(selected, dict):
        return ""
    papers = list(selected.get("papers") or [])[:max_papers]
    if not papers:
        return ""
    lines: list[str] = []
    for paper in papers:
        cite_key = _cite_key(paper)
        title = paper.get("title", "")
        year = paper.get("year")
        # Pull from structured summary if present (§quality phase 3).
        summary_path = paths.summaries_dir / f"{cite_key}.yaml"
        summary_bits: list[str] = []
        if summary_path.is_file():
            data = load_yaml(summary_path)
            if isinstance(data, dict):
                if cont_type := data.get("contribution_type"):
                    summary_bits.append(f"type={cont_type}")
                if datasets := data.get("datasets"):
                    summary_bits.append(f"datasets={datasets}")
                if baselines := data.get("baselines"):
                    summary_bits.append(f"baselines={baselines}")
                method = data.get("method") or ""
                if isinstance(method, str) and method:
                    summary_bits.append(f"method={method[:160]}")
        bits = " | ".join(summary_bits) or "(no structured summary)"
        lines.append(f"- [{cite_key}] {title} ({year}) — {bits}")
    return "\n".join(lines)


def cluster_related_work(
    paths: ProjectPaths,
    paper_plan: dict[str, Any] | None,
    *,
    llm: LLMClient,
) -> list[RelatedWorkCluster]:
    """Run the LLM clustering pass and return the resulting clusters.

    Returns an empty list if the library is empty.
    """
    library_md = _format_library_for_clustering(paths)
    if not library_md:
        return []

    parts = ["### LIBRARY"]
    parts.append(library_md)
    if paper_plan:
        parts.append("")
        parts.append("### PROPOSED PAPER")
        if thesis := paper_plan.get("thesis"):
            parts.append(f"thesis: {thesis}")
        for c in (paper_plan.get("contributions") or []):
            if isinstance(c, dict):
                parts.append(f"contribution {c.get('id', '?')}: {c.get('title', '')} — {c.get('description', '')}")

    fields = llm.complete_json(
        system=load_prompt("related_work_cluster"),
        user="\n".join(parts),
        schema=_ClusterFields,
        max_tokens=2400,
        temperature=0.2,
        node="relwork_cluster",
    )
    return list(fields.clusters)


# --- Persistence -----------------------------------------------------------


def load_related_work_plan(paths: ProjectPaths) -> RelatedWorkPlan:
    if not paths.related_work_yaml.is_file():
        return RelatedWorkPlan()
    raw = load_yaml(paths.related_work_yaml) or {}
    if not isinstance(raw, dict):
        return RelatedWorkPlan()
    return RelatedWorkPlan.from_yaml_dict(raw)


def save_related_work_plan(paths: ProjectPaths, plan: RelatedWorkPlan) -> None:
    paths.plans_dir.mkdir(parents=True, exist_ok=True)
    plan.last_updated_at = datetime.now(UTC)
    save_yaml(paths.related_work_yaml, plan.model_dump(mode="json"))

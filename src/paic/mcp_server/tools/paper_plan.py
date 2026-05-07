"""``paic_paper_plan_create`` / ``_update`` / ``_status`` — global paper plan tools.

A PAI-C project has at most one ``paper_plan.yaml`` (singleton). It captures
the thesis, contributions, section intent, terminology / symbols, and reserved
figure / table / algorithm slots so downstream compose / claim / quality-gate
stages can ground their output against one consistent global plan.

Generation flow:
1. Load the source ``IdeaCard`` and ``ExperimentPlan``.
2. Optionally include the project library (``selected.yaml``) as context.
3. Single LLM call returns ``_PlanFields`` (LLM-facing subset).
4. Wrap into a full ``PaperPlan`` (adds ``schema_version``, ``created_at``,
   ``updated_at``, ``idea_id``, ``experiment_id``).
5. Persist to ``<project>/.paic/plans/paper_plan.yaml``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.llm.prompts import load_prompt
from paic.schemas.idea import IdeaCard
from paic.schemas.paper_plan import (
    AlgorithmPlanItem,
    ContributionEntry,
    FigurePlanItem,
    PaperPlan,
    SectionPlanEntry,
    TablePlanItem,
)
from paic.workspace.paths import ProjectPaths, resolve_project
from paic.workspace.store import load_yaml, save_yaml


# --- LLM-facing schema (subset; created_at/updated_at/idea_id added in finalize)

class _PlanFields(BaseModel):
    thesis: str
    target_venue: str | None = None
    audience: str | None = None
    contributions: list[ContributionEntry] = Field(default_factory=list)
    section_plan: list[SectionPlanEntry] = Field(default_factory=list)
    terminology: dict[str, str] = Field(default_factory=dict)
    symbols: dict[str, str] = Field(default_factory=dict)
    figure_plan: list[FigurePlanItem] = Field(default_factory=list)
    table_plan: list[TablePlanItem] = Field(default_factory=list)
    algorithm_plan: list[AlgorithmPlanItem] = Field(default_factory=list)
    open_todos: list[str] = Field(default_factory=list)


# --- Helpers ---------------------------------------------------------------

def _format_idea_for_llm(idea: dict[str, Any]) -> str:
    return (
        f"### IDEA\n"
        f"Title: {idea.get('title')}\n"
        f"One-liner: {idea.get('one_liner')}\n"
        f"Motivation: {idea.get('motivation')}\n"
        f"Proposed approach: {idea.get('proposed_approach')}\n"
        f"Novelty claim: {idea.get('novelty_claim')}\n"
        f"Expected contribution: {idea.get('expected_contribution')}\n"
        f"Grounded in: {', '.join(idea.get('grounded_in', []) or []) or '(none cited)'}\n"
    )


def _format_experiment_for_llm(exp: dict[str, Any]) -> str:
    parts = ["### EXPERIMENT"]
    for key in (
        "research_questions", "hypotheses", "proposed_method", "datasets",
        "baselines", "metrics", "ablations", "success_criteria", "compute_budget",
    ):
        value = exp.get(key)
        if value:
            parts.append(f"- {key}: {value}")
    return "\n".join(parts)


def _format_library_for_llm(paths: ProjectPaths, max_papers: int = 60) -> str:
    selected = load_yaml(paths.selected_yaml) or {}
    if not isinstance(selected, dict):
        return ""
    papers = selected.get("papers") or []
    if not papers:
        return ""
    lines = ["### LIBRARY (papers available for citation)"]
    for paper in papers[:max_papers]:
        cite_key = paper.get("cite_key") or paper.get("arxiv_id") or paper.get("doi") or "?"
        title = paper.get("title", "")
        year = paper.get("year")
        lines.append(f"- [{cite_key}] {title} ({year})")
    if len(papers) > max_papers:
        lines.append(f"- … +{len(papers) - max_papers} more (truncated)")
    return "\n".join(lines)


# --- Tools -----------------------------------------------------------------

def paper_plan_create_tool(
    project_dir: str,
    idea_id: str,
    experiment_id: str,
    target_venue: str | None = None,
    audience: str | None = None,
    dry_run: bool = False,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Generate ``paper_plan.yaml`` from an idea + experiment + library.

    Errors out if a plan already exists; use ``paper_plan_update_tool`` to
    revise. Set ``dry_run=True`` to return the generated plan without
    writing it.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    if paths.paper_plan_yaml.exists() and not dry_run:
        return {
            "error": "paper_plan_already_exists",
            "path": str(paths.paper_plan_yaml),
            "hint": "Use paic_paper_plan_update to revise, or delete the file to regenerate.",
        }

    idea_path = paths.ideas_dir / f"{idea_id}.yaml"
    if not idea_path.is_file():
        return {"error": "idea_not_found", "idea_id": idea_id}
    idea = load_yaml(idea_path)
    IdeaCard.model_validate(idea)

    exp_path = paths.experiments_dir / f"{experiment_id}.yaml"
    if not exp_path.is_file():
        return {"error": "experiment_not_found", "experiment_id": experiment_id}
    experiment = load_yaml(exp_path)

    user_msg = "\n\n".join(filter(None, [
        _format_idea_for_llm(idea),
        _format_experiment_for_llm(experiment),
        _format_library_for_llm(paths),
    ]))
    if target_venue:
        user_msg += f"\n\n### CONSTRAINTS\nTarget venue: {target_venue}"
    if audience:
        user_msg += (f"\nIntended audience: {audience}"
                     if target_venue else f"\n\n### CONSTRAINTS\nIntended audience: {audience}")

    client = llm or get_default_client()
    try:
        fields = client.complete_json(
            system=load_prompt("paper_plan_generate"),
            user=user_msg,
            schema=_PlanFields,
            max_tokens=4096,
            temperature=0.2,
            node="paper_plan_generate",
        )
    except LLMUnavailable as exc:
        return {"error": "llm_unavailable", "detail": str(exc)}

    now = datetime.now(UTC)
    plan = PaperPlan(
        thesis=fields.thesis,
        target_venue=fields.target_venue or target_venue,
        audience=fields.audience or audience,
        contributions=fields.contributions,
        section_plan=fields.section_plan,
        terminology=fields.terminology,
        symbols=fields.symbols,
        figure_plan=fields.figure_plan,
        table_plan=fields.table_plan,
        algorithm_plan=fields.algorithm_plan,
        open_todos=fields.open_todos,
        idea_id=idea_id,
        experiment_id=experiment_id,
        created_at=now,
        updated_at=now,
    )

    if not dry_run:
        paths.plans_dir.mkdir(parents=True, exist_ok=True)
        save_yaml(paths.paper_plan_yaml, plan.model_dump(mode="json"))

    return {
        "plan": plan.model_dump(mode="json"),
        "path": str(paths.paper_plan_yaml),
        "written": not dry_run,
    }


def paper_plan_update_tool(
    project_dir: str,
    patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a shallow ``patch`` (dict merge) onto the existing paper plan.

    Returns ``{plan, path, changed_keys}``. Refuses to update if the plan
    doesn't exist yet — caller should run ``paic_paper_plan_create`` first.
    """
    paths = resolve_project(project_dir)
    if not paths.paper_plan_yaml.exists():
        return {
            "error": "paper_plan_not_found",
            "path": str(paths.paper_plan_yaml),
            "hint": "Run paic_paper_plan_create first.",
        }
    if not patch:
        return {"error": "empty_patch", "hint": "Pass at least one field to patch."}

    raw = load_yaml(paths.paper_plan_yaml) or {}
    if not isinstance(raw, dict):
        return {"error": "paper_plan_corrupt", "path": str(paths.paper_plan_yaml)}

    changed_keys: list[str] = []
    for k, v in patch.items():
        if raw.get(k) != v:
            raw[k] = v
            changed_keys.append(k)

    if not changed_keys:
        return {
            "plan": raw,
            "path": str(paths.paper_plan_yaml),
            "changed_keys": [],
            "hint": "No-op: patch matches current plan.",
        }

    raw["updated_at"] = datetime.now(UTC).isoformat()
    plan = PaperPlan.from_yaml_dict(raw)
    save_yaml(paths.paper_plan_yaml, plan.model_dump(mode="json"))

    return {
        "plan": plan.model_dump(mode="json"),
        "path": str(paths.paper_plan_yaml),
        "changed_keys": changed_keys,
    }


def paper_plan_status_tool(project_dir: str) -> dict[str, Any]:
    """Read ``paper_plan.yaml`` if present; report ``exists=False`` otherwise."""
    paths = resolve_project(project_dir)
    if not paths.paper_plan_yaml.exists():
        return {
            "exists": False,
            "path": str(paths.paper_plan_yaml),
            "plan": None,
        }
    raw = load_yaml(paths.paper_plan_yaml) or {}
    if not isinstance(raw, dict):
        return {"error": "paper_plan_corrupt", "path": str(paths.paper_plan_yaml)}
    plan = PaperPlan.from_yaml_dict(raw)
    return {
        "exists": True,
        "path": str(paths.paper_plan_yaml),
        "plan": plan.model_dump(mode="json"),
    }

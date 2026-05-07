"""Experiment design graph.

Linear pipeline (no human-in-the-loop interrupt by default):

```
START → load_idea → propose_plan → finalize → END
```

The proposed_method, baselines, datasets, metrics, and ablations are produced
by a single LLM call (``propose_plan``); a thin sanity check rejects obviously
broken outputs and asks the model for one corrective pass via the LLM client's
own retry path. Splitting into more nodes is possible later if the team wants
to override individual fields, but for MVP one call keeps cost & latency low.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from ulid import ULID

from paic.llm.client import LLMClient
from paic.llm.prompts import load_prompt
from paic.schemas.experiment import (
    AblationAxis,
    Baseline,
    Dataset,
    ExperimentPlan,
    Metric,
)
from paic.schemas.idea import IdeaCard
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, save_yaml


# --- LLM-facing schema (no id, status, created_at — those are added client-side)

class _DesignFields(BaseModel):
    research_questions: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    baselines: list[Baseline] = Field(default_factory=list)
    proposed_method: str
    metrics: list[Metric] = Field(default_factory=list)
    ablations: list[AblationAxis] = Field(default_factory=list)
    compute_budget: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    threats_to_validity: list[str] = Field(default_factory=list)
    timeline_weeks: int | None = None
    # §quality phase 5 — Optional separable concerns. The LLM is asked to
    # populate them in the same call; verifier nodes downstream check that
    # they're filled in plausibly.
    statistical_plan: list[str] = Field(default_factory=list)
    reproducibility: list[str] = Field(default_factory=list)


class ExperimentState(TypedDict, total=False):
    project_dir: str
    idea_id: str
    constraints: dict[str, Any] | None
    idea: dict[str, Any]
    plan: dict[str, Any]
    experiment_id: str
    run_id: str


@dataclass
class ExperimentDeps:
    llm: LLMClient
    paths: ProjectPaths


# --- Nodes ----------------------------------------------------------------

def _load_idea(state: ExperimentState, deps: ExperimentDeps) -> dict[str, Any]:
    idea_path = deps.paths.ideas_dir / f"{state['idea_id']}.yaml"
    if not idea_path.exists():
        raise FileNotFoundError(f"Idea not found: {state['idea_id']}")
    record = load_yaml(idea_path)
    # Validate via pydantic so downstream nodes see a known shape.
    IdeaCard.model_validate(record)
    return {"idea": record}


def _format_idea_for_llm(idea: dict[str, Any]) -> str:
    return (
        f"### IDEA\n"
        f"Title: {idea.get('title')}\n"
        f"One-liner: {idea.get('one_liner')}\n\n"
        f"Motivation: {idea.get('motivation')}\n\n"
        f"Proposed approach: {idea.get('proposed_approach')}\n\n"
        f"Novelty claim: {idea.get('novelty_claim')}\n\n"
        f"Expected contribution: {idea.get('expected_contribution')}\n\n"
        f"Grounded in: {', '.join(idea.get('grounded_in', []) or []) or '(none cited)'}\n"
        f"Risk factors: {', '.join(idea.get('risk_factors', []) or []) or '(none listed)'}\n"
    )


def _propose_plan(state: ExperimentState, deps: ExperimentDeps) -> dict[str, Any]:
    idea = state["idea"]
    constraints = state.get("constraints") or {}
    user_msg = _format_idea_for_llm(idea)
    if constraints:
        kv = "\n".join(f"- {k}: {v}" for k, v in constraints.items())
        user_msg += f"\n### CONSTRAINTS\n{kv}\n"

    fields = deps.llm.complete_json(
        system=load_prompt("experiment_design"),
        user=user_msg,
        schema=_DesignFields,
        max_tokens=4096,
        temperature=0.2,
        node="experiment_design",
    )
    return {"plan": fields.model_dump()}


def _verify_plan(state: ExperimentState, deps: ExperimentDeps) -> dict[str, Any]:
    """§quality phase 5 — programmatic verification of separable concerns.

    Runs after ``_propose_plan`` and before ``_finalize``. No LLM call:
    purely deterministic checks producing a list of soft warnings that
    persist into the experiment yaml's ``validation_warnings`` field.
    The SKILL surfaces these to the user post-design so they can iterate
    on the plan before review.

    Concerns checked (one per slice from the phase-5 spec):
    - ``baseline_retrieve``: every Baseline has ``paper_ref`` set
    - ``dataset_check``: every Dataset has ``license_note`` and at least
      one ``splits`` entry
    - ``metric_select``: at least one metric flagged ``primary=true``
    - ``ablation_design``: ablations list non-empty and each axis has
      ≥2 levels
    - ``compute_feasibility``: ``compute_budget`` non-empty
    - ``statistical_plan``: ``statistical_plan`` non-empty
    - ``repro_checklist``: ``reproducibility`` non-empty
    """
    plan = state.get("plan") or {}
    warnings: list[str] = []

    baselines = plan.get("baselines") or []
    for i, b in enumerate(baselines):
        if isinstance(b, dict) and not b.get("paper_ref"):
            warnings.append(
                f"baseline_retrieve: baseline #{i + 1} '{b.get('name', '?')}' "
                "has no paper_ref; consider attaching arxiv_id / doi."
            )
    if not baselines:
        warnings.append("baseline_retrieve: no baselines listed.")

    datasets = plan.get("datasets") or []
    for i, d in enumerate(datasets):
        if isinstance(d, dict) and not d.get("license_note"):
            warnings.append(
                f"dataset_check: dataset #{i + 1} '{d.get('name', '?')}' "
                "has no license_note; verify usage rights."
            )
        if isinstance(d, dict) and not d.get("splits"):
            warnings.append(
                f"dataset_check: dataset #{i + 1} '{d.get('name', '?')}' "
                "has no splits; specify n per train/val/test."
            )
    if not datasets:
        warnings.append("dataset_check: no datasets listed.")

    metrics = plan.get("metrics") or []
    primary_count = sum(
        1 for m in metrics if isinstance(m, dict) and m.get("primary")
    )
    if primary_count == 0:
        warnings.append("metric_select: no metric flagged primary=true.")
    elif primary_count > 1:
        warnings.append(
            f"metric_select: {primary_count} metrics flagged primary; "
            "exactly one is preferred."
        )

    ablations = plan.get("ablations") or []
    if not ablations:
        warnings.append("ablation_design: no ablations listed.")
    else:
        for i, a in enumerate(ablations):
            levels = (a.get("levels") or []) if isinstance(a, dict) else []
            if len(levels) < 2:
                warnings.append(
                    f"ablation_design: ablation #{i + 1} "
                    f"'{a.get('factor', '?')}' has <2 levels."
                )

    if not (plan.get("compute_budget") or "").strip():
        warnings.append("compute_feasibility: compute_budget is empty.")

    if not plan.get("statistical_plan"):
        warnings.append(
            "statistical_plan: missing — specify seeds, n, significance test, "
            "multiple-comparison correction."
        )

    if not plan.get("reproducibility"):
        warnings.append(
            "repro_checklist: missing — specify random_state, config_hash, "
            "version pinning, hardware spec."
        )

    return {"plan": {**plan, "validation_warnings": warnings}}


def _finalize(state: ExperimentState, deps: ExperimentDeps) -> dict[str, Any]:
    experiment_id = str(ULID())
    plan_data = state["plan"]
    full = ExperimentPlan(
        id=experiment_id,
        idea_id=state["idea_id"],
        research_questions=plan_data.get("research_questions", []),
        hypotheses=plan_data.get("hypotheses", []),
        datasets=plan_data.get("datasets", []),
        baselines=plan_data.get("baselines", []),
        proposed_method=plan_data["proposed_method"],
        metrics=plan_data.get("metrics", []),
        ablations=plan_data.get("ablations", []),
        compute_budget=plan_data.get("compute_budget"),
        success_criteria=plan_data.get("success_criteria", []),
        threats_to_validity=plan_data.get("threats_to_validity", []),
        timeline_weeks=plan_data.get("timeline_weeks"),
        statistical_plan=plan_data.get("statistical_plan", []),
        reproducibility=plan_data.get("reproducibility", []),
        validation_warnings=plan_data.get("validation_warnings", []),
        created_at=datetime.now(UTC),
        parent_run_id=state.get("run_id"),
        status="draft",
    )
    deps.paths.experiments_dir.mkdir(parents=True, exist_ok=True)
    save_yaml(
        deps.paths.experiments_dir / f"{experiment_id}.yaml",
        full.model_dump(mode="json"),
    )
    return {"experiment_id": experiment_id}


# --- Builder --------------------------------------------------------------

def build_experiment_graph(deps: ExperimentDeps):
    from paic.graphs.checkpointer import get_checkpointer

    g = StateGraph(ExperimentState)
    g.add_node("load_idea", lambda s: _load_idea(s, deps))
    g.add_node("propose_plan", lambda s: _propose_plan(s, deps))
    g.add_node("verify_plan", lambda s: _verify_plan(s, deps))
    g.add_node("finalize", lambda s: _finalize(s, deps))

    g.add_edge(START, "load_idea")
    g.add_edge("load_idea", "propose_plan")
    g.add_edge("propose_plan", "verify_plan")
    g.add_edge("verify_plan", "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=get_checkpointer(deps.paths))

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
    g.add_node("finalize", lambda s: _finalize(s, deps))

    g.add_edge(START, "load_idea")
    g.add_edge("load_idea", "propose_plan")
    g.add_edge("propose_plan", "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=get_checkpointer(deps.paths))

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

from paic.latex.filler import _cite_key
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


def _load_library_summary(paths: ProjectPaths, cap: int = 40) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (cite_keys, entries) for the project library, capped to ``cap``.

    ``entries`` is the trimmed view passed to the LLM as a baseline-source
    whitelist; ``cite_keys`` is a flat list used by the verifier.
    """
    selected = load_yaml(paths.selected_yaml) or {}
    if not isinstance(selected, dict):
        return [], []
    records = list(selected.get("papers") or [])[:cap]
    entries: list[dict[str, Any]] = []
    cite_keys: list[str] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        ck = _cite_key(r)
        cite_keys.append(ck)
        entries.append(
            {
                "cite_key": ck,
                "arxiv_id": r.get("arxiv_id"),
                "doi": r.get("doi"),
                "title": (r.get("title") or "").strip(),
                "year": r.get("year"),
            }
        )
    return cite_keys, entries


def _format_library_for_llm(entries: list[dict[str, Any]]) -> str:
    """Render the library cite-key whitelist as a compact bullet list."""
    if not entries:
        return (
            "### LIBRARY (project's ingested papers — baseline whitelist)\n"
            "(empty — only return baselines you genuinely know; signal the "
            "user to ingest more papers via /paic-ingest)\n"
        )
    lines = ["### LIBRARY (project's ingested papers — baseline whitelist)"]
    lines.append(
        "Prefer baselines whose paper_ref is in this list. If the strongest "
        "baseline is missing, you may still include it but its paper_ref "
        "should be the upstream id (arxiv_id / DOI) — the verifier will "
        "warn the user to /paic-ingest it."
    )
    for e in entries:
        ref = e.get("arxiv_id") or e.get("doi") or "(no-id)"
        lines.append(
            f"- [{e['cite_key']}] {e['title']} ({e.get('year') or '?'}) — ref: {ref}"
        )
    return "\n".join(lines) + "\n"


def _propose_plan(state: ExperimentState, deps: ExperimentDeps) -> dict[str, Any]:
    idea = state["idea"]
    constraints = state.get("constraints") or {}
    cite_keys, library_entries = _load_library_summary(deps.paths)
    user_msg = _format_idea_for_llm(idea)
    user_msg += "\n" + _format_library_for_llm(library_entries)
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
    plan = fields.model_dump()
    plan["_library_cite_keys"] = cite_keys  # consumed by _verify_plan, dropped before persist
    return {"plan": plan}


def _verify_plan(state: ExperimentState, deps: ExperimentDeps) -> dict[str, Any]:
    """§quality phase 5 — programmatic verification of separable concerns.

    Runs after ``_propose_plan`` and before ``_finalize``. No LLM call:
    purely deterministic checks producing a list of soft warnings that
    persist into the experiment yaml's ``validation_warnings`` field.
    The SKILL surfaces these to the user post-design so they can iterate
    on the plan before review.

    Concerns checked (one per slice from the phase-5 spec):
    - ``baseline_retrieve``: every Baseline has ``paper_ref`` set
    - ``baseline_in_library``: each ``paper_ref`` is one of the project's
      ingested papers (cite_key, arxiv_id, or DOI in selected.yaml). Baselines
      that aren't in the library still pass design but are surfaced so the
      user can /paic-ingest them before the compose step's cite-guard rejects.
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
    library_cite_keys: set[str] = set(plan.get("_library_cite_keys") or [])
    library_arxiv_ids: set[str] = {
        ck.removeprefix("arxiv_").replace("_", ".", 1)
        for ck in library_cite_keys
        if ck.startswith("arxiv_")
    }

    baselines = plan.get("baselines") or []
    for i, b in enumerate(baselines):
        if not isinstance(b, dict):
            continue
        ref = b.get("paper_ref")
        if not ref:
            warnings.append(
                f"baseline_retrieve: baseline #{i + 1} '{b.get('name', '?')}' "
                "has no paper_ref; consider attaching arxiv_id / doi."
            )
            continue
        # baseline_in_library: paper_ref should be ingested so the compose
        # step's \cite{} guard accepts it. Match against cite_key directly,
        # the arxiv_id form (LLM typically returns "2102.09050"), or DOI
        # (slug-derived cite_keys keep the doi prefix).
        ref_norm = str(ref).strip().lower()
        in_lib = (
            ref_norm in library_cite_keys
            or ref_norm in library_arxiv_ids
            or any(ck.endswith(ref_norm.replace("/", "_").replace(".", "_").replace("-", "_")) for ck in library_cite_keys if ck.startswith("doi_"))
        )
        if not in_lib:
            warnings.append(
                f"baseline_in_library: baseline #{i + 1} '{b.get('name', '?')}' "
                f"paper_ref={ref!r} is not in library/selected.yaml; run "
                "/paic-ingest before compose, or downstream \\cite{} guard will reject."
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

    # Drop the transient _library_cite_keys carrier — it's a propose→verify
    # plumbing field, not part of the ExperimentPlan schema.
    cleaned = {k: v for k, v in plan.items() if k != "_library_cite_keys"}
    return {"plan": {**cleaned, "validation_warnings": warnings}}


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

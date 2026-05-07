"""Multi-agent review tool — start / step / status."""

from __future__ import annotations

from typing import Any

from langgraph.types import Command
from pydantic import ValidationError
from ulid import ULID

from paic.config import load_config
from paic.graphs.review_graph import ReviewDeps, build_review_graph
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.llm.router import LLMRouter
from paic.mcp_server import runs as runs_registry
from paic.personas import PERSONA_NAMES
from paic.schemas.experiment import ExperimentPlan
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml


def _interpret_state(graph_state) -> dict[str, Any]:
    is_paused = bool(graph_state.next)
    next_node = graph_state.next[0] if graph_state.next else None
    values = graph_state.values or {}

    interrupt_payload: dict[str, Any] | None = None
    if is_paused and graph_state.tasks:
        for task in graph_state.tasks:
            for itr in task.interrupts or []:
                if itr.value:
                    interrupt_payload = itr.value
                    break
            if interrupt_payload:
                break

    # Phase C — distinguish the new ``host_orchestration`` interrupt stage
    # from the long-standing ``await_user`` stage so Skills can branch
    # without inspecting the payload's internal shape.
    awaiting: str | None = None
    host_directive: dict[str, Any] | None = None
    if interrupt_payload and isinstance(interrupt_payload, dict):
        stage = interrupt_payload.get("stage")
        if stage == "host_orchestration":
            awaiting = "host_orchestration"
            host_directive = interrupt_payload.get("directive")
        elif stage:
            awaiting = "user"

    return {
        "next_node": next_node,
        "is_paused": is_paused,
        "round": values.get("round"),
        "max_rounds": values.get("max_rounds"),
        "moderator_notes": values.get("moderator_notes") or [],
        "verdict": values.get("verdict"),
        "interrupt_payload": interrupt_payload,
        "awaiting": awaiting,
        "host_directive": host_directive,
    }


def _make_deps(project_dir: str, llm: LLMClient | None) -> ReviewDeps:
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        raise FileNotFoundError(f".paic/ does not exist at {paths.root}")
    cfg = load_config()
    router = LLMRouter(cfg)
    return ReviewDeps(
        llm=llm or get_default_client(),
        paths=paths,
        router=router,
    )


def review_start(
    project_dir: str,
    experiment_id: str,
    personas: list[str] | None = None,
    rounds: int = 2,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    if personas is None:
        personas = list(PERSONA_NAMES)
    else:
        unknown = [p for p in personas if p not in PERSONA_NAMES]
        if unknown:
            return {"error": "unknown_personas", "unknown": unknown, "valid": list(PERSONA_NAMES)}

    try:
        deps = _make_deps(project_dir, llm)
    except FileNotFoundError as exc:
        return {"error": "project_not_initialized", "detail": str(exc)}

    # Validate the experiment YAML up-front so hand-written plans surface
    # field-level pydantic errors instead of crashing inside the graph as a
    # generic ``graph_failed`` (e.g. AttributeError when ``metrics`` is a dict
    # rather than ``list[dict]``).
    exp_path = deps.paths.experiments_dir / f"{experiment_id}.yaml"
    if not exp_path.exists():
        return {"error": "experiment_not_found", "experiment_id": experiment_id}
    try:
        ExperimentPlan.model_validate(load_yaml(exp_path) or {})
    except ValidationError as exc:
        return {
            "error": "experiment_schema_invalid",
            "experiment_id": experiment_id,
            "detail": exc.errors(),
            "hint": (
                "Hand-written experiment YAML is missing fields or has wrong types. "
                "Common gotcha: list-typed fields (metrics, datasets, baselines, "
                "ablations, success_criteria, threats_to_validity, research_questions, "
                "hypotheses) must be lists of dicts, not single dicts. "
                "See src/paic/schemas/experiment.py:ExperimentPlan for the full schema."
            ),
        }

    run_id = str(ULID())
    runs_registry.register(
        run_id=run_id,
        kind="review",
        project_dir=str(deps.paths.root),
        thread_id=run_id,
        status="running",
        current_node="retrieve_context",
    )

    try:
        graph = build_review_graph(deps)
        config = {"configurable": {"thread_id": run_id}}
        graph.invoke(
            {
                "project_dir": str(deps.paths.root),
                "experiment_id": experiment_id,
                "personas": personas,
                "round": 0,
                "max_rounds": rounds,
                "run_id": run_id,
            },
            config=config,
        )
        snapshot = graph.get_state(config)
    except FileNotFoundError as exc:
        runs_registry.update(run_id, status="error", error=str(exc))
        return {"run_id": run_id, "error": "experiment_not_found", "detail": str(exc)}
    except LLMUnavailable as exc:
        runs_registry.update(run_id, status="error", error=str(exc))
        return {"run_id": run_id, "error": "llm_unavailable", "detail": str(exc)}
    except Exception as exc:
        runs_registry.update(run_id, status="error", error=repr(exc))
        return {"run_id": run_id, "error": "graph_failed", "detail": repr(exc)}

    parsed = _interpret_state(snapshot)
    status = "done" if parsed["verdict"] else ("awaiting_input" if parsed["is_paused"] else "running")
    runs_registry.update(run_id, status=status, current_node=parsed["next_node"])
    return {
        "run_id": run_id,
        "thread_id": run_id,
        "status": status,
        "current_node": parsed["next_node"],
        "round": parsed["round"],
        "max_rounds": parsed["max_rounds"],
        "panel": parsed["interrupt_payload"],
        "awaiting": parsed["awaiting"],
        "host_directive": parsed["host_directive"],
        "verdict": parsed["verdict"],
    }


def review_step(
    project_dir: str,
    run_id: str,
    rebuttal: str | None = None,
    plan_diff: str | None = None,
    skip_to_verdict: bool = False,
    host_response: dict[str, Any] | None = None,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    try:
        deps = _make_deps(project_dir, llm)
    except FileNotFoundError as exc:
        return {"error": "project_not_initialized", "detail": str(exc)}

    record = runs_registry.get(run_id)
    if record is None:
        return {"error": "run_not_found", "run_id": run_id}
    if record["kind"] != "review":
        return {"error": "wrong_kind", "expected": "review", "got": record["kind"]}

    config = {"configurable": {"thread_id": record["thread_id"]}}
    graph = build_review_graph(deps)

    # Host orchestration resume path: the Skill is supplying the host LLM's
    # output, which feeds straight back into the paused interrupt() call.
    if host_response is not None:
        try:
            graph.invoke(Command(resume=host_response), config=config)
            snapshot = graph.get_state(config)
        except ValidationError as exc:
            # Schema mismatch: graph is still paused on the same interrupt;
            # surface the directive again so the Skill can retry.
            paused_snapshot = graph.get_state(config)
            paused = _interpret_state(paused_snapshot)
            return {
                "run_id": run_id,
                "error": "host_response_invalid",
                "detail": exc.errors(),
                "host_directive": paused.get("host_directive"),
                "awaiting": paused.get("awaiting"),
            }
        except Exception as exc:
            runs_registry.update(run_id, status="error", error=repr(exc))
            return {"run_id": run_id, "error": "graph_failed", "detail": repr(exc)}

        parsed = _interpret_state(snapshot)
        status = "done" if parsed["verdict"] else ("awaiting_input" if parsed["is_paused"] else "running")
        runs_registry.update(run_id, status=status, current_node=parsed["next_node"])
        return {
            "run_id": run_id,
            "status": status,
            "current_node": parsed["next_node"],
            "round": parsed["round"],
            "max_rounds": parsed["max_rounds"],
            "panel": parsed["interrupt_payload"],
            "awaiting": parsed["awaiting"],
            "host_directive": parsed["host_directive"],
            "verdict": parsed["verdict"],
        }

    payload: dict[str, Any] = {}
    if rebuttal is not None:
        payload["rebuttal"] = rebuttal
    if plan_diff is not None:
        payload["plan_diff"] = plan_diff
    if skip_to_verdict:
        payload["skip_to_verdict"] = True

    try:
        graph.invoke(Command(resume=payload), config=config)
        snapshot = graph.get_state(config)
    except Exception as exc:
        runs_registry.update(run_id, status="error", error=repr(exc))
        return {"run_id": run_id, "error": "graph_failed", "detail": repr(exc)}

    parsed = _interpret_state(snapshot)
    status = "done" if parsed["verdict"] else ("awaiting_input" if parsed["is_paused"] else "running")
    runs_registry.update(run_id, status=status, current_node=parsed["next_node"])
    return {
        "run_id": run_id,
        "status": status,
        "current_node": parsed["next_node"],
        "round": parsed["round"],
        "max_rounds": parsed["max_rounds"],
        "panel": parsed["interrupt_payload"],
        "awaiting": parsed["awaiting"],
        "host_directive": parsed["host_directive"],
        "verdict": parsed["verdict"],
    }


def review_status(project_dir: str, run_id: str) -> dict[str, Any]:
    try:
        deps = _make_deps(project_dir, None)
    except FileNotFoundError as exc:
        return {"error": "project_not_initialized", "detail": str(exc)}

    record = runs_registry.get(run_id)
    if record is None:
        return {"error": "run_not_found", "run_id": run_id}
    if record["kind"] != "review":
        return {"error": "wrong_kind", "expected": "review", "got": record["kind"]}

    graph = build_review_graph(deps)
    config = {"configurable": {"thread_id": record["thread_id"]}}
    snapshot = graph.get_state(config)
    parsed = _interpret_state(snapshot)
    return {
        "run_id": run_id,
        "status": record["status"],
        "current_node": parsed["next_node"],
        "round": parsed["round"],
        "max_rounds": parsed["max_rounds"],
        "moderator_notes": parsed["moderator_notes"],
        "awaiting": parsed["awaiting"],
        "host_directive": parsed["host_directive"],
        "verdict": parsed["verdict"],
    }

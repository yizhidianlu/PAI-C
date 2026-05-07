"""Experiment-design tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ulid import ULID

from paic.graphs.experiment_graph import ExperimentDeps, build_experiment_graph
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.mcp_server import runs as runs_registry
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml, save_yaml


def experiment_start(
    project_dir: str,
    idea_id: str,
    constraints: dict[str, Any] | None = None,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    client = llm or get_default_client()
    deps = ExperimentDeps(llm=client, paths=paths)

    run_id = str(ULID())
    runs_registry.register(
        run_id=run_id,
        kind="experiment",
        project_dir=str(paths.root),
        thread_id=run_id,
        status="running",
        current_node="load_idea",
    )

    try:
        graph = build_experiment_graph(deps)
        config = {"configurable": {"thread_id": run_id}}
        graph.invoke(
            {
                "project_dir": str(paths.root),
                "idea_id": idea_id,
                "constraints": constraints or {},
                "run_id": run_id,
            },
            config=config,
        )
        snapshot = graph.get_state(config)
    except FileNotFoundError as exc:
        runs_registry.update(run_id, status="error", error=str(exc))
        return {"run_id": run_id, "error": "idea_not_found", "detail": str(exc)}
    except LLMUnavailable as exc:
        runs_registry.update(run_id, status="error", error=str(exc))
        return {"run_id": run_id, "error": "llm_unavailable", "detail": str(exc)}
    except Exception as exc:
        runs_registry.update(run_id, status="error", error=repr(exc))
        return {"run_id": run_id, "error": "graph_failed", "detail": repr(exc)}

    values = snapshot.values or {}
    experiment_id = values.get("experiment_id")
    runs_registry.update(run_id, status="done", current_node=None)
    return {
        "run_id": run_id,
        "experiment_id": experiment_id,
        "status": "done",
        "experiment_path": str(paths.experiments_dir / f"{experiment_id}.yaml")
        if experiment_id
        else None,
    }


def experiment_record_result_tool(
    project_dir: str,
    experiment_id: str,
    metric_name: str,
    value: float,
    run_id: str,
    *,
    unit: str = "",
    seed: int | None = None,
    timestamp: str | None = None,
    ci_lower: float | None = None,
    ci_upper: float | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Append a single ``ExperimentResult`` to ``experiments/<id>.yaml``.

    Idempotent by ``(metric_name, run_id, seed)``: a second call with the
    same triple **replaces** the existing entry (use to update CIs / notes
    after re-analysis).

    The ``run_id`` is whatever stable identifier maps back to your run logs
    — git sha, slurm job id, wandb run name, ulid, etc. PAI-C doesn't
    interpret it; ``check_numeric_provenance`` only checks the values.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    exp_path = paths.experiments_dir / f"{experiment_id}.yaml"
    if not exp_path.is_file():
        return {"error": "experiment_not_found", "experiment_id": experiment_id}

    raw = load_yaml(exp_path) or {}
    if not isinstance(raw, dict):
        return {"error": "experiment_corrupt", "path": str(exp_path)}

    new_record: dict[str, Any] = {
        "metric_name": metric_name,
        "value": float(value),
        "unit": unit,
        "run_id": run_id,
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
    }
    if seed is not None:
        new_record["seed"] = int(seed)
    if ci_lower is not None:
        new_record["ci_lower"] = float(ci_lower)
    if ci_upper is not None:
        new_record["ci_upper"] = float(ci_upper)
    if notes:
        new_record["notes"] = notes

    results = list(raw.get("results") or [])
    target_key = (metric_name, run_id, seed)
    replaced_index: int | None = None
    for i, r in enumerate(results):
        if not isinstance(r, dict):
            continue
        if (r.get("metric_name"), r.get("run_id"), r.get("seed")) == target_key:
            replaced_index = i
            break

    if replaced_index is not None:
        results[replaced_index] = new_record
    else:
        results.append(new_record)

    raw["results"] = results
    save_yaml(exp_path, raw)

    return {
        "experiment_id": experiment_id,
        "result_count": len(results),
        "added": replaced_index is None,
        "replaced": replaced_index is not None,
        "path": str(exp_path),
    }

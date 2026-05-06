"""Experiment-design tool."""

from __future__ import annotations

from typing import Any

from ulid import ULID

from paic.graphs.experiment_graph import ExperimentDeps, build_experiment_graph
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.mcp_server import runs as runs_registry
from paic.workspace.paths import resolve_project


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

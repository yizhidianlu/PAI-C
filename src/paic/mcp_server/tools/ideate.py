"""Ideate tool: ``paic_ideate_start`` and ``paic_ideate_step`` (§26 v2)."""

from __future__ import annotations

from typing import Any

from langgraph.types import Command
from ulid import ULID

from paic.config import load_config
from paic.graphs.ideate_graph import (
    DEFAULT_MAX_ROUNDS,
    DEFAULT_PERSONAS,
    IdeateDeps,
    build_ideate_graph,
)
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.llm.router import LLMRouter
from paic.mcp_server import runs as runs_registry
from paic.workspace.paths import resolve_project


def _make_deps(project_dir: str, llm: LLMClient | None) -> IdeateDeps:
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        raise FileNotFoundError(f".paic/ does not exist at {paths.root}")
    client = llm or get_default_client()
    cfg = load_config()
    return IdeateDeps(llm=client, paths=paths, router=LLMRouter(cfg))


def _interpret_state(graph_state) -> dict[str, Any]:
    """Read graph state via SqliteSaver -> turn into MCP-friendly dict."""
    is_paused = bool(graph_state.next)
    next_node = graph_state.next[0] if graph_state.next else None
    values = graph_state.values or {}

    drafts = values.get("drafts", [])
    panel_scores = values.get("panel_scores", [])

    interrupt_payload: dict[str, Any] | None = None
    if is_paused and graph_state.tasks:
        for task in graph_state.tasks:
            for itr in task.interrupts or []:
                if itr.value:
                    interrupt_payload = itr.value
                    break
            if interrupt_payload:
                break

    awaiting: str | None = None
    host_directive: dict[str, Any] | None = None
    if interrupt_payload and isinstance(interrupt_payload, dict):
        stage = interrupt_payload.get("stage")
        if stage == "host_orchestration":
            awaiting = "host_orchestration"
            host_directive = interrupt_payload.get("directive")
        elif stage:
            awaiting = "user"

    # Build preview_ideas (kept for v1-compat callers) + drafts_with_scores
    # (new richer payload). Both are derived from the same source.
    preview_ideas = []
    drafts_with_scores = []
    for i, d in enumerate(drafts):
        ps = panel_scores[i] if i < len(panel_scores) else {}
        preview_ideas.append({
            "idx": i,
            "title": d.get("title"),
            "one_liner": d.get("one_liner"),
            "composite_score": d.get("composite_score"),
        })
        # Trim per-persona breakdown to keep the response payload small
        # enough for the MCP/Claude Code token budget. Full rationale strings
        # live in the LangGraph checkpoint (sqlite); fetch via paic_runs_resume
        # if a richer view is needed.
        breakdown_full = ps.get("persona_breakdown") or {}
        breakdown_slim = {
            persona: {
                "feasibility": v.get("feasibility"),
                "novelty": v.get("novelty"),
                "impact": v.get("impact"),
                "red_flags": [str(rf)[:200] for rf in (v.get("red_flags") or [])][:3],
                "rationale_brief": (v.get("rationale") or "")[:80],
            }
            for persona, v in breakdown_full.items()
        }
        drafts_with_scores.append({
            "idx": i,
            "title": d.get("title"),
            "one_liner": d.get("one_liner"),
            "feasibility": ps.get("feasibility_avg", d.get("feasibility_score", 0.0)),
            "novelty": ps.get("novelty_avg", d.get("novelty_score", 0.0)),
            "impact": ps.get("impact_avg", d.get("impact_score", 0.0)),
            "composite": ps.get("composite", d.get("composite_score", 0.0)),
            "panel_consensus": ps.get("panel_consensus"),
            "red_flags": [str(rf)[:200] for rf in (ps.get("red_flags") or [])][:5],
            "persona_breakdown": breakdown_slim,
        })

    return {
        "next_node": next_node,
        "is_paused": is_paused,
        "preview_ideas": preview_ideas,
        "drafts_with_scores": drafts_with_scores,
        "round": values.get("round", 1),
        "max_rounds": values.get("max_rounds", DEFAULT_MAX_ROUNDS),
        "round_limit_reached": values.get("round", 1) >= values.get("max_rounds", DEFAULT_MAX_ROUNDS),
        "history_summary": [
            {"round": h.get("round"), "n_drafts": h.get("n_drafts", 0), "kept_count": len(h.get("kept", []) or [])}
            for h in values.get("history", [])
        ],
        "finalized_ids": values.get("finalized_ids") or [],
        "interrupt_payload": interrupt_payload,
        "awaiting": awaiting,
        "host_directive": host_directive,
    }


def ideate_start(
    project_dir: str,
    focus: str | None = None,
    n_candidates: int = 8,
    paper_ids: list[str] | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    personas: list[str] | None = None,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Kick off an ideate run (§26 v2 multi-round + multi-persona panel)."""
    try:
        deps = _make_deps(project_dir, llm)
    except FileNotFoundError as exc:
        return {"error": "project_not_initialized", "detail": str(exc)}

    run_id = str(ULID())
    runs_registry.register(
        run_id=run_id,
        kind="ideate",
        project_dir=str(deps.paths.root),
        thread_id=run_id,
        status="running",
        current_node="gather_corpus",
    )

    try:
        graph = build_ideate_graph(deps)
        config = {"configurable": {"thread_id": run_id}}
        initial: dict[str, Any] = {
            "project_dir": str(deps.paths.root),
            "focus": focus,
            "n_candidates": n_candidates,
            "paper_ids": paper_ids,
            "max_rounds": max_rounds,
            "personas": personas or list(DEFAULT_PERSONAS),
            "run_id": run_id,
            "round": 0,  # bumped to 1 inside _brainstorm
        }
        graph.invoke(initial, config=config)
        snapshot = graph.get_state(config)
    except LLMUnavailable as exc:
        runs_registry.update(run_id, status="error", error=str(exc))
        return {"run_id": run_id, "error": "llm_unavailable", "detail": str(exc)}
    except Exception as exc:
        runs_registry.update(run_id, status="error", error=repr(exc))
        return {"run_id": run_id, "error": "graph_failed", "detail": repr(exc)}

    parsed = _interpret_state(snapshot)
    status = "awaiting_input" if parsed["is_paused"] else "done"
    runs_registry.update(run_id, status=status, current_node=parsed["next_node"])
    return {
        "run_id": run_id,
        "thread_id": run_id,
        "status": status,
        "current_node": parsed["next_node"],
        "round": parsed["round"],
        "max_rounds": parsed["max_rounds"],
        "round_limit_reached": parsed["round_limit_reached"],
        "preview_ideas": parsed["preview_ideas"],
        "drafts_with_scores": parsed["drafts_with_scores"],
        "history_summary": parsed["history_summary"],
        "interrupt_payload": parsed["interrupt_payload"],
        "awaiting": parsed["awaiting"],
        "host_directive": parsed["host_directive"],
        "finalized_ids": parsed["finalized_ids"],
    }


def ideate_step(
    project_dir: str,
    run_id: str,
    action: str | None = None,
    keep: list[int] | None = None,
    feedback: str | None = None,
    n_more: int | None = None,
    host_response: dict[str, Any] | None = None,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Resume an ideate run.

    §26 v2 actions: ``"finalize"`` / ``"regenerate"`` / ``"refine"``.

    Backward compat (§26.10 R92): callers from the v1 era that pass
    ``keep=[...], feedback="..."`` without ``action`` get an implicit
    ``action="finalize"``. ``feedback`` without ``action`` is ambiguous
    (could mean regenerate-with-feedback or finalize-with-comment) — return
    a structured error so the SKILL can re-prompt.
    """
    try:
        deps = _make_deps(project_dir, llm)
    except FileNotFoundError as exc:
        return {"error": "project_not_initialized", "detail": str(exc)}

    record = runs_registry.get(run_id)
    if record is None:
        return {"error": "run_not_found", "run_id": run_id}
    if record["kind"] != "ideate":
        return {"error": "wrong_kind", "expected": "ideate", "got": record["kind"]}

    # Host orchestration resume path: the Skill is supplying the host LLM's
    # output for a paused ``llm_or_interrupt`` call. Skip the action / keep
    # parsing — those are user-decision fields, distinct from host responses.
    if host_response is not None:
        from pydantic import ValidationError as _VE

        config = {"configurable": {"thread_id": record["thread_id"]}}
        graph = build_ideate_graph(deps)
        try:
            graph.invoke(Command(resume=host_response), config=config)
            snapshot = graph.get_state(config)
        except _VE as exc:
            paused = _interpret_state(graph.get_state(config))
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
        status = "awaiting_input" if parsed["is_paused"] else "done"
        runs_registry.update(run_id, status=status, current_node=parsed["next_node"])
        return {
            "run_id": run_id,
            "status": status,
            "current_node": parsed["next_node"],
            "round": parsed["round"],
            "max_rounds": parsed["max_rounds"],
            "round_limit_reached": parsed["round_limit_reached"],
            "preview_ideas": parsed["preview_ideas"],
            "drafts_with_scores": parsed["drafts_with_scores"],
            "history_summary": parsed["history_summary"],
            "interrupt_payload": parsed["interrupt_payload"],
            "awaiting": parsed["awaiting"],
            "host_directive": parsed["host_directive"],
            "finalized_ids": parsed["finalized_ids"],
        }

    # §26.10 R92 — legacy compat path
    if action is None:
        if feedback is not None and keep is None:
            return {
                "error": "ambiguous_legacy_call",
                "detail": (
                    "feedback without action is ambiguous. Pass "
                    "action='regenerate' (or 'refine' / 'finalize') explicitly."
                ),
            }
        action = "finalize"

    if action not in ("finalize", "regenerate", "refine"):
        return {
            "error": "invalid_action",
            "got": action,
            "valid_actions": ["finalize", "regenerate", "refine"],
        }
    if action == "refine" and not keep:
        return {
            "error": "refine_requires_keep",
            "detail": "action='refine' needs keep=[idx, ...] to indicate which drafts to use as seeds.",
        }

    config = {"configurable": {"thread_id": record["thread_id"]}}
    graph = build_ideate_graph(deps)

    resume_payload: dict[str, Any] = {"action": action}
    if keep is not None:
        resume_payload["keep"] = keep
    if feedback is not None:
        resume_payload["feedback"] = feedback
    if n_more is not None:
        resume_payload["n_more"] = n_more

    try:
        graph.invoke(Command(resume=resume_payload), config=config)
        snapshot = graph.get_state(config)
    except Exception as exc:
        runs_registry.update(run_id, status="error", error=repr(exc))
        return {"run_id": run_id, "error": "graph_failed", "detail": repr(exc)}

    parsed = _interpret_state(snapshot)
    status = "awaiting_input" if parsed["is_paused"] else "done"
    runs_registry.update(run_id, status=status, current_node=parsed["next_node"])
    out: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "current_node": parsed["next_node"],
        "round": parsed["round"],
        "max_rounds": parsed["max_rounds"],
        "round_limit_reached": parsed["round_limit_reached"],
        "preview_ideas": parsed["preview_ideas"],
        "drafts_with_scores": parsed["drafts_with_scores"],
        "history_summary": parsed["history_summary"],
        "interrupt_payload": parsed["interrupt_payload"],
        "awaiting": parsed["awaiting"],
        "host_directive": parsed["host_directive"],
        "finalized_ids": parsed["finalized_ids"],
    }
    # If the user tried to keep iterating but round limit hit, surface a warning.
    if action in ("regenerate", "refine") and parsed["round_limit_reached"] and status == "done":
        out["warning"] = "round_limit_reached_force_finalize"
    return out

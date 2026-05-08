"""``paic_draft_compose_v2`` — generator-evaluator 4-call compose (ARS-fusion P1-1).

Wraps :func:`paic.latex.compose_v2.run_4call_compose` for the MCP
surface. Loads project context (paper_plan / idea / experiment / library
cite_keys), constructs the SprintContract, runs all 4 phases, returns
the full audit trail. By default writes the writer's draft (Phase 4b
output) to disk via the same persistence path as the v1 compose tool —
pass ``dry_run=True`` to skip the write and only return the audit JSON.

V1.0 design decision §2: ``strict=True`` (4-call) is the default;
``strict=False`` falls back to v1 single-call compose for cost-sensitive
iterations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paic.latex.compose_v2 import build_contract, run_4call_compose
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml

DEFAULT_TARGET_WORDS: dict[str, int] = {
    "00_abstract": 200,
    "01_intro": 800,
    "02_related": 700,
    "03_method": 900,
    "04_experiments": 800,
    "05_conclusion": 220,
}


def draft_compose_v2_tool(
    project_dir: str,
    section: str,
    *,
    idea_id: str | None = None,
    experiment_id: str | None = None,
    target_words: int | None = None,
    instruction: str | None = None,
    strict: bool = True,
    cross_model_evaluator: bool = False,
    write: bool = False,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Run the 4-call generator-evaluator compose pipeline.

    Args:
        section: section identifier (canonical / alias / path; same as
            v1 ``paic_draft_compose``)
        strict: when False, fall back to v1 single-call compose (returns
            ``{degraded: true}`` so caller knows). Default True per V1.0
            design decision §2 — quality optimisation, ~3× cost.
        cross_model_evaluator: when True, the evaluator phases (6a + 6b)
            use a different LLM backend than the writer phases. P2-3
            integration: requires user to configure the second backend.
        write: when True, the writer's Phase 4b ``composed_text`` is
            written to disk via the same path as the v1 persist tool.
            Default False (audit-only run); set True after the user
            reviews the EvaluatorDecision and confirms the draft is
            ready to land.

    Returns:
        On strict=True success: ``{contract, writer_commitment,
        writer_decision, evaluator_rubric, evaluator_decision,
        divergences, cross_model_used, written_to?}``.
        On strict=False: returns the v1 ``draft_compose_tool`` shape +
        ``{degraded: true}``.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    if not strict:
        from paic.mcp_server.tools.draft import draft_compose_tool
        out = draft_compose_tool(
            project_dir,
            section,
            mode="from_stub",
            idea_id=idea_id,
            experiment_id=experiment_id,
            target_words=target_words,
            instruction=instruction,
            dry_run=not write,
        )
        out["degraded"] = True
        out["degraded_reason"] = "strict=False — fell back to v1 single-call compose"
        return out

    # Strict path — run the 4-phase pipeline
    from paic.latex.compose import _section_canonical_name
    from paic.latex.polish import resolve_section_path

    target = resolve_section_path(paths, section)
    if not target.is_file():
        return {
            "error": "section_not_found",
            "section": section,
            "looked_at": str(target),
        }
    section_name = _section_canonical_name(target)
    section_type = section_name.split("_", 1)[1] if "_" in section_name else section_name

    target_words_resolved = target_words or DEFAULT_TARGET_WORDS.get(section_name, 600)

    # Load project context excerpts (capped to keep prompts tight)
    paper_plan = _safe_load_dict(paths.paper_plan_yaml)
    idea = _safe_load_dict(paths.ideas_dir / f"{idea_id}.yaml") if idea_id else None
    experiment = (
        _safe_load_dict(paths.experiments_dir / f"{experiment_id}.yaml")
        if experiment_id
        else None
    )

    library_cite_keys = sorted(_load_library_cite_keys(paths))
    if not library_cite_keys and section_name in {"01_intro", "02_related", "04_experiments"}:
        return {
            "error": "empty_library",
            "section_name": section_name,
            "hint": (
                "/paic-draft compose v2 requires non-empty library for the "
                "cite-aware sections. Run /paic-search + /paic-ingest first."
            ),
        }

    contract = build_contract(
        section_name=section_name,
        section_type=section_type,
        target_words=target_words_resolved,
        library_cite_keys=library_cite_keys,
        paper_plan_excerpt=_excerpt_paper_plan(paper_plan, section_name),
        idea_excerpt=_excerpt_idea(idea),
        experiment_excerpt=_excerpt_experiment(experiment),
        instruction=instruction,
    )

    client = llm or get_default_client()

    eval_client: LLMClient | None = None
    if cross_model_evaluator:
        # P2-3: caller is asking for cross-model verification. The
        # selection mechanism is intentionally minimal in V1.0 — we
        # simply construct a fresh client (router will pick the
        # configured evaluator nodes' backends, which the user can route
        # to a different profile via routing.overrides).
        eval_client = LLMClient(cfg=client.cfg, model=client.model)

    try:
        result = run_4call_compose(
            contract=contract,
            client=client,
            cross_model_evaluator_client=eval_client,
        )
    except LLMUnavailable as exc:
        return {"error": "llm_unavailable", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": "compose_v2_failed", "detail": repr(exc)}

    if write:
        from paic.latex.compose import persist_composed
        from paic.latex.polish import _hash
        original = target.read_text(encoding="utf-8")
        persist_out = persist_composed(
            paths,
            section=section,
            composed=result["writer_decision"]["composed_text"],
            original_hash=_hash(original),
        )
        result["written"] = persist_out

    return result


# ----------------------------------------------------- helpers


def _safe_load_dict(path: Path) -> dict | None:
    if not path.is_file():
        return None
    raw = load_yaml(path)
    return raw if isinstance(raw, dict) else None


def _load_library_cite_keys(paths) -> set[str]:
    selected = _safe_load_dict(paths.selected_yaml) or {}
    from paic.latex.filler import _cite_key
    return {_cite_key(p) for p in (selected.get("papers") or []) if isinstance(p, dict)}


def _excerpt_paper_plan(plan: dict | None, section_name: str) -> dict | None:
    if not plan:
        return None
    out: dict[str, Any] = {
        "thesis": plan.get("thesis"),
        "contributions": plan.get("contributions") or [],
    }
    # Section-specific intent
    for entry in plan.get("section_plan") or []:
        if isinstance(entry, dict) and entry.get("name") == section_name:
            out["section_intent"] = entry.get("intent")
            out["target_words"] = entry.get("target_words")
            break
    terminology = plan.get("terminology") or {}
    if isinstance(terminology, dict):
        out["terminology"] = list(terminology.keys())[:30]
    return out


def _excerpt_idea(idea: dict | None) -> dict | None:
    if not idea:
        return None
    return {
        "title": idea.get("title"),
        "summary": idea.get("summary"),
        "key_claims": idea.get("key_claims") or [],
    }


def _excerpt_experiment(exp: dict | None) -> dict | None:
    if not exp:
        return None
    return {
        "id": exp.get("id"),
        "research_questions": exp.get("research_questions") or [],
        "datasets": [
            d.get("name") if isinstance(d, dict) else d
            for d in (exp.get("datasets") or [])
        ],
        "baselines": [
            b.get("name") if isinstance(b, dict) else b
            for b in (exp.get("baselines") or [])
        ],
        "metrics": [
            m.get("name") if isinstance(m, dict) else m
            for m in (exp.get("metrics") or [])
        ],
    }

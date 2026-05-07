"""``paic_library_retrieve`` — section-aware BM25 retrieval over the project library."""

from __future__ import annotations

from typing import Any

from paic.library.retrieval import LibraryRetriever, build_query
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml


def library_retrieve_tool(
    project_dir: str,
    query: str | None = None,
    section: str | None = None,
    idea_id: str | None = None,
    experiment_id: str | None = None,
    extra: str | None = None,
    k: int = 12,
    mmr_lambda: float = 0.7,
) -> dict[str, Any]:
    """Retrieve top-k library papers for a section-targeted query.

    Either pass an explicit ``query`` (free-form), or pass ``section`` (and
    optionally ``idea_id`` / ``experiment_id``) so the tool builds a
    section-aware query from the paper plan + idea + experiment artifacts
    automatically.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    retriever = LibraryRetriever.build(paths)
    if len(retriever) == 0:
        return {
            "error": "library_empty",
            "hint": "Run /paic-search and /paic-ingest before retrieving.",
        }

    final_query = query
    if not final_query:
        if not section:
            return {
                "error": "no_query",
                "hint": "Pass either an explicit `query` or a `section` (with optional idea_id / experiment_id).",
            }
        plan: dict[str, Any] | None = None
        if paths.paper_plan_yaml.is_file():
            loaded = load_yaml(paths.paper_plan_yaml)
            if isinstance(loaded, dict):
                plan = loaded
        idea: dict[str, Any] | None = None
        if idea_id:
            idea_path = paths.ideas_dir / f"{idea_id}.yaml"
            if idea_path.is_file():
                loaded = load_yaml(idea_path)
                if isinstance(loaded, dict):
                    idea = loaded
        experiment: dict[str, Any] | None = None
        if experiment_id:
            exp_path = paths.experiments_dir / f"{experiment_id}.yaml"
            if exp_path.is_file():
                loaded = load_yaml(exp_path)
                if isinstance(loaded, dict):
                    experiment = loaded
        final_query = build_query(
            section,
            paper_plan=plan,
            idea=idea,
            experiment=experiment,
            extra=extra,
        )
        if not final_query:
            return {
                "error": "empty_query",
                "hint": "section + plan + idea + experiment yielded no query tokens; pass an explicit query.",
            }

    hits = retriever.retrieve(final_query, k=k, mmr_lambda=mmr_lambda)
    return {
        "query": final_query,
        "library_size": len(retriever),
        "k": k,
        "hits": [
            {
                "cite_key": h.cite_key,
                "score": round(h.score, 4),
                "match_reason": h.match_reason,
                "snippet": h.snippet,
                "title": h.paper.get("title"),
                "year": h.paper.get("year"),
                "authors": h.paper.get("authors"),
            }
            for h in hits
        ],
    }

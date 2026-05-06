"""Library tools: ``paic_s2_search`` / ``paic_dedupe`` / ``paic_library_add``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from paic.schemas.paper import PaperRef
from paic.sources.dedupe import dedupe
from paic.sources.semanticscholar import search_papers as s2_search
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml, save_yaml


def s2_search_tool(
    query: str,
    limit: int = 20,
    year_from: int | None = None,
    fields_of_study: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    result = s2_search(
        query,
        limit=limit,
        year_from=year_from,
        fields_of_study=fields_of_study,
        force=force,
    )
    return {
        "results": [p.model_dump(mode="json") for p in result.papers],
        "total": result.total,
        "from_cache": result.from_cache,
    }


def dedupe_tool(papers: list[dict[str, Any]]) -> dict[str, Any]:
    refs = [PaperRef.model_validate(p) for p in papers]
    res = dedupe(refs)
    return {
        "unique": [p.model_dump(mode="json") for p in res.unique],
        "duplicate_groups": res.duplicate_groups,
    }


def library_add_tool(
    project_dir: str,
    papers: list[dict[str, Any]],
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Add papers to the project library, deduplicating against existing entries."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    # Existing entries
    selected = load_yaml(paths.selected_yaml) or {}
    if not isinstance(selected, dict):
        selected = {}
    existing: list[dict[str, Any]] = list(selected.get("papers") or [])
    existing_keys = _collect_id_keys(existing)

    added: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    timestamp = datetime.now(UTC).isoformat()

    for raw in papers:
        ref = PaperRef.model_validate(raw)
        keys = _ref_id_keys(ref)
        if keys & existing_keys:
            skipped.append(ref.model_dump(mode="json"))
            continue
        record = ref.model_dump(mode="json")
        record["tags"] = list(tags or [])
        record["added_at"] = timestamp
        existing.append(record)
        existing_keys |= keys
        added.append(record)

    selected["papers"] = existing
    save_yaml(paths.selected_yaml, selected)
    return {"added": added, "skipped_duplicates": skipped, "library_count": len(existing)}


# --- helpers ---------------------------------------------------------------

def _ref_id_keys(ref: PaperRef) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if ref.arxiv_id:
        keys.add(("arxiv", ref.arxiv_id.strip().lower()))
    if ref.doi:
        keys.add(("doi", ref.doi.strip().lower()))
    if ref.s2_id:
        keys.add(("s2", ref.s2_id.strip().lower()))
    return keys


_FIELD_TO_KIND = {"arxiv_id": "arxiv", "doi": "doi", "s2_id": "s2"}


def _collect_id_keys(records: list[dict[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for r in records:
        for field, kind in _FIELD_TO_KIND.items():
            v = r.get(field)
            if v:
                keys.add((kind, v.strip().lower()))
    return keys

"""Library tools: ``paic_s2_search`` / ``paic_dedupe`` / ``paic_library_add``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paic.latex.filler import _cite_key
from paic.library.chunker import chunk_index_build
from paic.schemas.paper import PaperRef
from paic.sources.dedupe import dedupe
from paic.sources.semanticscholar import search_papers as s2_search
from paic.workspace.paths import ProjectPaths, resolve_project
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

    # Surface the entries with no local PDF (typical for s2-only or DOI-only
    # papers that don't have an auto-download path). The /paic-ingest SKILL
    # prints these as "未下载: N 篇（仅元数据）" so users know which papers
    # will fall back to abstract-only summarize.
    papers_without_pdf = [
        {
            "title": r.get("title"),
            "doi": r.get("doi"),
            "arxiv_id": r.get("arxiv_id"),
            "s2_id": r.get("s2_id"),
            "source": r.get("source"),
        }
        for r in added
        if not r.get("pdf_local_path")
    ]
    return {
        "added": added,
        "skipped_duplicates": skipped,
        "library_count": len(existing),
        "papers_without_pdf": papers_without_pdf,
    }


# --- chunk reindex (P0 #1: chunk-level grounding) --------------------------


def _paper_markdown_path(paths: ProjectPaths, paper: dict[str, Any], cite_key: str) -> Path | None:
    """Resolve the on-disk markdown body for one library paper.

    Order of preference:
    1. ``pdf_local_path`` when it ends in ``.md`` (set by ingest for the
       human-readable ``NNN_title.md`` naming scheme).
    2. ``library/pdfs/<cite_key>.md`` (legacy slug; what /paic-ingest writes
       when ``display_name`` isn't passed).

    Returns None when neither file exists. We deliberately do NOT extract
    from PDF here — chunk reindex is a post-summarize hook, and summarize
    already does the heavy PDF-to-markdown work + caches the result.
    """
    pdfs_dir = paths.pdfs_dir
    explicit = paper.get("pdf_local_path") if isinstance(paper, dict) else None
    if isinstance(explicit, str) and explicit:
        primary = pdfs_dir / explicit
        if primary.suffix.lower() == ".md" and primary.is_file():
            return primary
    fallback = pdfs_dir / f"{cite_key}.md"
    if fallback.is_file():
        return fallback
    return None


def library_reindex_chunks_tool(
    project_dir: str,
    *,
    target_tokens: int = 400,
    overlap_tokens: int = 80,
) -> dict[str, Any]:
    """Build chunk indices for every library paper that has a markdown body.

    For each paper in ``selected.yaml``: locate the markdown file
    (``library/pdfs/<cite_key>.md`` or ``pdf_local_path``), chunk it
    with :func:`paic.library.chunker.chunk_paper_markdown`, and persist
    to ``library/chunks/<cite_key>.json``.

    Used by:
    - ``/paic-ingest`` after a bulk import — gets the chunk index built
      once, before compose runs depend on it.
    - One-shot migration for older projects (created before P0 #1
      shipped). Idempotent: running it twice produces the same indices.

    Returns ``{library_count, indexed, skipped, total_chunks, chunks_dir}``.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    selected = load_yaml(paths.selected_yaml) or {}
    papers = list(selected.get("papers") or []) if isinstance(selected, dict) else []
    if not papers:
        return {
            "error": "library_empty",
            "hint": "Run /paic-search and /paic-ingest before reindexing chunks.",
        }

    indexed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total_chunks = 0
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        cite_key = _cite_key(paper)
        md_path = _paper_markdown_path(paths, paper, cite_key)
        if md_path is None:
            skipped.append({"cite_key": cite_key, "reason": "no_markdown_body"})
            continue
        try:
            body = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            skipped.append({"cite_key": cite_key, "reason": f"read_error:{exc}"})
            continue
        chunks = chunk_index_build(
            paths,
            cite_key,
            body,
            target_tokens=target_tokens,
            overlap_tokens=overlap_tokens,
        )
        if not chunks:
            skipped.append({"cite_key": cite_key, "reason": "empty_after_chunking"})
            continue
        indexed.append({
            "cite_key": cite_key,
            "chunks": len(chunks),
            "source": str(md_path),
        })
        total_chunks += len(chunks)

    return {
        "library_count": len(papers),
        "indexed": indexed,
        "skipped": skipped,
        "total_chunks": total_chunks,
        "chunks_dir": str(paths.library_dir / "chunks"),
    }


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

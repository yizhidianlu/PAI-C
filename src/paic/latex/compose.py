"""LaTeX section compose — v0.3 (§21).

Where polish (§20) rewrites an existing section, compose generates a section
from scratch (or from a stub) using the project's idea + experiment + library.
The LLM is told which cite keys are available and must pick from that
whitelist; the guard rejects any citation not in the library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paic.latex.filler import _cite_key
from paic.latex.guard import (
    strip_markdown_fence,
    validate_composed,
)
from paic.latex.polish import (
    _backup_path,
    _hash,
    _make_diff,
    resolve_section_path,
)
from paic.llm.prompts import load_prompt
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml

VALID_MODES: tuple[str, ...] = ("from_stub", "from_scratch", "paragraph")

# Cap library context at 40 papers. Prompts grow large fast (each entry ~150
# chars + summary), and most projects have ≤30 selected papers anyway. When
# there are more, we drop in insertion order — users who want different
# selection order should re-rank library/selected.yaml manually.
MAX_LIBRARY_PAPERS_IN_PROMPT = 40

# Per-section default word targets (used when the caller doesn't pass
# target_words). Surfaced to the LLM via the prompt; non-binding.
_DEFAULT_TARGET_WORDS: dict[str, int] = {
    "00_abstract": 200,
    "01_intro": 800,
    "02_related": 700,
    "03_method": 900,
    "04_experiments": 800,
    "05_conclusion": 220,
}


def _section_canonical_name(target: Path) -> str:
    """Strip ``.tex`` and any prefix to get e.g. ``01_intro``."""
    return target.stem


def _summary_one_liner(summary_md_path: Path | None, abstract: str | None) -> str:
    """Pick the most informative one-line description of a paper.

    Prefer the first non-trivial line of the structured summary markdown;
    fall back to the abstract's first 160 chars; if neither, "(no summary)".
    """
    if summary_md_path and summary_md_path.is_file():
        try:
            for line in summary_md_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                # Skip headers (# / ##), list markers (- / *), and blank lines
                if not stripped or stripped.startswith(("#", "-", "*", ">", "```")):
                    continue
                return stripped[:200]
        except OSError:
            pass
    if abstract:
        condensed = " ".join(abstract.split())
        return condensed[:160]
    return "(no summary)"


def _build_library_context(
    paths: ProjectPaths,
    *,
    section_name: str | None = None,
    paper_plan: dict[str, Any] | None = None,
    idea: dict[str, Any] | None = None,
    experiment: dict[str, Any] | None = None,
) -> tuple[str, set[str], bool]:
    """Build the markdown bullet list + the cite_key whitelist for the prompt.

    Returns ``(markdown_text, cite_key_set, retrieval_used)``. ``markdown_text``
    is empty when the library is empty.

    When the library exceeds ``MAX_LIBRARY_PAPERS_IN_PROMPT`` and a
    ``section_name`` is given, switches to BM25 retrieval (§quality phase 2)
    so the prompt picks the most relevant ``MAX_LIBRARY_PAPERS_IN_PROMPT``
    papers for that section instead of the first N from insertion order.
    The ``cite_key`` whitelist always includes the **entire** library — the
    LLM may still cite anything in the project, the bullet list just
    surfaces the most relevant subset.
    """
    selected = load_yaml(paths.selected_yaml) or {}
    papers = list(selected.get("papers") or []) if isinstance(selected, dict) else []
    if not papers:
        return ("", set(), False)

    # Whitelist always covers every library paper — retrieval only shapes
    # the bullet list shown in the prompt.
    full_cite_keys: set[str] = {_cite_key(p) for p in papers}

    retrieval_used = False
    capped: list[dict[str, Any]]
    if (
        section_name
        and len(papers) > MAX_LIBRARY_PAPERS_IN_PROMPT
    ):
        try:
            from paic.library.retrieval import LibraryRetriever, build_query
            retriever = LibraryRetriever.build(paths)
            query = build_query(
                section_name,
                paper_plan=paper_plan,
                idea=idea,
                experiment=experiment,
            )
            if query and len(retriever) > 0:
                hits = retriever.retrieve(query, k=MAX_LIBRARY_PAPERS_IN_PROMPT)
                if hits:
                    capped = [h.paper for h in hits]
                    retrieval_used = True
                else:
                    capped = papers[:MAX_LIBRARY_PAPERS_IN_PROMPT]
            else:
                capped = papers[:MAX_LIBRARY_PAPERS_IN_PROMPT]
        except Exception:  # noqa: BLE001 — fall back to legacy behavior on any retrieval failure
            capped = papers[:MAX_LIBRARY_PAPERS_IN_PROMPT]
    else:
        # Cap by insertion order — users can re-rank by editing selected.yaml.
        capped = papers[:MAX_LIBRARY_PAPERS_IN_PROMPT]

    cite_keys: set[str] = set()
    bullets: list[str] = []
    for record in capped:
        key = _cite_key(record)
        cite_keys.add(key)
        title = record.get("title") or "Untitled"
        authors = record.get("authors") or []
        first_author = authors[0].split()[-1] if authors else "Anonymous"
        year = record.get("year") or "n.d."
        # Match the summarize tool's filename convention: cite_key (canonical,
        # filesystem-safe — same key used by library/pdfs/<cite_key>.{md,pdf}).
        # Legacy fall-back: pre-fix arxiv summaries lived at <arxiv_id>.md
        # (the unmodified user input). Probe both so older libraries keep
        # rendering correctly until they're re-summarized.
        summary_path = paths.summaries_dir / f"{key}.md"
        if not summary_path.exists():
            legacy_id = (
                record.get("arxiv_id")
                or record.get("doi")
                or record.get("s2_id")
                or title
            )
            legacy_path = paths.summaries_dir / f"{legacy_id}.md"
            if legacy_path.exists():
                summary_path = legacy_path
        one_liner = _summary_one_liner(summary_path, record.get("abstract"))
        bullets.append(
            f"- [{key}] {title} ({first_author} et al., {year}) — {one_liner}"
        )

    overflow_note = ""
    if len(papers) > MAX_LIBRARY_PAPERS_IN_PROMPT:
        if retrieval_used:
            overflow_note = (
                f"\n(NOTE: {len(papers)} papers in library; the {MAX_LIBRARY_PAPERS_IN_PROMPT} "
                f"most relevant for this section are listed — others remain citable.)"
            )
        else:
            overflow_note = (
                f"\n(NOTE: {len(papers)} papers in library; only the first "
                f"{MAX_LIBRARY_PAPERS_IN_PROMPT} are listed here.)"
            )
    # Whitelist returned to caller is the **full** library so the cite_key
    # validator doesn't reject papers outside the bullet list.
    return ("\n".join(bullets) + overflow_note, full_cite_keys, retrieval_used)


def _format_paper_plan(plan: dict[str, Any], section_name: str) -> str:
    """Render the plan as a prompt-ready block, focused on what's relevant
    to ``section_name``. Returns empty string if plan has no useful content."""
    lines: list[str] = []
    if thesis := plan.get("thesis"):
        lines.append(f"  - thesis: {thesis}")
    if venue := plan.get("target_venue"):
        lines.append(f"  - target_venue: {venue}")
    if audience := plan.get("audience"):
        lines.append(f"  - audience: {audience}")
    if contributions := plan.get("contributions"):
        lines.append("  - contributions:")
        for c in contributions:
            cid = c.get("id") if isinstance(c, dict) else None
            title = c.get("title") if isinstance(c, dict) else None
            desc = c.get("description") if isinstance(c, dict) else None
            if cid and title:
                lines.append(f"      [{cid}] {title}: {desc or ''}")
    # Section-targeted slice: surface only the section_plan entry for this section
    section_entries = plan.get("section_plan") or []
    for entry in section_entries:
        if isinstance(entry, dict) and entry.get("name") == section_name:
            lines.append(f"  - this section's intent: {entry.get('intent', '')}")
            supports = entry.get("supports_contributions") or []
            if supports:
                lines.append(f"  - this section supports contributions: {supports}")
            break
    if terminology := plan.get("terminology"):
        if isinstance(terminology, dict) and terminology:
            lines.append("  - terminology (use these exact phrases):")
            for term, defn in terminology.items():
                lines.append(f"      \"{term}\": {defn}")
    if symbols := plan.get("symbols"):
        if isinstance(symbols, dict) and symbols:
            lines.append("  - symbols:")
            for sym, meaning in symbols.items():
                lines.append(f"      {sym}: {meaning}")
    return "\n".join(lines)


def _format_user_prompt(
    *,
    section_name: str,
    mode: str,
    instruction: str | None,
    target_words: int | None,
    section_stub: str,
    idea: dict[str, Any] | None,
    experiment: dict[str, Any] | None,
    library_md: str,
    paper_plan: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = [f"Section: {section_name}", f"Mode: {mode}"]
    target = target_words or _DEFAULT_TARGET_WORDS.get(section_name)
    if target:
        parts.append(f"Target length: ~{target} words (soft target)")
    if instruction:
        parts.append(f"Additional instruction: {instruction.strip()}")

    if paper_plan:
        plan_md = _format_paper_plan(paper_plan, section_name)
        if plan_md:
            parts.append("")
            parts.append("Paper Plan (the global thesis this section must serve):")
            parts.append(plan_md)

    parts.append("")
    parts.append("Idea:")
    if idea:
        for key in (
            "title", "one_liner", "motivation", "proposed_approach",
            "novelty_claim", "expected_contribution",
        ):
            value = idea.get(key)
            if value:
                parts.append(f"  - {key}: {value}")
        if idea.get("grounded_in"):
            parts.append(f"  - grounded_in: {idea['grounded_in']}")
    else:
        parts.append("  (no idea provided)")

    parts.append("")
    parts.append("Experiment:")
    if experiment:
        for key in (
            "research_questions", "hypotheses", "proposed_method",
            "datasets", "baselines", "metrics", "ablations",
            "success_criteria", "compute_budget",
        ):
            value = experiment.get(key)
            if value:
                parts.append(f"  - {key}: {value}")
    else:
        parts.append("  (no experiment provided)")

    parts.append("")
    parts.append("Library available for citation (use ONLY these `\\cite{}` keys):")
    if library_md:
        parts.append(library_md)
    else:
        parts.append("  (library is empty — do not use \\cite{} at all)")

    parts.append("")
    parts.append("Section stub / outline (for from_stub mode):")
    parts.append("```latex")
    parts.append(section_stub if section_stub.strip() else "(empty — section file is blank)")
    parts.append("```")

    parts.append("")
    parts.append("Output the composed LaTeX section only. No fences, no commentary.")
    return "\n".join(parts)


def compose_section(
    paths: ProjectPaths,
    *,
    section: str,
    mode: str = "from_stub",
    idea_id: str | None = None,
    experiment_id: str | None = None,
    target_words: int | None = None,
    instruction: str | None = None,
    dry_run: bool = False,
    llm=None,
) -> dict[str, Any]:
    """End-to-end compose: load idea + experiment + library, call LLM,
    validate cite keys + structure, write back + ``.bak`` backup.

    Returns the standard compose payload (see plan §21.4) on success or a
    structured error dict (``section_not_found`` / ``invalid_mode`` /
    ``empty_library`` / ``llm_unavailable`` / ``latex_validation_failed``)
    on failure.
    """
    if mode not in VALID_MODES:
        return {
            "error": "invalid_mode",
            "got": mode,
            "valid_modes": list(VALID_MODES),
        }

    target = resolve_section_path(paths, section)
    if not target.is_file():
        return {
            "error": "section_not_found",
            "section": section,
            "looked_at": str(target),
            "hint": (
                "Run paic_draft_fill first to create the section file, "
                "or pass an existing section name."
            ),
        }

    section_name = _section_canonical_name(target)
    original = target.read_text(encoding="utf-8")
    section_stub = "" if mode == "from_scratch" else original

    idea = experiment = None
    if idea_id:
        idea_path = paths.ideas_dir / f"{idea_id}.yaml"
        if idea_path.is_file():
            idea = load_yaml(idea_path)
    if experiment_id:
        exp_path = paths.experiments_dir / f"{experiment_id}.yaml"
        if exp_path.is_file():
            experiment = load_yaml(exp_path)

    paper_plan: dict[str, Any] | None = None
    if paths.paper_plan_yaml.is_file():
        loaded = load_yaml(paths.paper_plan_yaml)
        if isinstance(loaded, dict):
            paper_plan = loaded

    library_md, library_keys, retrieval_used = _build_library_context(
        paths,
        section_name=section_name,
        paper_plan=paper_plan,
        idea=idea,
        experiment=experiment,
    )

    # Abstract / conclusion typically have no cites — empty library is OK.
    # Other sections benefit from at least a few; warn but don't abort.
    library_size = len(library_keys)
    if not library_keys and section_name in {"01_intro", "02_related", "04_experiments"}:
        return {
            "error": "empty_library",
            "section": str(target),
            "section_name": section_name,
            "hint": (
                "compose for this section needs library papers to cite. "
                "Run /paic-search and /paic-ingest first."
            ),
        }

    system_prompt = load_prompt("compose_section")
    user_prompt = _format_user_prompt(
        section_name=section_name,
        mode=mode,
        instruction=instruction,
        target_words=target_words,
        section_stub=section_stub,
        idea=idea,
        experiment=experiment,
        library_md=library_md,
        paper_plan=paper_plan,
    )

    if llm is None:
        from paic.llm.backends.base import LLMUnavailable
        from paic.llm.client import get_default_client
        try:
            llm = get_default_client()
        except LLMUnavailable as exc:
            return {"error": "llm_unavailable", "detail": str(exc)}

    if mode == "paragraph":
        # §quality phase 6 — outline → write → polish pipeline.
        from paic.latex.paragraph_compose import compose_section_paragraphs
        from paic.library.claims import load_ledger
        from paic.library.retrieval import LibraryRetriever, build_query
        retriever = LibraryRetriever.build(paths)
        retrieval_hits: list[dict[str, Any]] = []
        if len(retriever) > 0:
            query = build_query(
                section_name,
                paper_plan=paper_plan,
                idea=idea,
                experiment=experiment,
            )
            if query:
                hits = retriever.retrieve(query, k=20)
                retrieval_hits = [
                    {
                        "cite_key": h.cite_key,
                        "score": h.score,
                        "snippet": h.snippet,
                        "title": h.paper.get("title"),
                        "match_reason": h.match_reason,
                    }
                    for h in hits
                ]
        ledger = load_ledger(paths)
        claims_dump = [c.model_dump(mode="json") for c in ledger.claims]
        try:
            result = compose_section_paragraphs(
                section=section_name,
                paper_plan=paper_plan,
                idea=idea,
                experiment=experiment,
                retrieval_hits=retrieval_hits,
                claims=claims_dump,
                target_words=(target_words or _DEFAULT_TARGET_WORDS.get(section_name, 800)),
                llm=llm,
                instruction=instruction,
            )
        except Exception as exc:
            from paic.llm.backends.base import LLMUnavailable
            if isinstance(exc, LLMUnavailable):
                return {"error": "llm_unavailable", "detail": str(exc)}
            raise
        composed = strip_markdown_fence(result.section_text).strip() + "\n"
        paragraph_count = len(result.paragraphs)
        spec_count = len(result.specs)
    else:
        try:
            response = llm.complete(
                system=system_prompt,
                user=user_prompt,
                max_tokens=4096,
                temperature=0.4,
                node="draft_compose",
            )
        except Exception as exc:
            from paic.llm.backends.base import LLMUnavailable
            if isinstance(exc, LLMUnavailable):
                return {"error": "llm_unavailable", "detail": str(exc)}
            raise
        composed = strip_markdown_fence(response.text).strip() + "\n"
        paragraph_count = 0
        spec_count = 0

    report = validate_composed(composed, library_keys)
    if not report.ok:
        return {
            "error": "latex_validation_failed",
            "section": str(target),
            "section_name": section_name,
            "mode": mode,
            "validation": report.to_dict(),
            "cite_keys_missing_from_library": report.cite_keys_missing,
            "library_size": library_size,
            "composed_preview": composed[:1500],
            "hint": (
                "The model's output failed structural checks. If "
                "cite_keys_missing is non-empty, the LLM cited a paper not "
                "in your library — ingest it first or re-run compose. "
                "Original file untouched."
            ),
        }

    diff = _make_diff(original, composed, target.name)
    original_hash = _hash(original)
    cite_keys_used = sorted(set(_extract_used_cite_keys(composed)))

    # Phase 4 — extract claims from the composed text and merge into
    # claims.yaml. Best-effort: failures are not fatal, just suppress the
    # claim-related fields in the payload so the SKILL knows extraction
    # didn't run.
    new_claims_summary: list[dict[str, Any]] | None = None
    try:
        from paic.library.claims import (
            extract_claims_from_section,
            load_ledger,
            merge_claims,
            save_ledger,
        )
        from paic.schemas.claim import ClaimsLedger
        new_claims = extract_claims_from_section(
            composed, section_name, llm=llm,
        )
        if new_claims:
            existing = load_ledger(paths)
            merged = merge_claims(existing.claims, new_claims)
            save_ledger(paths, ClaimsLedger(claims=merged))
            new_claims_summary = [
                {
                    "id": c.id,
                    "type": c.type,
                    "status": c.status,
                    "text": c.text,
                }
                for c in new_claims
                if c.type in {"novelty", "comparative", "numeric", "result"}
                and c.status == "needs_evidence"
            ]
    except Exception:  # noqa: BLE001 — claim extraction is non-blocking
        new_claims_summary = None

    payload: dict[str, Any] = {
        "section": str(target),
        "section_name": section_name,
        "mode": mode,
        "instruction": instruction,
        "target_words": target_words or _DEFAULT_TARGET_WORDS.get(section_name),
        "original": original,
        "original_hash": original_hash,
        "composed": composed,
        "diff": diff,
        "cite_keys_used": cite_keys_used,
        "cite_keys_missing_from_library": [],
        "validation": report.to_dict(),
        "library_size": library_size,
        "paper_plan_used": paper_plan is not None,
        "retrieval_used": retrieval_used,
        "claims_needs_evidence_strong": new_claims_summary,
        "paragraph_count": paragraph_count,
        "outline_spec_count": spec_count,
        "wrote": False,
        "backup_path": None,
    }

    if dry_run:
        return payload

    backup = _backup_path(target)
    backup.write_text(original, encoding="utf-8")
    target.write_text(composed, encoding="utf-8")
    payload["wrote"] = True
    payload["backup_path"] = str(backup)
    return payload


def persist_composed(
    paths: ProjectPaths,
    *,
    section: str,
    composed: str,
    original_hash: str,
) -> dict[str, Any]:
    """Host-orchestration counterpart of ``compose_section``.

    Re-reads the file (refusing if its sha256 has drifted from
    ``original_hash``), validates against the library cite-key set, then
    writes + backs up.
    """
    target = resolve_section_path(paths, section)
    if not target.is_file():
        return {
            "error": "section_not_found",
            "section": section,
            "looked_at": str(target),
        }

    original = target.read_text(encoding="utf-8")
    current_hash = _hash(original)
    if current_hash != original_hash:
        return {
            "error": "original_hash_mismatch",
            "section": str(target),
            "expected_hash": original_hash,
            "current_hash": current_hash,
            "hint": (
                "Section file changed since paic_draft_compose was called. "
                "Re-run compose to pick up the current contents."
            ),
        }

    _, library_keys, _ = _build_library_context(paths)
    composed_clean = strip_markdown_fence(composed).strip() + "\n"
    report = validate_composed(composed_clean, library_keys)
    if not report.ok:
        return {
            "error": "latex_validation_failed",
            "section": str(target),
            "validation": report.to_dict(),
            "cite_keys_missing_from_library": report.cite_keys_missing,
            "hint": "Composed text failed structural validation. Original file untouched.",
        }

    diff = _make_diff(original, composed_clean, target.name)
    cite_keys_used = sorted(set(_extract_used_cite_keys(composed_clean)))
    backup = _backup_path(target)
    backup.write_text(original, encoding="utf-8")
    target.write_text(composed_clean, encoding="utf-8")

    return {
        "section": str(target),
        "diff": diff,
        "validation": report.to_dict(),
        "cite_keys_used": cite_keys_used,
        "backup_path": str(backup),
        "wrote": True,
    }


def _extract_used_cite_keys(text: str) -> list[str]:
    """Flat list of cite keys actually referenced in ``text``."""
    from paic.latex.guard import extract_cite_keys
    return list(extract_cite_keys(text).keys())

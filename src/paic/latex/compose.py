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

VALID_MODES: tuple[str, ...] = ("from_stub", "from_scratch")

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


def _build_library_context(paths: ProjectPaths) -> tuple[str, set[str]]:
    """Build the markdown bullet list + the cite_key whitelist for the prompt.

    Returns ``(markdown_text, cite_key_set)``. ``markdown_text`` is empty
    when the library is empty.
    """
    selected = load_yaml(paths.selected_yaml) or {}
    papers = list(selected.get("papers") or []) if isinstance(selected, dict) else []
    if not papers:
        return ("", set())

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
        overflow_note = (
            f"\n(NOTE: {len(papers)} papers in library; only the first "
            f"{MAX_LIBRARY_PAPERS_IN_PROMPT} are listed here.)"
        )
    return ("\n".join(bullets) + overflow_note, cite_keys)


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
) -> str:
    parts: list[str] = [f"Section: {section_name}", f"Mode: {mode}"]
    target = target_words or _DEFAULT_TARGET_WORDS.get(section_name)
    if target:
        parts.append(f"Target length: ~{target} words (soft target)")
    if instruction:
        parts.append(f"Additional instruction: {instruction.strip()}")

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

    library_md, library_keys = _build_library_context(paths)

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
    )

    if llm is None:
        from paic.llm.backends.base import LLMUnavailable
        from paic.llm.client import get_default_client
        try:
            llm = get_default_client()
        except LLMUnavailable as exc:
            return {"error": "llm_unavailable", "detail": str(exc)}

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

    _, library_keys = _build_library_context(paths)
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

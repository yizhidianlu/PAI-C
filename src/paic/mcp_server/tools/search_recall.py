"""``paic_search_recall_check`` — title substring tiering for /paic-search.

Bridges the gap between ``paic_dedupe`` (which returns a flat ``unique`` list)
and the SKILL's final markdown table. Without this step the LLM picks which
papers to surface from the dedupe pool by attention alone, which silently
drops papers whose titles explicitly contain the user's core query terms.

Given a deduped paper list and 2-4 LLM-extracted ``core_terms`` (English noun
phrases distilled from the original topic + selected query variants), this
tool deterministically buckets papers into four tiers based on title-only
substring hits, so the SKILL can render a 'must-include' tier above the
softer recall tail.

Matching rules:
- Title is normalized via the same lowercase + whitespace collapse used in
  ``paic.sources.dedupe._normalize_title``.
- Each ``core_term`` is matched as a word-boundary substring on the
  normalized title (``\\b<term>\\b``). Multi-word terms keep their internal
  spaces so they match contiguous spans only ("motor imagery" matches
  "Motor Imagery EEG" but not "motor performance and imagery").
- Tiering on a per-paper basis:
    T1_strict — hits == N (all core_terms)
    T1_loose  — N >= 3 and hits >= N - 1 (loose mode only)
    T2_partial — hits >= 1
    T3_others — hits == 0
- ``tier1_min`` floor: when ``len(T1_strict) + len(T1_loose) < tier1_min``,
  promote highest-hit papers from T2 into T1_loose to ensure the user
  always gets a populated 'must-include' tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from paic.schemas.paper import PaperRef
from paic.sources.dedupe import _normalize_title

MatchMode = Literal["strict", "loose"]


@dataclass
class _PaperHit:
    idx: int
    hits: list[str]


def _normalize_term(term: str) -> str:
    """Lowercase + collapse internal whitespace; preserve multi-word phrases."""
    return " ".join(term.lower().split())


def _build_pattern(term: str) -> re.Pattern[str]:
    """Word-boundary regex for a normalized term.

    For ASCII-only terms we use ``\\b`` boundaries. For terms containing
    non-ASCII chars (e.g. Greek letters in physics queries), ``\\b`` is
    unreliable, so we fall back to a plain substring match.
    """
    escaped = re.escape(term)
    if term.isascii():
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def _scan_paper(title: str, terms: list[str], patterns: list[re.Pattern[str]]) -> list[str]:
    """Return the subset of ``terms`` whose pattern matches the title."""
    norm = _normalize_title(title)
    return [t for t, p in zip(terms, patterns) if p.search(norm)]


def search_recall_check(
    papers: list[PaperRef],
    core_terms: list[str],
    *,
    tier1_min: int = 5,
    match_mode: MatchMode = "loose",
) -> dict[str, Any]:
    """Bucket ``papers`` into 4 tiers by title hits on ``core_terms``.

    See module docstring for the matching rules.
    """
    seen: set[str] = set()
    norm_terms: list[str] = []
    for raw in core_terms:
        t = _normalize_term(raw)
        if t and t not in seen:
            seen.add(t)
            norm_terms.append(t)

    n_terms = len(norm_terms)
    patterns = [_build_pattern(t) for t in norm_terms]

    scored: list[_PaperHit] = []
    for idx, ref in enumerate(papers):
        hits = _scan_paper(ref.title, norm_terms, patterns) if norm_terms else []
        scored.append(_PaperHit(idx=idx, hits=hits))

    t1_strict: list[_PaperHit] = []
    t1_loose: list[_PaperHit] = []
    t2_partial: list[_PaperHit] = []
    t3_others: list[_PaperHit] = []

    for s in scored:
        h = len(s.hits)
        if n_terms == 0:
            t3_others.append(s)
            continue
        if h == n_terms:
            t1_strict.append(s)
        elif match_mode == "loose" and n_terms >= 3 and h >= n_terms - 1:
            t1_loose.append(s)
        elif h >= 1:
            t2_partial.append(s)
        else:
            t3_others.append(s)

    # Floor guarantee: promote highest-hit T2 papers into T1_loose if T1 is short.
    if n_terms > 0 and (len(t1_strict) + len(t1_loose)) < tier1_min and t2_partial:
        t2_partial.sort(key=lambda s: (-len(s.hits), s.idx))
        promote = max(0, tier1_min - len(t1_strict) - len(t1_loose))
        promoted, t2_partial = t2_partial[:promote], t2_partial[promote:]
        t1_loose.extend(promoted)
        # Keep t2 in original idx order after promotion.
        t2_partial.sort(key=lambda s: s.idx)

    def _emit(bucket: list[_PaperHit]) -> list[dict[str, Any]]:
        return [{"idx": s.idx, "hits": s.hits} for s in bucket]

    return {
        "core_terms_normalized": norm_terms,
        "tier1_strict": _emit(t1_strict),
        "tier1_loose": _emit(t1_loose),
        "tier2_partial": _emit(t2_partial),
        "tier3_others": _emit(t3_others),
        "stats": {
            "total": len(papers),
            "t1_strict": len(t1_strict),
            "t1_loose": len(t1_loose),
            "t2_partial": len(t2_partial),
            "t3_others": len(t3_others),
            "core_term_count": n_terms,
            "match_mode": match_mode,
        },
    }


def search_recall_check_tool(
    papers: list[dict[str, Any]],
    core_terms: list[str],
    tier1_min: int = 5,
    match_mode: str = "loose",
) -> dict[str, Any]:
    """MCP-facing wrapper: validate raw dicts → ``PaperRef`` and dispatch."""
    refs = [PaperRef.model_validate(p) for p in papers]
    if match_mode not in ("strict", "loose"):
        raise ValueError(f"match_mode must be 'strict' or 'loose', got {match_mode!r}")
    return search_recall_check(
        refs,
        core_terms,
        tier1_min=tier1_min,
        match_mode=match_mode,  # type: ignore[arg-type]
    )

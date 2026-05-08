"""Citation hallucination detection — first pass via Semantic Scholar batch verify.

Usage::

    verified, pending = verify_library_via_s2(paths, cache=...)

``verified`` is the list of ``cite_key`` strings S2 confirmed exist with
matching bibliographic metadata. ``pending`` is a list of
:class:`WebSearchPending` for citations S2 could neither confirm nor
deny — the caller (or host orchestration) is expected to dispatch
WebSearch and feed verdicts back via :func:`classify_websearch_results`.

Three-layer filter design (per V1.0 plan §P0-1):

1. **S2 batch verify** — fast path; ~70% hit rate per ARS data.
2. **WebSearch fallback** — for the ~30% S2 misses; a host directive
   asks the main conversation to run Claude's built-in WebSearch.
3. **LLM judge classification** — survivors are routed to
   :func:`classify_websearch_results` which assigns the 5-type
   hallucination label (TF / PAC / IH / PH / SH) using the WebSearch
   evidence + paper metadata. Inline mode runs an LLM call here; host
   mode bundles it with the ``pending_ai_judge`` list.

Cache: ``<project>/.paic/state/integrity_cache.json`` keyed on
``(cite_key, sha256(normalized title))`` so re-runs only verify diff.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from paic.integrity.types import IntegrityIssue, WebSearchPending
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml

# Title-fuzzy match threshold for "S2 record matches the paper we expected".
# Below this we treat the S2 result as a near-miss (surface as PH/SH candidate
# for the LLM judge to disambiguate). Tuned for tolerance to subtitle
# trimming while still catching mashup fabrications (Lin et al. 2020 case).
_TITLE_MATCH_RATIO = 88

# How many candidate titles to send per S2 search call. S2's /paper/search
# endpoint takes a free-text query and returns up to ``limit`` records.
_S2_LIMIT_PER_QUERY = 5


@dataclass(frozen=True)
class _LibraryEntry:
    """A normalized view of one ``selected.yaml`` record for verification."""

    cite_key: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    doi: str | None
    arxiv_id: str | None
    s2_id: str | None

    def normalized_title(self) -> str:
        return _normalize_title(self.title)

    def cache_key(self) -> str:
        h = hashlib.sha256(self.normalized_title().encode("utf-8")).hexdigest()[:16]
        return f"{self.cite_key}:{h}"


# ----------------------------------------------------- public API


def verify_library_via_s2(
    paths: ProjectPaths,
    *,
    cache: dict[str, dict] | None = None,
    s2_search_fn: Any = None,
    enabled: bool = True,
) -> tuple[list[str], list[WebSearchPending], list[IntegrityIssue], dict[str, dict]]:
    """Batch-verify every paper in ``selected.yaml`` against Semantic Scholar.

    Returns ``(verified_cite_keys, pending, structural_issues, updated_cache)``:

    - ``verified_cite_keys`` — S2 confirmed the paper exists and metadata
      matches within tolerance.
    - ``pending`` — papers S2 could not confirm; need WebSearch.
    - ``structural_issues`` — bibliographic inconsistencies S2 surfaced
      directly (e.g. ``IH`` for missing DOI when one is available on S2).
    - ``updated_cache`` — augmented ``cache`` dict the caller should
      persist back to ``integrity_cache.json``.

    ``s2_search_fn`` is injectable for tests; defaults to
    :func:`paic.sources.semanticscholar.search_papers`. The fn must return
    an object with a ``papers`` list of records exposing ``title`` /
    ``authors`` / ``year`` / ``doi`` / ``arxiv_id`` attributes (the
    :class:`PaperRef` shape).

    ``enabled=False`` short-circuits — every library entry routes to
    ``pending`` with ``reason="s2_disabled"``. Use this in tests / when
    the user explicitly opts out of S2 (e.g. SEMANTIC_SCHOLAR_API_KEY
    refusal).
    """
    cache = dict(cache or {})
    entries = _load_library_entries(paths)
    if not entries:
        return [], [], [], cache

    if not enabled:
        pending = [
            WebSearchPending(
                cite_key=e.cite_key,
                title=e.title,
                authors=list(e.authors),
                year=e.year,
                expected_doi=e.doi,
                expected_arxiv_id=e.arxiv_id,
                s2_attempted=False,
                reason="s2_disabled",
            )
            for e in entries
        ]
        return [], pending, [], cache

    # Lazy import — keeps the integrity package import-cycle free.
    if s2_search_fn is None:
        from paic.sources.semanticscholar import search_papers as _default_s2
        s2_search_fn = _default_s2

    verified: list[str] = []
    pending: list[WebSearchPending] = []
    issues: list[IntegrityIssue] = []

    for entry in entries:
        ck = entry.cache_key()
        if ck in cache:
            cached = cache[ck]
            verdict = cached.get("verdict")
            if verdict == "VERIFIED":
                verified.append(entry.cite_key)
                continue
            if verdict == "PENDING":
                pending.append(_pending_from_cache(entry, cached))
                continue
            # Unknown verdict in cache — treat as fresh and re-verify.

        result = _run_s2_lookup(entry, s2_search_fn)
        if result is None:
            pending.append(
                WebSearchPending(
                    cite_key=entry.cite_key,
                    title=entry.title,
                    authors=list(entry.authors),
                    year=entry.year,
                    expected_doi=entry.doi,
                    expected_arxiv_id=entry.arxiv_id,
                    s2_attempted=True,
                    reason="s2_no_match",
                )
            )
            cache[ck] = {"verdict": "PENDING", "reason": "s2_no_match"}
            continue

        match, structural = result
        verified.append(entry.cite_key)
        issues.extend(structural)
        cache[ck] = {
            "verdict": "VERIFIED",
            "matched_title": match.get("title"),
            "matched_doi": match.get("doi"),
        }

    return verified, pending, issues, cache


def classify_websearch_results(
    web_search_results: list[dict],
    library_index: dict[str, _LibraryEntry] | None = None,
    paths: ProjectPaths | None = None,
) -> list[IntegrityIssue]:
    """Convert WebSearch verdicts into :class:`IntegrityIssue` entries.

    Each ``web_search_results`` item is a dict with shape::

        {
            "cite_key": "lin_2020_qa_taiwan",
            "verdict": "VERIFIED" | "NOT_FOUND" | "MISMATCH",
            "evidence_url": ["https://doi.org/..."],
            "matched_title": "...",
            "matched_authors": [...],
            "matched_year": 2021,
            "matched_doi": "10.1007/...",
            "notes": "...",     # optional free-text from the host
        }

    Verdict mapping:

    - ``VERIFIED`` → no issue (drop)
    - ``NOT_FOUND`` → ``TF`` (Total Fabrication, severity=blocker)
    - ``MISMATCH`` → classify into ``PAC`` / ``IH`` / ``PH`` / ``SH``
      based on which fields drifted (authors / DOI / pages / year)

    Mismatch heuristic (matches ARS 5-type taxonomy):

    - authors changed entirely → PAC
    - title + book name + page numbers all wrong but authors intact → PH
    - DOI / volume / pages / year missing → IH
    - only year or initials wrong, everything else matches → SH
    """
    library_index = library_index or (
        _index_library(paths) if paths is not None else {}
    )

    issues: list[IntegrityIssue] = []
    for raw in web_search_results:
        cite_key = raw.get("cite_key")
        verdict = (raw.get("verdict") or "").upper()
        if not cite_key or verdict == "VERIFIED":
            continue

        evidence = list(raw.get("evidence_url") or [])
        matched = {
            "title": raw.get("matched_title"),
            "authors": list(raw.get("matched_authors") or []),
            "year": raw.get("matched_year"),
            "doi": raw.get("matched_doi"),
        }

        if verdict == "NOT_FOUND":
            issues.append(IntegrityIssue(
                kind="TF",
                severity="blocker",
                target=cite_key,
                detail=(
                    f"No evidence the paper '{cite_key}' exists. WebSearch "
                    f"returned no matching record. {raw.get('notes') or ''}"
                ).strip(),
                actionable_fix=(
                    "Remove the citation, or replace with a verified paper. "
                    "If the user believes this is a real reference, recheck "
                    "title spelling / DOI and re-run the integrity gate."
                ),
                evidence_url=evidence,
            ))
            continue

        if verdict == "MISMATCH":
            entry = library_index.get(cite_key)
            kind = _classify_mismatch(entry, matched, raw.get("notes") or "")
            severity = "major"  # blocker promotion happens in the runner
            issues.append(IntegrityIssue(
                kind=kind,
                severity=severity,
                target=cite_key,
                detail=(
                    f"WebSearch found a related but different paper. "
                    f"Original metadata diverges from the verified record. "
                    f"{raw.get('notes') or ''}"
                ).strip(),
                actionable_fix=(
                    "Replace the bibliographic record in selected.yaml "
                    "with the corrected metadata listed under "
                    "suggested_correction. Re-export refs.bib."
                ),
                evidence_url=evidence,
                suggested_correction={k: v for k, v in matched.items() if v},
            ))
            continue

        # Unknown verdict — surface as advisory minor so the user notices.
        issues.append(IntegrityIssue(
            kind="IH",
            severity="minor",
            target=cite_key,
            detail=f"WebSearch returned an unrecognized verdict: {verdict!r}",
            actionable_fix="Re-run integrity gate with a clearer WebSearch verdict.",
            evidence_url=evidence,
        ))

    return issues


# ----------------------------------------------------- helpers


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — used for fuzzy match."""
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def _load_library_entries(paths: ProjectPaths) -> list[_LibraryEntry]:
    """Read ``selected.yaml`` and project each paper into a verification entry."""
    selected = load_yaml(paths.selected_yaml) or {}
    if not isinstance(selected, dict):
        return []
    out: list[_LibraryEntry] = []
    from paic.latex.filler import _cite_key
    for paper in selected.get("papers") or []:
        if not isinstance(paper, dict):
            continue
        title = paper.get("title")
        if not title:
            continue
        out.append(_LibraryEntry(
            cite_key=_cite_key(paper),
            title=str(title),
            authors=tuple(str(a) for a in (paper.get("authors") or []) if a),
            year=_safe_int(paper.get("year")),
            doi=paper.get("doi") or None,
            arxiv_id=paper.get("arxiv_id") or None,
            s2_id=paper.get("s2_id") or None,
        ))
    return out


def _index_library(paths: ProjectPaths | None) -> dict[str, _LibraryEntry]:
    if paths is None:
        return {}
    return {e.cite_key: e for e in _load_library_entries(paths)}


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _run_s2_lookup(
    entry: _LibraryEntry,
    s2_search_fn: Any,
) -> tuple[dict, list[IntegrityIssue]] | None:
    """Search S2 by title and return ``(matched_record_dict, structural_issues)``.

    ``None`` means S2 had nothing within fuzz threshold — caller routes
    this entry to WebSearch.
    """
    try:
        result = s2_search_fn(entry.title, limit=_S2_LIMIT_PER_QUERY)
    except Exception:  # noqa: BLE001 — never let S2 errors break the whole gate
        return None

    papers = getattr(result, "papers", None) or []
    if not papers:
        return None

    target_norm = entry.normalized_title()
    best: tuple[int, Any] | None = None
    for record in papers:
        # PaperRef shape — pull title regardless of dict vs pydantic.
        title = getattr(record, "title", None) or (
            record.get("title") if isinstance(record, dict) else None
        )
        if not title:
            continue
        ratio = fuzz.token_set_ratio(target_norm, _normalize_title(str(title)))
        if best is None or ratio > best[0]:
            best = (ratio, record)

    if best is None or best[0] < _TITLE_MATCH_RATIO:
        return None

    record = best[1]
    matched = {
        "title": getattr(record, "title", None) or record.get("title"),
        "authors": list(getattr(record, "authors", None) or record.get("authors") or []),
        "year": getattr(record, "year", None) if hasattr(record, "year") else record.get("year"),
        "doi": getattr(record, "doi", None) if hasattr(record, "doi") else record.get("doi"),
        "arxiv_id": (
            getattr(record, "arxiv_id", None) if hasattr(record, "arxiv_id")
            else record.get("arxiv_id")
        ),
    }
    structural = _structural_diff(entry, matched)
    return matched, structural


def _structural_diff(entry: _LibraryEntry, matched: dict) -> list[IntegrityIssue]:
    """Surface ``IH`` / ``SH`` issues when S2 metadata disagrees with selected.yaml.

    These are advisories — even a VERIFIED paper can have minor metadata
    drift the user wants to know about (e.g. selected.yaml has wrong year).
    """
    issues: list[IntegrityIssue] = []
    matched_year = _safe_int(matched.get("year"))
    if entry.year and matched_year and entry.year != matched_year:
        issues.append(IntegrityIssue(
            kind="SH",
            severity="minor",
            target=entry.cite_key,
            detail=(
                f"selected.yaml year={entry.year} but Semantic Scholar reports "
                f"year={matched_year}."
            ),
            actionable_fix=(
                f"Update selected.yaml entry to year={matched_year} and re-export "
                "refs.bib."
            ),
            suggested_correction={"year": matched_year},
        ))
    if not entry.doi and matched.get("doi"):
        issues.append(IntegrityIssue(
            kind="IH",
            severity="minor",
            target=entry.cite_key,
            detail=(
                "Library record is missing a DOI but Semantic Scholar has "
                f"one available: {matched['doi']}."
            ),
            actionable_fix=(
                f"Set doi={matched['doi']!r} on the selected.yaml entry and "
                "re-export refs.bib."
            ),
            suggested_correction={"doi": matched.get("doi")},
        ))
    return issues


def _pending_from_cache(entry: _LibraryEntry, cached: dict) -> WebSearchPending:
    return WebSearchPending(
        cite_key=entry.cite_key,
        title=entry.title,
        authors=list(entry.authors),
        year=entry.year,
        expected_doi=entry.doi,
        expected_arxiv_id=entry.arxiv_id,
        s2_attempted=True,
        reason=cached.get("reason", "s2_no_match"),
    )


def _classify_mismatch(
    entry: _LibraryEntry | None,
    matched: dict,
    notes: str,
) -> str:
    """Heuristic 5-type classifier for MISMATCH WebSearch verdicts.

    Order is intentional: most specific check first.
    - PAC (authors entirely changed) > PH (multiple field drift) > IH
      (missing core fields) > SH (only year / initials wrong).
    """
    if entry is None:
        return "PH"

    matched_authors = [a.lower() for a in (matched.get("authors") or []) if a]
    expected_authors = [a.lower() for a in entry.authors]
    if expected_authors and matched_authors:
        # Last name set comparison — robust to reordering / first-name changes.
        expected_last = {a.split()[-1] for a in expected_authors if a.split()}
        matched_last = {a.split()[-1] for a in matched_authors if a.split()}
        if expected_last and not (expected_last & matched_last):
            return "PAC"

    drifted_fields = 0
    matched_year = _safe_int(matched.get("year"))
    if entry.year and matched_year and entry.year != matched_year:
        drifted_fields += 1
    if entry.doi and matched.get("doi") and entry.doi != matched["doi"]:
        drifted_fields += 1

    # Mashup signal in the host's notes (e.g. "title and book name from
    # different sources") — surface as PH explicitly.
    if "mashup" in notes.lower() or "different source" in notes.lower():
        return "PH"
    if drifted_fields >= 2:
        return "PH"
    if not entry.doi and not matched.get("doi"):
        return "IH"
    return "SH"

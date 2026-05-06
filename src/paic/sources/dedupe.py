"""Deduplicate paper references across sources.

Strategy (in order):
1. Exact match on any of arxiv_id / doi / s2_id (case-insensitive).
2. Fuzzy title match (rapidfuzz token-set ratio >= ``title_threshold``).

Output preserves the first occurrence of each cluster and reports the
duplicate groupings so the caller can show the user which records merged.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from paic.schemas.paper import PaperRef


@dataclass
class DedupeResult:
    unique: list[PaperRef]
    duplicate_groups: list[list[int]]  # indices in original list, first index = kept


def _id_keys(ref: PaperRef) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if ref.arxiv_id:
        keys.append(("arxiv", ref.arxiv_id.strip().lower()))
    if ref.doi:
        keys.append(("doi", ref.doi.strip().lower()))
    if ref.s2_id:
        keys.append(("s2", ref.s2_id.strip().lower()))
    return keys


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def dedupe(papers: list[PaperRef], *, title_threshold: int = 90) -> DedupeResult:
    """Return ``DedupeResult`` collapsing equivalent ``PaperRef`` entries."""
    n = len(papers)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri == rj:
            return
        # Always keep the smaller index as the canonical representative
        # so iteration order is preserved when emitting `unique`.
        if ri < rj:
            parent[rj] = ri
        else:
            parent[ri] = rj

    # Pass 1: exact ID match
    by_key: dict[tuple[str, str], int] = {}
    for idx, ref in enumerate(papers):
        for key in _id_keys(ref):
            if key in by_key:
                union(by_key[key], idx)
            else:
                by_key[key] = idx

    # Pass 2: fuzzy title match across remaining clusters.
    # We compare each cluster's representative title to the rest.
    titles = [_normalize_title(p.title) for p in papers]
    for i in range(n):
        if find(i) != i:
            continue
        for j in range(i + 1, n):
            if find(j) == find(i):
                continue
            score = fuzz.token_set_ratio(titles[i], titles[j])
            if score >= title_threshold:
                union(i, j)

    # Group by root
    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)

    unique: list[PaperRef] = []
    duplicate_groups: list[list[int]] = []
    for root, members in sorted(groups.items()):
        members.sort()
        unique.append(papers[members[0]])
        if len(members) > 1:
            duplicate_groups.append(members)
    return DedupeResult(unique=unique, duplicate_groups=duplicate_groups)

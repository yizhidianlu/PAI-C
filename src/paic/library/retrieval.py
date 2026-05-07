"""BM25 + MMR retrieval over the project library.

Replaces compose's old "first-N papers from selected.yaml" behavior with
section-aware ranking. The retriever indexes each paper using:

- ``selected.yaml`` (title, authors, year, abstract, tags)
- the structured summary at ``library/summaries/<cite_key>.yaml`` if present
  (problem / method / key_results / limitations / techniques)

Then BM25 on the joined token stream gives an initial ranking, and MMR
(Maximal Marginal Relevance) reranks for diversity to avoid k near-duplicates
from the same author / method family.

Usage:

    retriever = LibraryRetriever.build(paths)
    query = build_query(section="01_intro", paper_plan=plan, idea=idea, experiment=exp)
    hits = retriever.retrieve(query, k=15)
    for hit in hits:
        print(hit.cite_key, hit.score, hit.match_reason, hit.snippet)

Caching: the BM25 index is rebuilt from disk on every ``LibraryRetriever.build``
call. For now this is fine — a 100-paper library indexes in < 100ms — and
sidesteps the cache-invalidation question (mtime tracking is doable but adds
complexity). The plan reserves ``library/retrieval_cache.json`` as the future
cache slot; we just don't write it yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rank_bm25 import BM25Okapi

from paic.latex.filler import _cite_key
from paic.library.chunker import Chunk, load_all_chunks
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml

# Tiny stopword list — keep it small; BM25's IDF already deweights common words.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "by", "with",
    "and", "or", "but", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "we", "our", "they",
    "their", "from", "as", "into", "than", "then", "so", "such", "via",
    "based", "using", "use", "used", "use-", "approach", "method", "model",
    "paper", "study", "work", "show", "shows", "shown", "demonstrate",
})

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + alphanumeric tokens with hyphens preserved.

    ``self-attention`` → ``["self-attention"]``, not split at the hyphen.
    Stopwords removed. Single-char tokens dropped. Order preserved (BM25
    ignores position but it makes debugging easier).
    """
    if not text:
        return []
    return [
        t for t in (m.group().lower() for m in _TOKEN_RE.finditer(text))
        if t not in _STOPWORDS and len(t) > 1
    ]


@dataclass(frozen=True)
class ChunkHit:
    """One chunk-level hit attached to a paper's :class:`RetrievalHit`.

    P0 #1 (chunk-level grounding): when the project has a chunk index
    on disk (built by ``paic_summarize_run`` or
    ``paic_library_reindex_chunks``), retrieve() can return the top-N
    chunks for each paper hit so compose can surface actual passages
    instead of just a one-line summary.
    """

    chunk_id: str
    cite_key: str
    score: float
    text: str
    section_path: str


@dataclass(frozen=True)
class RetrievalHit:
    cite_key: str
    score: float
    match_reason: list[str]
    """Tokens from the query that hit this paper's text. Subset of query
    tokens; informational, used to render a "matched on: foo, bar" hint."""

    snippet: str
    """Up to ~200 chars from the most-matched field, for the prompt
    bullet list shown to the LLM."""

    paper: dict[str, Any]
    """The full PaperRef dict from selected.yaml so callers can render
    title / authors / year without re-loading."""

    chunks: list[ChunkHit] = field(default_factory=list)
    """Top-N chunks (BM25-ranked against the same query) for this paper
    when chunk-level grounding is enabled. Empty when the project has no
    chunk index, when the paper has no chunks, or when the caller didn't
    request chunks (``chunks_per_paper=0``)."""


@dataclass
class _IndexedPaper:
    cite_key: str
    paper: dict[str, Any]
    text: str
    """The full searchable concatenation. Stored for snippet extraction."""

    tokens: list[str]


# ---------------------------------------------------------- index build


def _join_summary_text(record: dict[str, Any]) -> str:
    """Extract the full searchable text from a structured summary yaml dict.

    Pulls the legacy fields (``problem`` / ``method`` / ``key_results`` /
    ``limitations`` / ``techniques`` / ``relevance_to_project``) plus the
    §quality phase 3 expansion (``datasets`` / ``baselines`` / ``metrics`` /
    ``numeric_results`` / ``assumptions`` / ``failure_modes`` /
    ``open_questions`` / ``citation_claims`` / ``quote_spans`` /
    ``contribution_type``). Resilient to missing fields and non-dict shapes.
    """
    if not isinstance(record, dict):
        return ""
    parts: list[str] = []
    for key in ("problem", "method", "relevance_to_project", "contribution_type"):
        v = record.get(key)
        if isinstance(v, str) and v:
            parts.append(v)
    for key in (
        "key_results", "limitations", "techniques",
        "datasets", "baselines", "metrics", "numeric_results",
        "assumptions", "failure_modes", "open_questions",
        "citation_claims", "quote_spans",
    ):
        v = record.get(key)
        if isinstance(v, list):
            parts.extend(str(x) for x in v if x)
    return " ".join(parts)


def _build_paper_text(paper: dict[str, Any], summary: dict[str, Any] | None) -> str:
    """Concatenate every searchable field for a single paper."""
    parts: list[str] = []
    for key in ("title", "abstract"):
        v = paper.get(key)
        if isinstance(v, str) and v:
            parts.append(v)
    authors = paper.get("authors") or []
    if isinstance(authors, list):
        parts.extend(str(a) for a in authors if a)
    venue = paper.get("venue")
    if isinstance(venue, str) and venue:
        parts.append(venue)
    tags = paper.get("tags") or []
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags if t)
    if summary:
        parts.append(_join_summary_text(summary))
    return " ".join(parts)


def _load_summary_for(paths: ProjectPaths, cite_key: str, paper: dict[str, Any]) -> dict[str, Any] | None:
    """Try the canonical and legacy summary paths."""
    for path in (
        paths.summaries_dir / f"{cite_key}.yaml",
        paths.summaries_dir / f"{paper.get('arxiv_id') or paper.get('doi') or cite_key}.yaml",
    ):
        if path.is_file():
            data = load_yaml(path)
            if isinstance(data, dict):
                return data
    return None


# ---------------------------------------------------------- main class


class LibraryRetriever:
    """Section-aware retriever over the project library.

    Construct via :meth:`build`. Use :meth:`retrieve` with a section query.
    """

    def __init__(
        self,
        indexed: list[_IndexedPaper],
        *,
        chunks_by_key: dict[str, list[Chunk]] | None = None,
    ) -> None:
        self._indexed = indexed
        self._chunks_by_key: dict[str, list[Chunk]] = chunks_by_key or {}
        # rank_bm25 hits ZeroDivisionError on a corpus where every document
        # tokenizes to nothing (e.g. tiny placeholder titles in test fixtures).
        # Guard against it; retrieve() will then short-circuit to ``[]``.
        if indexed and any(p.tokens for p in indexed):
            self._bm25 = BM25Okapi([p.tokens for p in indexed])
        else:
            self._bm25 = None  # type: ignore[assignment]

    @classmethod
    def build(cls, paths: ProjectPaths) -> "LibraryRetriever":
        """Build the BM25 index from ``selected.yaml`` + ``summaries/``.

        Also loads any chunk indices under ``library/chunks/`` so chunk-level
        retrieval is opt-in via ``retrieve(chunks_per_paper=K)``. When
        chunks/ doesn't exist (older projects), chunk_per_paper falls back
        to an empty list per hit — paper-level results still work.

        Returns a retriever with an empty index when the library is empty.
        """
        selected = load_yaml(paths.selected_yaml) or {}
        papers = list(selected.get("papers") or []) if isinstance(selected, dict) else []
        indexed: list[_IndexedPaper] = []
        for paper in papers:
            cite_key = _cite_key(paper)
            summary = _load_summary_for(paths, cite_key, paper)
            text = _build_paper_text(paper, summary)
            tokens = _tokenize(text)
            indexed.append(_IndexedPaper(
                cite_key=cite_key, paper=paper, text=text, tokens=tokens,
            ))
        chunks_by_key = load_all_chunks(paths)
        return cls(indexed, chunks_by_key=chunks_by_key)

    @property
    def chunk_count(self) -> int:
        """Total chunks loaded across all papers — for surfacing index health
        (``paic_library_reindex_chunks`` reports it; tests assert it)."""
        return sum(len(c) for c in self._chunks_by_key.values())

    def __len__(self) -> int:
        return len(self._indexed)

    def retrieve(
        self,
        query: str,
        k: int = 12,
        mmr_lambda: float = 0.7,
        *,
        chunks_per_paper: int = 0,
    ) -> list[RetrievalHit]:
        """Return the top-``k`` hits for ``query``, MMR-reranked for diversity.

        ``mmr_lambda`` controls relevance / diversity trade-off:
        - ``1.0`` → pure BM25 (no diversity)
        - ``0.0`` → pure diversity (ignore relevance)
        - ``0.7`` (default) → relevance-leaning with mild deduplication

        ``chunks_per_paper`` (P0 #1): when > 0 and a chunk index is on
        disk, attaches that many top-BM25 chunks per paper to each
        :class:`RetrievalHit`. Default 0 keeps the paper-level behavior
        for callers that don't want chunk-level grounding.
        """
        if not self._indexed or not query.strip() or k <= 0 or self._bm25 is None:
            return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        query_token_set = set(query_tokens)

        scores = self._bm25.get_scores(query_tokens)
        # Candidates: take 3*k (or all) by raw BM25, then MMR rerank to k.
        # NB: BM25Okapi returns NEGATIVE scores when the corpus is too small
        # for stable IDF estimates (e.g. single-doc corpora) — a negative
        # score still means "this doc actually matches at least one query
        # token". We filter out only exactly-zero scores (= zero matches).
        ranked_indices = sorted(
            range(len(self._indexed)),
            key=lambda i: scores[i],
            reverse=True,
        )
        cand_count = min(len(ranked_indices), max(k * 3, k))
        candidates = [(i, float(scores[i])) for i in ranked_indices[:cand_count]
                      if scores[i] != 0]
        if not candidates:
            return []

        selected = self._mmr_rerank(candidates, query_tokens, k, mmr_lambda)

        hits: list[RetrievalHit] = []
        for idx, score in selected:
            paper = self._indexed[idx]
            paper_token_set = set(paper.tokens)
            matched = sorted(query_token_set & paper_token_set)
            snippet = self._snippet(paper.text, matched)
            chunk_hits: list[ChunkHit] = []
            if chunks_per_paper > 0:
                chunk_hits = self._top_chunks_for(
                    paper.cite_key, query_tokens, chunks_per_paper,
                )
            hits.append(RetrievalHit(
                cite_key=paper.cite_key,
                score=score,
                match_reason=matched,
                snippet=snippet,
                paper=paper.paper,
                chunks=chunk_hits,
            ))
        return hits

    # ------------------------------------------------------ chunk hits

    def _top_chunks_for(
        self, cite_key: str, query_tokens: list[str], k: int,
    ) -> list[ChunkHit]:
        """Rank a single paper's chunks against the query, return top-``k``.

        Uses the same tokenizer as paper-level retrieval (consistent
        stopword handling). Falls back to ``[]`` for papers without a
        chunk index — caller decides whether to surface that as a
        warning (compose) or stay silent (review).

        Scoring: BM25 over the per-paper corpus *plus* a query-token
        overlap count. Per-paper BM25 corpora are tiny (often 5-30
        chunks) and BM25's IDF formula returns exactly 0 for tokens
        present in half the chunks — which would silently drop
        legitimately matching chunks. Sorting by ``(overlap_count,
        bm25_score)`` keeps the BM25 signal as a tiebreaker while
        ensuring chunks that contain query terms aren't filtered out.
        """
        chunks = self._chunks_by_key.get(cite_key, [])
        if not chunks or k <= 0:
            return []
        token_lists = [_tokenize(c.text) for c in chunks]
        if not any(token_lists):
            return []
        try:
            bm25 = BM25Okapi(token_lists)
            scores = bm25.get_scores(query_tokens)
        except (ZeroDivisionError, ValueError):
            scores = [0.0] * len(chunks)

        query_set = set(query_tokens)
        ranked: list[tuple[int, float, int]] = []
        for i, _chunk in enumerate(chunks):
            overlap = len(query_set & set(token_lists[i]))
            if overlap == 0:
                continue
            ranked.append((i, float(scores[i]), overlap))
        # Prefer chunks with more query-token coverage; BM25 score breaks ties.
        ranked.sort(key=lambda r: (r[2], r[1]), reverse=True)

        out: list[ChunkHit] = []
        for i, score, _overlap in ranked[:k]:
            chunk = chunks[i]
            out.append(ChunkHit(
                chunk_id=chunk.chunk_id,
                cite_key=cite_key,
                score=score,
                text=chunk.text,
                section_path=chunk.section_path,
            ))
        return out

    # ------------------------------------------------------ MMR

    def _mmr_rerank(
        self,
        candidates: list[tuple[int, float]],
        query_tokens: list[str],
        k: int,
        mmr_lambda: float,
    ) -> list[tuple[int, float]]:
        """Maximal Marginal Relevance rerank.

        Score = λ * BM25(q, d) - (1 - λ) * max_j Jaccard(d, d_j)

        We measure diversity via Jaccard token overlap between candidate
        token sets, which is cheap and works well in practice for paper
        bullet lists.
        """
        if mmr_lambda >= 1.0:
            return candidates[:k]
        if not candidates:
            return []

        # Normalize BM25 scores to [0, 1] so the diversity term is
        # comparable across queries with different score magnitudes.
        # Shift-and-scale so negative BM25 scores (single-doc corpus
        # artifact) still yield a usable relevance signal.
        raw_scores = [score for _, score in candidates]
        lo = min(raw_scores)
        hi = max(raw_scores)
        span = hi - lo
        if span > 0:
            norm = {idx: (score - lo) / span for idx, score in candidates}
        else:
            norm = {idx: 1.0 for idx, _ in candidates}

        raw = {idx: score for idx, score in candidates}
        token_sets: dict[int, set[str]] = {
            idx: set(self._indexed[idx].tokens) for idx, _ in candidates
        }

        selected: list[tuple[int, float]] = []
        remaining = list(candidates)
        while remaining and len(selected) < k:
            best_idx = -1
            best_mmr = -float("inf")
            for cand_idx, _cand_raw in remaining:
                rel = norm[cand_idx]
                if not selected:
                    div = 0.0
                else:
                    div = max(
                        _jaccard(token_sets[cand_idx], token_sets[sel_idx])
                        for sel_idx, _ in selected
                    )
                mmr = mmr_lambda * rel - (1 - mmr_lambda) * div
                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = cand_idx
            if best_idx < 0:
                break
            selected.append((best_idx, raw[best_idx]))
            remaining = [(i, s) for i, s in remaining if i != best_idx]
        return selected

    # ------------------------------------------------------ snippet

    @staticmethod
    def _snippet(text: str, matched_tokens: list[str], window: int = 80) -> str:
        """Extract a short snippet around the first matched token.

        Returns up to ~200 chars centered on a hit. Falls back to the
        first 200 chars when no matched token is found in ``text``.
        """
        if not text:
            return ""
        if not matched_tokens:
            return text[:200].strip()
        lower = text.lower()
        for tok in matched_tokens:
            pos = lower.find(tok)
            if pos >= 0:
                start = max(0, pos - window)
                end = min(len(text), pos + len(tok) + window)
                snippet = text[start:end].strip()
                prefix = "…" if start > 0 else ""
                suffix = "…" if end < len(text) else ""
                return prefix + snippet + suffix
        return text[:200].strip()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


# ---------------------------------------------------------- query builder


_SECTION_INTENT_HINT: dict[str, str] = {
    "01_intro": "motivation problem introduction context",
    "02_related": "related work prior approaches comparison contrast",
    "03_method": "method algorithm technical approach architecture",
    "04_experiments": "experiments results evaluation benchmark dataset baseline metric",
    "05_discussion": "discussion limitations implications",
    "05_conclusion": "conclusion summary contribution",
    "06_conclusion": "conclusion summary contribution",
    "00_abstract": "thesis contribution overview",
    "review": "limitations threats critique",
    "figure": "visualization diagram architecture pipeline",
}


def build_query(
    section: str,
    *,
    paper_plan: dict[str, Any] | None = None,
    idea: dict[str, Any] | None = None,
    experiment: dict[str, Any] | None = None,
    extra: str | None = None,
) -> str:
    """Compose a section-aware retrieval query.

    Combines:
    1. A small section-intent hint (what kind of papers this section needs).
    2. The paper_plan thesis + this section's intent (when available).
    3. The idea title / one-liner / proposed approach (always informative).
    4. Experiment baselines / datasets / metrics (for ``04_experiments``).
    5. Optional ``extra`` free-form caller input.

    Output is a single space-joined string fed to BM25.
    """
    parts: list[str] = []
    hint = _SECTION_INTENT_HINT.get(section)
    if hint:
        parts.append(hint)

    if paper_plan:
        if thesis := paper_plan.get("thesis"):
            parts.append(str(thesis))
        for entry in (paper_plan.get("section_plan") or []):
            if isinstance(entry, dict) and entry.get("name") == section:
                if intent := entry.get("intent"):
                    parts.append(str(intent))
                break
        for term in (paper_plan.get("terminology") or {}).keys():
            parts.append(str(term))

    if idea:
        for key in ("title", "one_liner", "proposed_approach", "novelty_claim"):
            v = idea.get(key)
            if isinstance(v, str) and v:
                parts.append(v)

    if experiment and section in {"03_method", "04_experiments"}:
        method = experiment.get("proposed_method")
        if isinstance(method, str) and method:
            parts.append(method)
        for key in ("datasets", "baselines", "metrics"):
            for item in (experiment.get(key) or []):
                if isinstance(item, dict):
                    name = item.get("name")
                    if name:
                        parts.append(str(name))
                elif isinstance(item, str):
                    parts.append(item)

    if extra:
        parts.append(extra)

    return " ".join(parts).strip()

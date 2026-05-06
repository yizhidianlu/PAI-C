"""Semantic Scholar Graph API client.

The arxiv MCP already uses Semantic Scholar for citation graphs, so this client
focuses on the gap: keyword/author/DOI search across all venues (not just
arXiv). Responses are cached on disk under ``~/.paic/cache/s2/`` keyed by a
hash of the query parameters; cached entries never expire (callers can pass
``force=True`` to bypass).

Rate limiting (S2 official: ``1 req/sec cumulative`` for authenticated keys,
~0.33 req/sec for anonymous): a process-wide :class:`_RateLimiter` gates calls
based on ``cfg.effective_s2_rate_limit()``. The 429-then-retry path is kept
as a defensive backstop for cases where wall-clock measurement disagrees with
the server (network jitter, system clock skew).

When wiring this into an async event loop later, swap ``time.sleep`` in
``_RateLimiter.gate`` for ``await asyncio.sleep`` — see R13 in plan §13.6.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from paic.config import Config, ensure_global_dirs, load_config
from paic.logging import get_logger
from paic.schemas.paper import PaperRef

LOG = get_logger("paic.s2")

SEARCH_PATH = "/paper/search"
DEFAULT_FIELDS = (
    "paperId,externalIds,title,authors,year,venue,abstract,fieldsOfStudy"
)


@dataclass(frozen=True)
class S2SearchResult:
    papers: list[PaperRef]
    total: int
    from_cache: bool


class _RateLimiter:
    """Process-wide gating for Semantic Scholar requests.

    S2's quota is *cumulative across endpoints*, so we keep a single
    ``_last_call_at`` rather than per-host or per-method state. The lock
    serializes the gate decision; ``time.sleep`` itself happens outside the
    lock so a slow caller doesn't block other callers from queuing up.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call_at: float = 0.0

    def gate(self, min_interval_sec: float) -> float:
        """Block until at least ``min_interval_sec`` has passed since last call.

        Returns the wait time actually slept (for tests / instrumentation).
        """
        if min_interval_sec <= 0:
            return 0.0
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_at
            wait = min_interval_sec - elapsed
            # Reserve our slot now so concurrent callers serialize correctly.
            self._last_call_at = now + max(0.0, wait)
        if wait > 0:
            time.sleep(wait)
            return wait
        return 0.0

    def reset_for_tests(self) -> None:
        with self._lock:
            self._last_call_at = 0.0


_RATE_LIMITER = _RateLimiter()


def _cache_key(query: str, params: dict[str, Any]) -> str:
    payload = json.dumps({"q": query, "p": params}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _cache_path(cfg: Config, key: str) -> Path:
    return cfg.s2_cache_dir / f"{key}.json"


def _to_paper_ref(record: dict[str, Any]) -> PaperRef | None:
    """Map an S2 paper record to a PaperRef, dropping malformed entries."""
    title = record.get("title")
    if not title:
        return None
    external = record.get("externalIds") or {}
    arxiv_id = external.get("ArXiv")
    doi = external.get("DOI")
    s2_id = record.get("paperId")
    authors = [a.get("name") for a in (record.get("authors") or []) if a.get("name")]
    return PaperRef(
        arxiv_id=arxiv_id,
        doi=doi,
        s2_id=s2_id,
        title=title,
        authors=authors,
        year=record.get("year"),
        venue=record.get("venue") or None,
        abstract=record.get("abstract"),
        source="s2",
    )


def search_papers(
    query: str,
    *,
    limit: int = 20,
    year_from: int | None = None,
    fields_of_study: list[str] | None = None,
    force: bool = False,
    cfg: Config | None = None,
    client: httpx.Client | None = None,
    _skip_rate_limit: bool = False,
) -> S2SearchResult:
    """Search Semantic Scholar by free-text query.

    ``force=True`` bypasses the cache. Pass ``client`` to inject a stub for
    tests (it must implement ``.get(url, params=..., headers=..., timeout=...)``).

    ``_skip_rate_limit`` disables client-side gating — only used by tests that
    inject a stub HTTP client and don't want artificial sleeps. Real callers
    should never set this.
    """
    cfg = cfg or load_config()
    ensure_global_dirs(cfg)

    params: dict[str, Any] = {
        "query": query,
        "limit": min(max(limit, 1), 100),
        "fields": DEFAULT_FIELDS,
    }
    if year_from is not None:
        params["year"] = f"{year_from}-"
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)

    key = _cache_key(query, params)
    cache_file = _cache_path(cfg, key)
    if not force and cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        return _result_from_payload(cached, from_cache=True)

    headers = {}
    if cfg.semantic_scholar_api_key:
        headers["x-api-key"] = cfg.semantic_scholar_api_key

    rate_limit = cfg.effective_s2_rate_limit()
    min_interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
    base_url = cfg.providers_s2.base_url
    timeout = cfg.providers_s2.timeout_sec

    payload: dict[str, Any]
    owns_client = client is None
    http = client or httpx.Client()
    try:
        # Defensive: keep the 429 retry even though we proactively gate, in case
        # clock skew or a parallel caller from another process pushes us over.
        for attempt in range(2):
            if not _skip_rate_limit:
                _RATE_LIMITER.gate(min_interval)
            response = http.get(
                base_url + SEARCH_PATH,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            if response.status_code == 429 and attempt == 0:
                LOG.warning(
                    "S2 returned 429 despite client-side rate limit (%.2f req/s); "
                    "sleeping 2s and retrying once",
                    rate_limit,
                )
                time.sleep(2)
                continue
            response.raise_for_status()
            payload = response.json()
            break
        else:  # pragma: no cover - exhausted retries
            payload = {"data": [], "total": 0}
    except httpx.HTTPError as exc:
        LOG.error("S2 search failed: %s", exc)
        return S2SearchResult(papers=[], total=0, from_cache=False)
    finally:
        if owns_client:
            http.close()

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return _result_from_payload(payload, from_cache=False)


def _result_from_payload(payload: dict[str, Any], *, from_cache: bool) -> S2SearchResult:
    raw_records = payload.get("data") or []
    papers: list[PaperRef] = []
    for record in raw_records:
        ref = _to_paper_ref(record)
        if ref is not None:
            papers.append(ref)
    return S2SearchResult(papers=papers, total=int(payload.get("total", len(papers))), from_cache=from_cache)

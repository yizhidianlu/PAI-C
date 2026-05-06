"""MCP-call throttling helpers — §15 (arxiv) and §18 (paper-search-mcp).

PAI-C's Python process is *not* in the call path of either external MCP server:
Skills tell Claude to invoke ``mcp__arxiv__*`` and ``mcp__paper_search__*``
directly. That means we cannot enforce rate limiting the way
``semanticscholar._RateLimiter`` does for S2 (where every HTTP call flows
through PAI-C Python).

Instead, we expose two tiny sleep tools:

* ``paic_arxiv_pace`` — between arxiv MCP calls (download / read / search).
  Default delay = ``cfg.providers_arxiv.inter_batch_delay_sec`` (3.0s).
* ``paic_search_pace(platform=...)`` — between paper-search-mcp calls. Default
  delay is per-platform from ``cfg.providers_external_search.inter_call_delay_sec``.

The Skill prompt is responsible for invoking these between successive calls.
This is *voluntary* coordination — if the LLM forgets to call them, no gating
happens. See R20 (§15.6) and R32 (§18.14).

Future async refactor: when FastMCP adopts an async event loop, swap
``time.sleep`` for ``await asyncio.sleep`` to avoid blocking the loop.
"""

from __future__ import annotations

import time
from typing import Any

from paic.config import Config, load_config

PACE_CAP_SECONDS = 30.0


def arxiv_pace_run(
    seconds: float | None = None,
    *,
    cfg: Config | None = None,
    _skip_sleep: bool = False,
) -> dict[str, Any]:
    """Sleep ``seconds`` (default = ``cfg.providers_arxiv.inter_batch_delay_sec``).

    Skill layer calls this between arxiv MCP operations to stay under
    arxiv.org's recommended 1 req / 3 seconds.

    Args:
        seconds: explicit duration in seconds. If ``None``, read default from
            config. Must be in ``[0, PACE_CAP_SECONDS]``.
        cfg: injected config (tests).
        _skip_sleep: bypass the actual ``time.sleep`` — only for tests.

    Returns:
        ``{"slept_sec": float, "default_used": bool}``.

    Raises:
        ValueError: if ``seconds`` is negative or exceeds ``PACE_CAP_SECONDS``.
    """
    cfg = cfg or load_config()
    default_used = seconds is None
    target = (
        cfg.providers_arxiv.inter_batch_delay_sec if default_used else float(seconds)
    )
    if target < 0:
        raise ValueError(f"seconds must be non-negative (got {target})")
    if target > PACE_CAP_SECONDS:
        raise ValueError(
            f"seconds={target} exceeds cap {PACE_CAP_SECONDS}; "
            "use a smaller value or split the wait into multiple calls"
        )
    if not _skip_sleep and target > 0:
        time.sleep(target)
    return {"slept_sec": target, "default_used": default_used}


def search_pace_run(
    platform: str,
    seconds: float | None = None,
    *,
    cfg: Config | None = None,
    _skip_sleep: bool = False,
) -> dict[str, Any]:
    """Sleep between paper-search-mcp calls to ``platform``.

    Default delay is read from
    ``cfg.providers_external_search.inter_call_delay_sec[platform]``,
    falling back to 1.0s for platforms not in the table. Same hard cap as
    ``arxiv_pace_run`` (PACE_CAP_SECONDS = 30s).

    Args:
        platform: upstream platform name (``pubmed`` / ``biorxiv`` / ``openalex`` …).
            Free-form string; unknown names use the 1.0s fallback.
        seconds: explicit duration. If ``None``, uses the per-platform default.
        cfg: injected config (tests).
        _skip_sleep: bypass actual ``time.sleep`` — only for tests.

    Returns:
        ``{"slept_sec": float, "platform": str, "default_used": bool}``.

    Raises:
        ValueError: if ``platform`` is empty, or ``seconds`` is negative or
            exceeds ``PACE_CAP_SECONDS``.
    """
    if not platform or not str(platform).strip():
        raise ValueError("platform must be a non-empty string")
    cfg = cfg or load_config()
    default_used = seconds is None
    target = (
        cfg.providers_external_search.delay_for(platform)
        if default_used
        else float(seconds)
    )
    if target < 0:
        raise ValueError(f"seconds must be non-negative (got {target})")
    if target > PACE_CAP_SECONDS:
        raise ValueError(
            f"seconds={target} exceeds cap {PACE_CAP_SECONDS}; "
            "use a smaller value or split the wait into multiple calls"
        )
    if not _skip_sleep and target > 0:
        time.sleep(target)
    return {
        "slept_sec": target,
        "platform": str(platform),
        "default_used": default_used,
    }

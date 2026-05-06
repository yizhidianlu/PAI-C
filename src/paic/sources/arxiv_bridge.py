"""Read arxiv MCP's locally-downloaded papers.

The arxiv MCP stores converted markdown under a configurable directory.
Different arxiv MCP versions disagree on the default — newer builds use
``~/Documents/arxiv-papers/`` while older builds use ``~/.arxiv-mcp/papers/``.
PAI-C probes a list of candidate roots (configurable via
``cfg.arxiv_mcp_storage_paths``) and returns the first hit.

If no file is found across any candidate, we return ``None``. The summarize
tool's caller (the ``paic-summarize`` skill) then has two options:
1. call ``mcp__arxiv__read_paper(paper_id)`` and pass the markdown back as
   ``paper_text=...`` to ``paic_summarize_run`` (the recommended path), or
2. ask the user to ``mcp__arxiv__download_paper`` first and retry.
"""

from __future__ import annotations

from pathlib import Path

from paic.config import Config, load_config


def _candidate_paths(storage: Path, paper_id: str) -> list[Path]:
    """Return a list of plausible file paths for ``paper_id`` under one root."""
    pid = paper_id.strip()
    return [
        storage / f"{pid}.md",
        storage / pid / "paper.md",
        storage / pid / f"{pid}.md",
        storage / "markdown" / f"{pid}.md",
    ]


def find_local_markdown(paper_id: str, *, cfg: Config | None = None) -> Path | None:
    """Locate the markdown file for ``paper_id`` across all configured roots."""
    cfg = cfg or load_config()
    for storage in cfg.arxiv_mcp_storage_paths:
        if not storage.exists():
            continue
        for candidate in _candidate_paths(storage, paper_id):
            if candidate.is_file():
                return candidate
    return None


def read_local_markdown(paper_id: str, *, cfg: Config | None = None) -> str | None:
    """Return the markdown body for a paper, or ``None`` if not found."""
    path = find_local_markdown(paper_id, cfg=cfg)
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def iter_storage_roots(*, cfg: Config | None = None) -> list[tuple[Path, bool, int]]:
    """For ``paic doctor``: list every configured root with hit-status + count."""
    cfg = cfg or load_config()
    out: list[tuple[Path, bool, int]] = []
    for storage in cfg.arxiv_mcp_storage_paths:
        if storage.exists() and storage.is_dir():
            count = sum(1 for _ in storage.rglob("*.md"))
            out.append((storage, True, count))
        else:
            out.append((storage, False, 0))
    return out

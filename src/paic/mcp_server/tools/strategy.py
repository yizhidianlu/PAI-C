"""Search strategy resolver — §18.

Reports the active multi-platform search plan for a given project: which
platforms to query, what pacing each one uses, and any warnings (missing
optional API keys, disabled external search, …).

The Skill layer calls this once at the top of ``/paic-search`` to fan out
correctly without having to read yaml itself.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from paic.config import DOMAIN_PRESETS, Config, load_config
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml

# Platforms that always go through the legacy code path (arxiv MCP /
# paic_s2_search), not paper-search-mcp.
_LEGACY_PLATFORMS: tuple[str, ...] = ("arxiv", "semantic_scholar")

# Platforms that gate on an env var. (key_envs, hint_when_missing).
_PLATFORM_KEY_REQUIREMENTS: dict[str, tuple[tuple[str, ...], str]] = {
    "ieee": (
        ("PAPER_SEARCH_MCP_IEEE_API_KEY", "IEEE_API_KEY"),
        "IEEE Xplore API key not set",
    ),
    "acm": (
        ("PAPER_SEARCH_MCP_ACM_API_KEY", "ACM_API_KEY"),
        "ACM Digital Library API key not set",
    ),
    "unpaywall": (
        ("PAPER_SEARCH_MCP_UNPAYWALL_EMAIL", "UNPAYWALL_EMAIL"),
        "Unpaywall requires an email contact (PAPER_SEARCH_MCP_UNPAYWALL_EMAIL)",
    ),
}


def _platform_available(platform: str) -> tuple[bool, str | None]:
    """Return (available, reason_if_unavailable)."""
    req = _PLATFORM_KEY_REQUIREMENTS.get(platform)
    if req is None:
        return True, None
    env_names, hint = req
    if any(os.environ.get(name) for name in env_names):
        return True, None
    return False, hint


def search_strategy_run(
    *,
    project_dir: str | Path,
    cfg: Config | None = None,
) -> dict[str, Any]:
    """Resolve the active search strategy for a project.

    Returns:
        ``{
            "enabled": bool,                    # external search globally on?
            "domain_preset": str | None,        # project's preset (or default)
            "platforms": [str, ...],            # active platform list
            "pacing": {platform: float, ...},   # per-call delay for non-legacy platforms
            "max_results_per_platform": int,
            "warnings": [str, ...],
        }``

    When ``providers.external_search.enabled = false`` (the default),
    ``platforms`` collapses to ``["arxiv", "semantic_scholar"]`` and the Skill
    layer should fall back to the legacy two-source flow.
    """
    cfg = cfg or load_config()
    ext = cfg.providers_external_search

    # Load project metadata (best-effort — strategy must not fail if no project).
    domain_preset: str | None = None
    project_platforms: list[str] = []
    try:
        paths = resolve_project(project_dir)
        if paths.project_yaml.is_file():
            meta = load_yaml(paths.project_yaml) or {}
            if isinstance(meta, dict):
                domain_preset = meta.get("domain_preset") or None
                listed = meta.get("platforms") or []
                if isinstance(listed, list):
                    project_platforms = [str(p) for p in listed if isinstance(p, str)]
    except FileNotFoundError:
        pass

    if not ext.enabled:
        return {
            "enabled": False,
            "domain_preset": domain_preset,
            "platforms": list(_LEGACY_PLATFORMS),
            "pacing": {},
            "max_results_per_platform": ext.max_results_per_platform,
            "warnings": [
                "external search disabled "
                "(set providers.external_search.enabled=true in ~/.paic/config.yaml)",
            ],
        }

    # Resolve platform list: project-yaml override > preset > global default preset
    if project_platforms:
        platforms = list(project_platforms)
    else:
        preset = domain_preset if domain_preset in DOMAIN_PRESETS else ext.default_preset
        domain_preset = preset
        platforms = list(DOMAIN_PRESETS.get(preset, []))

    # Always prepend arxiv + semantic_scholar (legacy backbone). Insert in
    # reverse so the final order is: arxiv, semantic_scholar, ...rest.
    for p in reversed(_LEGACY_PLATFORMS):
        if p not in platforms:
            platforms.insert(0, p)

    # Filter unavailable platforms (missing optional keys).
    warnings: list[str] = []
    available: list[str] = []
    for p in platforms:
        ok, reason = _platform_available(p)
        if ok:
            available.append(p)
        else:
            warnings.append(f"{p} skipped: {reason}")

    pacing = {
        p: ext.delay_for(p)
        for p in available
        if p not in _LEGACY_PLATFORMS
    }

    return {
        "enabled": True,
        "domain_preset": domain_preset,
        "platforms": available,
        "pacing": pacing,
        "max_results_per_platform": ext.max_results_per_platform,
        "warnings": warnings,
    }

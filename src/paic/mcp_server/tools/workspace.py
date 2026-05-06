"""Workspace tools: ``paic_workspace_init`` and ``paic_workspace_status``.

These are pure functions; the MCP server (Phase 2) wraps them with
``@mcp.tool`` decorators. Returning JSON-serializable dicts keeps the contract
identical whether called from MCP or from unit tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID

from paic.config import DOMAIN_PRESETS
from paic.workspace.paths import (
    ProjectPaths,
    ensure_project_layout,
    find_project_root,
    resolve_project,
)
from paic.workspace.store import load_yaml, save_yaml

# Platforms that always go through the legacy path (arxiv MCP / paic_s2_search).
# We prepend them to every project's platform list so the existing /paic-search
# flow keeps working even when external_search adds new platforms on top.
_LEGACY_PLATFORMS: tuple[str, ...] = ("arxiv", "semantic_scholar")


def _resolve_platforms(
    domain_preset: str | None,
    platforms_override: list[str] | None,
) -> tuple[str | None, list[str] | None]:
    """Decide what to write to project.yaml.

    Returns ``(preset_to_record, platforms_to_record)``. Either may be None
    when the caller passed nothing — workspace_init then leaves the project
    yaml untouched on those fields (so an existing project's config isn't
    silently wiped by a re-init that omits the args).
    """
    if platforms_override is not None:
        # Trust the override; tag with "custom" so doctor/strategy can show it.
        cleaned = [str(p).strip() for p in platforms_override if str(p).strip()]
        seen: set[str] = set()
        ordered: list[str] = []
        for p in (*_LEGACY_PLATFORMS, *cleaned):
            if p not in seen:
                seen.add(p)
                ordered.append(p)
        return ("custom", ordered)

    if domain_preset is not None:
        preset = str(domain_preset).strip()
        if preset and preset in DOMAIN_PRESETS:
            seen2: set[str] = set()
            ordered2: list[str] = []
            for p in (*_LEGACY_PLATFORMS, *DOMAIN_PRESETS[preset]):
                if p not in seen2:
                    seen2.add(p)
                    ordered2.append(p)
            return (preset, ordered2)
        # Unknown preset string → record the preset name as-is so the user
        # sees their typo, but don't fabricate a platforms list. strategy.py
        # will fall back to the global default at search time.
        return (preset or None, None)

    return (None, None)


def workspace_init(
    project_dir: str | Path,
    title: str | None = None,
    venue: str | None = None,
    deadline: str | None = None,
    domain_preset: str | None = None,
    platforms_override: list[str] | None = None,
) -> dict[str, Any]:
    """Initialize ``.paic/`` in the given directory.

    Idempotent: re-running on an already-initialized project just refreshes the
    layout. Caller decides whether to update ``project.yaml`` metadata.

    ``domain_preset`` (one of ``cs_ml`` / ``biomed`` / ``physics_math`` /
    ``econ_social`` / ``engineering`` / ``interdisciplinary``) and
    ``platforms_override`` (free-form list) opt the project into the §18
    multi-platform search flow. Both are advisory — if
    ``providers.external_search.enabled`` is ``false`` globally, the legacy
    two-source path still runs regardless.
    """
    root = Path(project_dir).expanduser().resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)

    paths = ProjectPaths(root=root)
    created = not paths.paic_dir.exists()
    ensure_project_layout(paths)

    preset_to_record, platforms_to_record = _resolve_platforms(
        domain_preset, platforms_override
    )

    if created or not paths.project_yaml.exists():
        project_id = str(ULID())
        meta: dict[str, Any] = {
            "project_id": project_id,
            "title": title,
            "venue": venue,
            "deadline": deadline,
            "created_at": datetime.now(UTC).isoformat(),
            "schema_version": 1,
        }
        # Only record platform fields when caller asked for them; leaving them
        # absent is the signal "use global default_preset at strategy time".
        if preset_to_record is not None:
            meta["domain_preset"] = preset_to_record
        if platforms_to_record is not None:
            meta["platforms"] = platforms_to_record
        save_yaml(paths.project_yaml, meta)
    else:
        meta = load_yaml(paths.project_yaml) or {}
        # patch metadata fields the caller passed; preserve existing values
        # for fields they left None.
        updated = False
        for key, value in (("title", title), ("venue", venue), ("deadline", deadline)):
            if value is not None and meta.get(key) != value:
                meta[key] = value
                updated = True
        if preset_to_record is not None and meta.get("domain_preset") != preset_to_record:
            meta["domain_preset"] = preset_to_record
            updated = True
        if platforms_to_record is not None and meta.get("platforms") != platforms_to_record:
            meta["platforms"] = platforms_to_record
            updated = True
        if updated:
            save_yaml(paths.project_yaml, meta)

    return {
        "project_id": meta.get("project_id"),
        "paic_dir": str(paths.paic_dir),
        "created": created,
        "domain_preset": meta.get("domain_preset"),
        "platforms": meta.get("platforms"),
    }


def workspace_status(project_dir: str | Path | None = None) -> dict[str, Any]:
    """Return a snapshot of the project's current state."""
    if project_dir is None:
        # Best-effort: walk up from cwd.
        found = find_project_root(Path.cwd())
        if found is None:
            return {"initialized": False, "reason": "no .paic/ found at or above cwd"}
        paths = ProjectPaths(root=found)
    else:
        paths = resolve_project(project_dir)

    if not paths.paic_dir.exists():
        return {"initialized": False, "reason": f"no .paic/ at {paths.root}"}

    project_meta = load_yaml(paths.project_yaml) or {}
    selected = load_yaml(paths.selected_yaml) or {}
    library_count = len(selected.get("papers", [])) if isinstance(selected, dict) else 0

    ideas_count = sum(1 for p in paths.ideas_dir.glob("*.yaml") if p.name != "_ranking.yaml")
    experiments_count = sum(1 for _ in paths.experiments_dir.glob("*.yaml"))
    reviews_count = sum(1 for _ in paths.reviews_dir.iterdir() if _.is_dir())

    runs_snapshot = load_yaml(paths.runs_yaml) or {}
    active_runs = runs_snapshot.get("active", []) if isinstance(runs_snapshot, dict) else []

    return {
        "initialized": True,
        "project": project_meta,
        "library_count": library_count,
        "ideas_count": ideas_count,
        "experiments_count": experiments_count,
        "reviews_count": reviews_count,
        "active_runs": active_runs,
        "paic_dir": str(paths.paic_dir),
    }

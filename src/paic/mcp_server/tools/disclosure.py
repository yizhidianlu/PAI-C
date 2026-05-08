"""``paic_disclosure_generate`` MCP tool — venue-specific AI disclosure render.

Pure template render via :mod:`paic.format.disclosure`. No LLM call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paic.format.disclosure import (
    SUPPORTED_VENUES,
    DisclosureContext,
    generate_disclosure,
)
from paic.workspace.paths import resolve_project
from paic.workspace.store import write_text


def disclosure_generate_tool(
    project_dir: str,
    *,
    venue: str,
    tools: list[dict[str, Any]],
    paper_title: str | None = None,
    author_responsibility_note: str | None = None,
    raise_equity_note: str | None = None,
    extra_lines: list[str] | None = None,
    output_format: str = "markdown",
    write_to: str | None = None,
) -> dict[str, Any]:
    """Render the disclosure paragraph for ``venue``.

    ``tools`` is the inventory: list of dicts each with ``name`` /
    ``stage`` / ``extent`` / ``purpose``. Stages: ``ideation``,
    ``literature_review``, ``drafting``, ``analysis``, ``revision``,
    ``formatting``. Extents: ``minor``, ``moderate``, ``extensive``.

    ``write_to`` is optional — when given (relative or absolute path),
    the rendered text is written to that file in addition to being
    returned. Default suggested locations: ``drafts/disclosure.md`` for
    Markdown / ``drafts/disclosure.tex`` for LaTeX.

    Returns ``{venue, output_format, content, placement_hint, warnings,
    template_path, written_to?, supported_venues}``.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    cleaned_tools = _clean_tools(tools)

    ctx = DisclosureContext(
        venue=venue,
        tools=cleaned_tools,
        paper_title=paper_title,
        author_responsibility_note=(
            author_responsibility_note
            or DisclosureContext.__dataclass_fields__["author_responsibility_note"].default
        ),
        raise_equity_note=raise_equity_note or "",
        extra_lines=list(extra_lines or []),
        output_format=output_format,
    )
    out = generate_disclosure(ctx)
    if "error" in out:
        return out

    if write_to:
        target = _resolve_path(paths.root, write_to)
        write_text(target, out["content"])
        out["written_to"] = str(target)

    out["supported_venues"] = list(SUPPORTED_VENUES)
    return out


def _clean_tools(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Coerce each entry to the four expected str keys; drop incomplete rows.

    Keeps the surface forgiving — users can paste rough JSON and we fill
    the missing fields with sensible placeholders.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "stage": str(entry.get("stage") or "drafting").strip(),
            "extent": str(entry.get("extent") or "moderate").strip(),
            "purpose": str(entry.get("purpose") or "(unspecified)").strip(),
        })
    return out


def _resolve_path(project_root: Path, candidate: str) -> Path:
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return p
    return (project_root / p).resolve()

"""``paic_format_convert`` MCP tool — Pandoc-driven document & citation conversion.

Pure subprocess wrapper around :mod:`paic.format.pandoc_bridge` and
:mod:`paic.format.bib_convert`. No LLM call, no host orchestration —
this is a pre-submission packaging step that runs locally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paic.format.bib_convert import (
    CSL_ALIASES,
    detect_csl_path,
    list_csl_aliases,
    render_with_citation_style,
)
from paic.format.pandoc_bridge import (
    PandocUnavailable,
    convert_document,
    pandoc_available,
)
from paic.workspace.paths import resolve_project


def format_convert_tool(
    project_dir: str,
    *,
    input_path: str,
    output_path: str,
    target_format: str | None = None,
    citation_style: str | None = None,
    bibliography: str | None = None,
    from_format: str | None = None,
) -> dict[str, Any]:
    """Convert a manuscript file via pandoc.

    Args:
        project_dir: PAI-C project root (used to locate ``refs.bib`` if
            ``bibliography`` is omitted).
        input_path: source file (absolute or project-relative).
        output_path: destination file (absolute or project-relative).
            Format inferred from suffix unless ``target_format`` set.
        target_format: pandoc ``-t`` value (``docx`` / ``pdf`` / ``latex`` /
            ``markdown`` / ``html``); optional when output_path suffix
            disambiguates.
        citation_style: alias from :data:`paic.format.bib_convert.CSL_ALIASES`
            (``apa7`` / ``chicago`` / ``mla9`` / ``ieee`` / ``vancouver``)
            or a literal ``<id>.csl`` filename in your CSL search dir.
            When set, pandoc runs with ``--citeproc --csl=<path>``.
            Falls back to pandoc's built-in default when CSL not found.
        bibliography: BibTeX file. Default: ``<project>/.paic/drafts/refs.bib``
            when present.
        from_format: pandoc ``-f`` value, optional when input suffix
            disambiguates.

    Returns the same shape :func:`paic.format.pandoc_bridge.convert_document`
    returns plus ``citation_style`` / ``csl_path_used`` / ``notes``.

    On missing pandoc → ``error: pandoc_unavailable`` with install
    instructions instead of crashing (mirror Overleaf-sync's wireup).
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    if not pandoc_available():
        return {
            "error": "pandoc_unavailable",
            "hint": (
                "Install pandoc and restart Claude Code. Brew: `brew install "
                "pandoc`. Apt: `apt-get install pandoc`. Windows: "
                "https://pandoc.org/installing.html. For PDF output also "
                "install a LaTeX engine (TeX Live / MikTeX / Tectonic)."
            ),
        }

    in_path = _resolve_path(paths.root, input_path)
    out_path = _resolve_path(paths.root, output_path)
    if not in_path.is_file():
        return {
            "error": "input_not_found",
            "input_path": str(in_path),
            "hint": "Pass an absolute path or one project-relative to project_dir.",
        }

    bib_path: Path | None = None
    if bibliography:
        bib_path = _resolve_path(paths.root, bibliography)
        if not bib_path.is_file():
            return {
                "error": "bibliography_not_found",
                "bibliography": str(bib_path),
            }
    else:
        default_bib = paths.drafts_dir / "refs.bib"
        if default_bib.is_file():
            bib_path = default_bib

    try:
        if citation_style and bib_path is not None:
            result = render_with_citation_style(
                input_path=in_path,
                output_path=out_path,
                bibliography=bib_path,
                citation_style=citation_style,
                from_format=from_format,
                to_format=target_format,
            )
        else:
            result = convert_document(
                input_path=in_path,
                output_path=out_path,
                from_format=from_format,
                to_format=target_format,
                bibliography=bib_path,
            )
            if citation_style:
                result.setdefault("notes", []).append(
                    "citation_style was specified but no bibliography is "
                    "available — pandoc will not render citations. Pass "
                    "bibliography=path/to/refs.bib or place one at "
                    "<project>/.paic/drafts/refs.bib."
                )
    except PandocUnavailable as exc:
        return {"error": "pandoc_unavailable", "detail": str(exc)}

    if not result.get("ok"):
        result["error"] = result.get("error", "pandoc_failed")
    return result


def list_supported_citation_styles() -> dict[str, Any]:
    """Return CSL aliases the format-convert tool accepts (used by the SKILL doc)."""
    return {
        "aliases": list_csl_aliases(),
        "alias_to_filename": dict(CSL_ALIASES),
        "csl_search_path": _describe_csl_search_path(),
    }


def _resolve_path(project_root: Path, candidate: str) -> Path:
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return p
    return (project_root / p).resolve()


def _describe_csl_search_path() -> list[str]:
    import os
    search_dirs: list[str] = []
    if os.environ.get("PAIC_CSL_DIR"):
        search_dirs.append(f"$PAIC_CSL_DIR={os.environ['PAIC_CSL_DIR']}")
    home = os.environ.get("PAIC_HOME") or "~/.paic"
    search_dirs.append(f"{home}/csl/")
    search_dirs.append("(package vendored — none in v1)")
    return search_dirs


def diagnose_csl(citation_style: str) -> dict[str, Any]:
    """Quick check: would the requested style resolve to a CSL file?

    Used by the SKILL to warn the user up-front when the requested style
    won't be found, before they wait for pandoc to run.
    """
    csl = detect_csl_path(citation_style)
    return {
        "citation_style": citation_style,
        "csl_path_used": str(csl) if csl else None,
        "found": csl is not None,
        "search_path": _describe_csl_search_path(),
    }

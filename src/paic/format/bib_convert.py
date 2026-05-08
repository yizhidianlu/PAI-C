"""Citation-style conversion via Pandoc + CSL (ARS-fusion P0-3).

Two operations:

1. :func:`render_with_citation_style` — given a Markdown / LaTeX manuscript +
   a BibTeX file + a CSL style id, run pandoc with ``--citeproc --csl=<path>``
   to emit the same manuscript with citations rendered in the requested style.

2. :func:`detect_csl_path` — locate the requested CSL file. Search order:

   - ``$PAIC_CSL_DIR`` (override)
   - ``~/.paic/csl/<id>.csl``
   - ``<package>/templates/csl/<id>.csl`` (vendored, if shipped)

   Returns ``None`` when no candidate exists; the caller decides whether
   to error out or fall back to pandoc's built-in Chicago default.

Supported CSL ids (the MCP tool layer accepts these aliases):

- ``apa7`` → "American Psychological Association 7th edition"
- ``chicago-author-date`` → "Chicago Manual of Style 17th edition (author-date)"
- ``mla9`` → "Modern Language Association 9th edition"
- ``ieee`` → "IEEE"
- ``vancouver`` → "Vancouver"

Each id maps to one of the canonical CSL filenames from
https://github.com/citation-style-language/styles (CC-BY-SA) — not vendored
in v1; users download once and drop into ``~/.paic/csl/``.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

from paic.format.pandoc_bridge import PandocUnavailable, convert_document, pandoc_available

# CSL aliases the MCP tool accepts — keys are user-facing, values are the
# canonical CSL filenames at github.com/citation-style-language/styles.
CSL_ALIASES: dict[str, str] = {
    "apa7": "apa.csl",
    "apa": "apa.csl",
    "chicago": "chicago-author-date.csl",
    "chicago-author-date": "chicago-author-date.csl",
    "chicago-notes": "chicago-note-bibliography.csl",
    "mla9": "modern-language-association.csl",
    "mla": "modern-language-association.csl",
    "ieee": "ieee.csl",
    "vancouver": "vancouver.csl",
}


def list_csl_aliases() -> list[str]:
    """Return the user-facing CSL alias keys (deduplicated)."""
    return sorted(set(CSL_ALIASES.keys()))


def _csl_search_dirs() -> list[Path]:
    """Search order for CSL files."""
    dirs: list[Path] = []
    override = os.environ.get("PAIC_CSL_DIR")
    if override:
        dirs.append(Path(override).expanduser().resolve())
    paic_home = Path(os.environ.get("PAIC_HOME", Path.home() / ".paic")).expanduser().resolve()
    dirs.append(paic_home / "csl")
    return dirs


def detect_csl_path(alias_or_filename: str) -> Path | None:
    """Resolve ``alias`` / ``<id>.csl`` to an on-disk CSL file.

    Returns the first hit in :func:`_csl_search_dirs`; falls back to the
    package-vendored ``templates/csl/<filename>`` if any. Returns
    ``None`` when nothing matches.
    """
    if not alias_or_filename:
        return None

    target_filename = CSL_ALIASES.get(alias_or_filename.lower(), alias_or_filename)
    if not target_filename.endswith(".csl"):
        target_filename = f"{target_filename}.csl"

    for d in _csl_search_dirs():
        p = d / target_filename
        if p.is_file():
            return p

    # Last resort: vendored templates (none ship in v1).
    try:
        files = resources.files("paic.format.templates").joinpath("csl")
        candidate = files / target_filename
        if candidate.is_file():
            return Path(str(candidate))
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    return None


def render_with_citation_style(
    *,
    input_path: Path,
    output_path: Path,
    bibliography: Path,
    citation_style: str,
    from_format: str | None = None,
    to_format: str | None = None,
) -> dict:
    """Convert one manuscript with citations rendered in the requested style.

    Returns the same shape :func:`convert_document` returns plus a
    ``citation_style`` echo and a ``csl_path_used`` field (string or
    None when pandoc fell back to its built-in default).

    Raises :class:`PandocUnavailable` when pandoc is missing.
    """
    if not pandoc_available():
        raise PandocUnavailable("pandoc not on PATH — install before running format-convert")

    csl_path = detect_csl_path(citation_style) if citation_style else None

    result = convert_document(
        input_path=input_path,
        output_path=output_path,
        from_format=from_format,
        to_format=to_format,
        bibliography=bibliography,
        csl=csl_path,
    )
    result["citation_style"] = citation_style
    result["csl_path_used"] = str(csl_path) if csl_path else None
    if csl_path is None and citation_style:
        result.setdefault("notes", []).append(
            f"CSL file for '{citation_style}' not found. Searched "
            f"{[str(d) for d in _csl_search_dirs()]}. "
            "Falling back to pandoc's built-in default style. "
            "Download from https://github.com/citation-style-language/styles "
            "and drop into ~/.paic/csl/."
        )
    return result

"""Library attachment helper — §24.

After ``/paic-ingest`` triggers a download (via arxiv MCP or paper-search-mcp),
we want a copy of the file in the project's own ``.paic/library/pdfs/``
directory. This module's tool consolidates that step:

- For arxiv papers, where the upstream MCP writes markdown to a global
  storage path, we locate the file via ``arxiv_bridge`` and copy it.
- For paper-search-mcp papers, the SKILL passes ``save_path`` directly
  to ``mcp__paper_search__download_<platform>``, so the file already
  lives at the right place — no copy needed. (We expose this tool only
  for the arxiv path; non-arxiv attaches go through paper-search-mcp's
  own ``save_path`` plumbing.)

The destination filename uses the same ``cite_key`` convention as the
LaTeX bibliography (``arxiv_<id>``, ``doi_<slug>``, ``s2_<id>``, …) so a
``\\cite{KEY}`` in a draft maps directly to ``library/pdfs/KEY.<ext>``.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from paic.config import load_config
from paic.latex.filler import _cite_key
from paic.sources.arxiv_bridge import find_local_markdown
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml, save_yaml

# Allow alphanumerics, dot, underscore, hyphen — covers the ``NNN_title.ext``
# pattern emitted by the ingest skill while rejecting path traversal,
# directory separators, and control characters.
_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _has_any_identifier(paper: dict[str, Any]) -> bool:
    return bool(
        paper.get("arxiv_id")
        or paper.get("doi")
        or paper.get("s2_id")
        or paper.get("title")
    )


def _sanitize_display_name(name: str) -> str | None:
    """Validate a SKILL-supplied display filename. Returns the name if safe,
    else ``None``. The caller treats ``None`` as a hard error — we never
    silently coerce a malformed name into something else, because that would
    make ingest's filename audit unverifiable from the call site.
    """
    if not name or len(name) > 200:
        return None
    if ".." in name:
        return None
    if not _DISPLAY_NAME_RE.match(name):
        return None
    return name


def _persist_pdf_local_path(paths, cite_key: str, filename: str) -> bool:
    """Idempotently write ``pdf_local_path = filename`` into the selected.yaml
    entry whose cite_key matches. Returns True if the file was rewritten,
    False if no matching entry exists (paper not yet registered) or the
    field already had the correct value.
    """
    selected = load_yaml(paths.selected_yaml) or {}
    if not isinstance(selected, dict):
        return False
    papers = selected.get("papers") or []
    for record in papers:
        if not isinstance(record, dict):
            continue
        if _cite_key(record) == cite_key:
            if record.get("pdf_local_path") == filename:
                return False
            record["pdf_local_path"] = filename
            selected["papers"] = papers
            save_yaml(paths.selected_yaml, selected)
            return True
    return False


def _ext_for(paper: dict[str, Any], source_path: Path | None) -> str:
    """Pick the destination file extension.

    arxiv MCP writes ``.md`` (markdown). paper-search-mcp writes ``.pdf``.
    When the caller supplies a ``source_path``, trust its suffix; otherwise
    fall back based on the paper's identifier:
      - ``arxiv_id`` set → ``.md`` (the only case we auto-locate a source)
      - else → ``.pdf`` (this code path is currently unreachable without
        ``source_path``, so it's a defensive default)
    """
    if source_path is not None and source_path.suffix:
        return source_path.suffix.lower()
    if paper.get("arxiv_id"):
        return ".md"
    return ".pdf"


def library_attach_paper_tool(
    project_dir: str,
    paper: dict[str, Any],
    source_path: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Copy a downloaded paper into ``<project>/.paic/library/pdfs/``.

    Two modes:

    1. **explicit** — caller passes ``source_path``: we copy that file,
       picking the extension from its suffix.
    2. **arxiv auto-locate** — ``source_path`` is None and the paper has
       an ``arxiv_id``: we use ``arxiv_bridge.find_local_markdown`` to
       resolve the upstream markdown and copy it.

    Filename resolution:

    - Default (``display_name=None``) → ``<cite_key>.<ext>`` (the legacy
      BibTeX-aligned slug, kept for backward compatibility).
    - ``display_name="<safe_filename>"`` → the supplied filename is used
      verbatim (after sanitization). On success we write the filename
      back into ``selected.yaml`` as ``pdf_local_path`` so downstream
      summarize / draft can find the PDF without re-deriving it.

    Skips silently (``copied=False``) if the destination already exists.
    Returns ``{cite_key, dest_path, copied, ext, source_path, pdf_local_path}``
    on success or an ``error`` dict on failure.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    if not isinstance(paper, dict) or not _has_any_identifier(paper):
        return {
            "error": "cannot_derive_cite_key",
            "detail": "paper must have at least one of arxiv_id / doi / s2_id / title",
        }

    cite_key = _cite_key(paper)

    # Resolve the source. Either trust the caller, or — for arxiv — locate
    # via arxiv_bridge using the configured storage roots.
    src: Path | None = None
    if source_path:
        src = Path(source_path).expanduser()
        if not src.is_file():
            return {
                "error": "source_not_found",
                "detail": f"source_path does not exist: {src}",
                "cite_key": cite_key,
            }
    elif paper.get("arxiv_id"):
        cfg = load_config()
        located = find_local_markdown(paper["arxiv_id"], cfg=cfg)
        if located is None:
            return {
                "error": "source_not_found",
                "detail": (
                    f"arxiv markdown for {paper['arxiv_id']} not found in any "
                    f"configured storage path. Run mcp__arxiv__download_paper first."
                ),
                "cite_key": cite_key,
                "arxiv_id": paper["arxiv_id"],
            }
        src = located
    else:
        return {
            "error": "source_not_found",
            "detail": (
                "no source_path provided and paper has no arxiv_id; for "
                "non-arxiv papers, pass save_path to download_<platform> "
                "directly (no attach call needed) or supply source_path."
            ),
            "cite_key": cite_key,
        }

    ext = _ext_for(paper, src)
    paths.pdfs_dir.mkdir(parents=True, exist_ok=True)

    final_filename: str | None = None
    if display_name is not None:
        sanitized = _sanitize_display_name(display_name)
        if sanitized is None:
            return {
                "error": "invalid_display_name",
                "detail": (
                    "display_name must contain only [A-Za-z0-9._-], be "
                    "non-empty, ≤200 chars, and not contain '..' — got "
                    f"{display_name!r}."
                ),
                "cite_key": cite_key,
            }
        final_filename = sanitized
        dest = paths.pdfs_dir / sanitized
    else:
        dest = paths.pdfs_dir / f"{cite_key}{ext}"

    if dest.exists():
        result: dict[str, Any] = {
            "cite_key": cite_key,
            "dest_path": str(dest),
            "copied": False,
            "reason": "already_exists",
            "ext": ext,
            "source_path": str(src),
        }
        if final_filename is not None:
            _persist_pdf_local_path(paths, cite_key, final_filename)
            result["pdf_local_path"] = final_filename
        return result

    try:
        shutil.copyfile(src, dest)
    except OSError as exc:
        return {
            "error": "copy_failed",
            "detail": str(exc),
            "cite_key": cite_key,
            "source_path": str(src),
            "dest_path": str(dest),
        }

    result = {
        "cite_key": cite_key,
        "dest_path": str(dest),
        "copied": True,
        "ext": ext,
        "source_path": str(src),
    }
    if final_filename is not None:
        _persist_pdf_local_path(paths, cite_key, final_filename)
        result["pdf_local_path"] = final_filename
    return result

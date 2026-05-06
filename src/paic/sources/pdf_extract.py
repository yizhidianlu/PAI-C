"""PDF → text extraction via pypdf — §25.

Used by ``/paic-summarize`` to read the project-local PDFs that §24's
multi-platform ingest deposited under ``library/pdfs/<cite_key>.pdf``.
arxiv papers continue to use the upstream markdown path; this module
covers everything else (PubMed / bioRxiv / OpenAlex / Crossref …).

Why pypdf and not GROBID/pdfplumber/pymupdf:

- pypdf is pure-Python, MIT-licensed, ~500KB on disk. No system deps,
  no Docker, no AGPL.
- LLMs summarize from raw text; we don't need pdfplumber's column-aware
  output or GROBID's structured TEI. Word order is occasionally
  scrambled in multi-column papers but the content is all there.
- Failure modes (encrypted / scanned-image-only / corrupted) are
  surfaced as named errors so the SKILL can give the user actionable
  guidance instead of a blank "extraction failed".
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

# pypdf is the maintained fork of PyPDF2; the public API has been stable
# from 4.x onward. Heavy import is done lazily so unrelated PAI-C calls
# don't pay the cost.

ExtractionError = Literal[
    "io_error",
    "corrupt",
    "encrypted",
    "empty_extraction",
]


def _content_hash(path: Path) -> str:
    """sha256 of the PDF file contents — keys the cache.

    Hashing content (not path+mtime) lets the same PDF in two different
    project copies share a cache entry, and also avoids stale hits when
    a PDF is overwritten in place.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_cache(cache_dir: Path, key: str) -> str | None:
    cached = cache_dir / f"{key}.txt"
    if cached.is_file():
        try:
            return cached.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def _write_cache(cache_dir: Path, key: str, text: str) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{key}.txt").write_text(text, encoding="utf-8")
    except OSError:
        # Cache is best-effort; failing to write shouldn't break the call.
        pass


def extract_pdf_text(
    path: Path,
    *,
    cache_dir: Path | None = None,
    min_chars: int = 200,
) -> tuple[str | None, ExtractionError | None]:
    """Extract plain text from a PDF.

    Args:
        path: PDF file on disk.
        cache_dir: optional cache root. When provided, successful extractions
            are written to ``<cache_dir>/<sha256[:16]>.txt`` and subsequent
            calls on the same file (by content hash) return the cached text
            without re-running pypdf. Cache is append-only — to force a
            re-extraction, delete the cache file (or the whole directory).
        min_chars: extracted text shorter than this is treated as
            ``empty_extraction``. Catches scanned-image PDFs where pypdf
            returns the empty string for every page. Default 200 covers
            most "PDF-as-image" cases without false-positiving on real
            short letters.

    Returns:
        ``(text, None)`` on success, ``(None, error_reason)`` on failure
        where ``error_reason`` is one of ``io_error`` / ``corrupt`` /
        ``encrypted`` / ``empty_extraction``.
    """
    if not path.is_file():
        return (None, "io_error")

    try:
        content_hash = _content_hash(path)
    except OSError:
        return (None, "io_error")
    cache_key = content_hash[:16]

    if cache_dir is not None:
        cached = _read_cache(cache_dir, cache_key)
        if cached is not None and len(cached) >= min_chars:
            return (cached, None)

    # Lazy import — pypdf brings in a chunk of code we'd rather not load
    # on every PAI-C MCP call.
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        # Defensive — pyproject pins pypdf>=4.0; this branch only fires if
        # the user yanked it manually.
        return (None, "io_error")

    try:
        reader = PdfReader(str(path))
    except PdfReadError:
        return (None, "corrupt")
    except (OSError, ValueError):
        return (None, "io_error")

    # pypdf surfaces encryption via .is_encrypted; some PDFs use empty
    # passwords and decrypt successfully — try that before giving up.
    if getattr(reader, "is_encrypted", False):
        try:
            ok = reader.decrypt("")
        except Exception:
            ok = 0
        if not ok:
            return (None, "encrypted")

    parts: list[str] = []
    try:
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text:
                parts.append(text)
    except PdfReadError:
        return (None, "corrupt")

    body = "\n\n".join(parts).strip()
    if len(body) < min_chars:
        return (None, "empty_extraction")

    if cache_dir is not None:
        _write_cache(cache_dir, cache_key, body)

    return (body, None)

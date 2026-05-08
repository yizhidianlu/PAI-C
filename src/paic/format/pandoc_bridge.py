"""Subprocess wrapper for the ``pandoc`` CLI.

Pandoc is a **weak dependency** — when missing, callers receive a
:class:`PandocUnavailable` exception with platform-specific install
instructions instead of a crash. This mirrors the
``paic_draft_sync_overleaf`` pattern (Dropbox is also opt-in).

Supported conversions (MVP):

- Markdown → DOCX
- Markdown → PDF (via pdflatex / xelatex / tectonic if available)
- Markdown → LaTeX
- LaTeX → DOCX
- LaTeX → Markdown
- DOCX → Markdown

Citation style conversion uses ``--citeproc`` with a ``--csl=<path>``
flag when a CSL file is supplied. PAI-C does not vendor CSL files in
v1; users are expected to download from
https://github.com/citation-style-language/styles (CC-BY-SA) and pass
the path via :func:`convert_document`. Without ``--csl``, pandoc uses
its built-in default (Chicago author-date).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PANDOC_BINARY = "pandoc"


class PandocUnavailable(RuntimeError):
    """Raised when pandoc is not installed or cannot be invoked."""


_INSTALL_INSTRUCTIONS = """
Pandoc is required for /paic-format-convert. Install:

  · macOS:    brew install pandoc
  · Ubuntu:   apt-get install pandoc
  · Windows:  https://pandoc.org/installing.html (or `choco install pandoc`)

For PDF output also install a LaTeX engine:
  · TeX Live (cross-platform), MikTeX (Windows), or Tectonic.

After install, restart Claude Code so the MCP server picks up the new PATH.
""".strip()


def pandoc_available() -> bool:
    """Cheap PATH check — does NOT invoke ``pandoc --version``."""
    return shutil.which(PANDOC_BINARY) is not None


def _ensure_pandoc() -> None:
    if not pandoc_available():
        raise PandocUnavailable(_INSTALL_INSTRUCTIONS)


def convert_document(
    input_path: Path,
    output_path: Path,
    *,
    from_format: str | None = None,
    to_format: str | None = None,
    bibliography: Path | None = None,
    csl: Path | None = None,
    extra_args: list[str] | None = None,
    timeout_sec: float = 90.0,
) -> dict:
    """Run ``pandoc`` to convert ``input_path`` → ``output_path``.

    Returns ``{ok, command, stdout, stderr}``. Raises
    :class:`PandocUnavailable` when pandoc is missing.

    Format detection:

    - ``from_format`` / ``to_format`` are passed to ``pandoc -f / -t`` when
      provided. Otherwise pandoc autodetects by suffix.
    - When ``bibliography`` is provided, ``--citeproc`` is added so cites
      get rendered. ``csl`` (optional) selects the citation style file.
    """
    _ensure_pandoc()

    input_path = Path(input_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()
    if not input_path.is_file():
        return {
            "ok": False,
            "error": "input_not_found",
            "input_path": str(input_path),
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [PANDOC_BINARY, str(input_path), "-o", str(output_path)]
    if from_format:
        cmd.extend(["-f", from_format])
    if to_format:
        cmd.extend(["-t", to_format])
    if bibliography is not None:
        cmd.extend(["--citeproc", "--bibliography", str(bibliography)])
        if csl is not None:
            cmd.extend(["--csl", str(csl)])
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "pandoc_timeout",
            "command": cmd,
            "timeout_sec": timeout_sec,
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": "pandoc_invocation_failed",
            "command": cmd,
            "detail": repr(exc),
        }

    return {
        "ok": result.returncode == 0,
        "command": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:] if result.stdout else "",
        "stderr": result.stderr[-4000:] if result.stderr else "",
        "output_path": str(output_path),
    }

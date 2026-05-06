"""Copy template static assets to drafts/ — §19.

User-supplied LaTeX templates often ship with venue-specific ``.sty`` /
``.cls`` / ``.bst`` files plus figure assets. The fill flow walks the
template root and copies every non-jinja file into ``drafts/`` so the
generated ``main.tex`` can reference them without the user having to
manually drop them in (closing the loop on the long-standing instruction
"please drop neurips_2024.sty into drafts/ before compiling").
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

# Files PAI-C writes itself during fill — never overwrite these from a template.
# Their template versions (if any) are usually leftover examples from the venue
# bundle; the SKILL surfaces a warning so the user knows we skipped them.
_MANAGED_FILENAMES: frozenset[str] = frozenset({
    "main.tex",
    "refs.bib",
})

# Sections live under sections/ — those are also PAI-C-managed (rendered from
# _shared/sections/*.j2 or the template's own sections/*.j2). Static .tex files
# in template-root/sections/ would clobber them, so we skip.
_MANAGED_DIRS: frozenset[str] = frozenset({"sections"})

# File extensions that are jinja templates, not static assets.
_JINJA_SUFFIXES: frozenset[str] = frozenset({".j2", ".jinja", ".jinja2"})


def _is_static_asset(path: Path, *, template_root: Path) -> tuple[bool, str | None]:
    """Decide if ``path`` should be copied to drafts/.

    Returns ``(should_copy, skip_reason)``. ``skip_reason`` is non-None when
    we're deliberately skipping a file the caller might want a warning about
    (e.g. ``refs.bib`` from the template, since PAI-C generates its own).
    """
    if not path.is_file():
        return False, None
    if path.name == "template.yaml":
        return False, None  # silent skip — metadata, not an asset
    if path.suffix in _JINJA_SUFFIXES:
        return False, None  # silent skip — jinja templates are rendered separately
    rel = path.relative_to(template_root)
    parts = rel.parts
    if parts and parts[0] in _MANAGED_DIRS:
        # E.g. sections/foo.tex — clobbers PAI-C-rendered sections.
        return False, f"{rel.as_posix()} skipped — PAI-C renders sections/ from jinja"
    if path.name in _MANAGED_FILENAMES:
        return False, f"{rel.as_posix()} skipped — PAI-C generates {path.name}"
    return True, None


def copy_static_assets(template_root: Path, drafts_dir: Path) -> dict[str, Any]:
    """Copy every static asset from ``template_root`` to ``drafts_dir``.

    Preserves the relative directory layout (so ``figures/logo.pdf`` lands at
    ``drafts/figures/logo.pdf``). Overwrites existing files at the destination
    — fill is the source of truth.

    Returns:
        ``{"copied": [str, ...], "skipped": [str, ...]}``. ``copied`` paths
        are POSIX-style relative to ``drafts_dir`` for stable display.
        ``skipped`` are warnings (e.g. attempted to copy ``refs.bib``).
    """
    copied: list[str] = []
    skipped: list[str] = []

    if not template_root.is_dir():
        return {"copied": copied, "skipped": skipped}

    drafts_dir.mkdir(parents=True, exist_ok=True)

    for src in sorted(template_root.rglob("*")):
        ok, reason = _is_static_asset(src, template_root=template_root)
        if not ok:
            if reason is not None:
                skipped.append(reason)
            continue
        rel = src.relative_to(template_root)
        dst = drafts_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel.as_posix())

    return {"copied": copied, "skipped": skipped}

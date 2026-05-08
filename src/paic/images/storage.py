"""Versioned figure storage under ``<project>/.paic/figures/<slot>/``.

Layout:

    figures/
      _plan.yaml                      # the figure plan (top-level)
      <slot>/
        v1.png                        # generate
        v2_edit.png                   # edit of v1
        v3_variant.png                # variation of v1
        meta.yaml                     # version index + per-version provenance

A "version label" is ``v<n>`` for plain generations and ``v<n>_edit`` /
``v<n>_variant`` for derived versions. ``n`` is a monotonically-increasing
integer per slot — easier to reason about than ULIDs in 1:1 user
conversations and still unambiguous in `.tex` paths.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, save_yaml

VersionKind = Literal["generate", "edit", "variant"]

_VERSION_RE = re.compile(r"^v(\d+)(?:_(edit|variant))?$")


def _slot_dir(paths: ProjectPaths, slot: str) -> Path:
    return paths.figures_dir / slot


def _meta_path(paths: ProjectPaths, slot: str) -> Path:
    return _slot_dir(paths, slot) / "meta.yaml"


def list_versions(paths: ProjectPaths, slot: str) -> list[str]:
    """Return version labels in slot order (v1, v2, ...) — empty if no slot dir."""
    sdir = _slot_dir(paths, slot)
    if not sdir.exists():
        return []
    labels: list[tuple[int, str]] = []
    for item in sdir.iterdir():
        if item.suffix.lower() != ".png":
            continue
        m = _VERSION_RE.match(item.stem)
        if m:
            labels.append((int(m.group(1)), item.stem))
    labels.sort()
    return [label for _, label in labels]


def latest_version(paths: ProjectPaths, slot: str) -> str | None:
    versions = list_versions(paths, slot)
    return versions[-1] if versions else None


def latest_version_bytes(paths: ProjectPaths, slot: str) -> bytes | None:
    label = latest_version(paths, slot)
    if not label:
        return None
    return (_slot_dir(paths, slot) / f"{label}.png").read_bytes()


def version_bytes(paths: ProjectPaths, slot: str, label: str) -> bytes:
    return (_slot_dir(paths, slot) / f"{label}.png").read_bytes()


def next_version_label(paths: ProjectPaths, slot: str, kind: VersionKind) -> str:
    """Compute the next monotonic label for the given kind.

    ``kind='generate'`` → ``v<n>`` ; ``kind='edit'`` → ``v<n>_edit`` ;
    ``kind='variant'`` → ``v<n>_variant``. ``n`` is one more than the max
    integer prefix already on disk, regardless of kind — so chronological
    order remains obvious from the filename.
    """
    versions = list_versions(paths, slot)
    if not versions:
        n = 1
    else:
        last = versions[-1]
        m = _VERSION_RE.match(last)
        n = (int(m.group(1)) + 1) if m else 1
    if kind == "generate":
        return f"v{n}"
    return f"v{n}_{kind}"


def load_meta(paths: ProjectPaths, slot: str) -> dict[str, Any]:
    """Load ``meta.yaml`` for a slot. Empty dict on missing file."""
    return load_yaml(_meta_path(paths, slot), default={}) or {}


def save_version(
    paths: ProjectPaths,
    slot: str,
    png_bytes: bytes,
    *,
    kind: VersionKind,
    prompt: str,
    model: str,
    parent_version: str | None = None,
    extra: dict[str, Any] | None = None,
    brief: dict[str, Any] | None = None,
) -> tuple[str, Path]:
    """Persist a new version + update meta.yaml. Returns (label, png_path).

    ``brief`` is an optional snapshot of the grounding context active at
    generation time — typically ``{scene_description, supporting_claims,
    primary_claim_id, claim_texts, terminology_used}``. Stored under each
    version entry's ``brief`` key so a regen / audit can replay what
    claims and terminology the planner referenced even after claims.yaml
    or paper_plan.yaml mutate downstream. ``None`` keeps the entry slim
    (existing behavior — old plans without claims still work).
    """
    sdir = _slot_dir(paths, slot)
    sdir.mkdir(parents=True, exist_ok=True)
    label = next_version_label(paths, slot, kind)
    png_path = sdir / f"{label}.png"
    png_path.write_bytes(png_bytes)

    meta = load_meta(paths, slot)
    meta.setdefault("slot", slot)
    versions = meta.setdefault("versions", [])
    entry: dict[str, Any] = {
        "version": label,
        "kind": kind,
        "model": model,
        "prompt": prompt,
        "parent_version": parent_version,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if extra:
        entry.update(extra)
    if brief:
        entry["brief"] = brief
    versions.append(entry)
    save_yaml(_meta_path(paths, slot), meta)
    return label, png_path


def figure_latex_snippet(
    slot: str,
    version: str,
    *,
    caption: str | None = None,
    width: str = "0.8\\linewidth",
    placement: str = "t",
) -> str:
    """Render the ``\\begin{figure}...\\end{figure}`` block.

    Path is project-relative (``figures/<slot>/<version>.png``) so it works
    inside the user's draft/main.tex regardless of where they pasted the
    snippet, as long as the build root sees ``figures/`` (default for PAI-C
    scaffold).
    """
    cap = caption or f"\\TODO{{caption for {slot}}}"
    return (
        "\\begin{figure}[" + placement + "]\n"
        "  \\centering\n"
        f"  \\includegraphics[width={width}]{{figures/{slot}/{version}.png}}\n"
        f"  \\caption{{{cap}}}\n"
        f"  \\label{{fig:{slot}}}\n"
        "\\end{figure}\n"
    )

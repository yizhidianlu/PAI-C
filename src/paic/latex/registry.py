"""LaTeX template registry — §19.

Discovers built-in templates (cvpr / neurips / ieee under ``src/paic/latex/templates/``)
and project-local user templates (``<project>/.paic/templates/<name>/``).

Project-local templates win on name collision so users can override built-ins
without renaming their target venue.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from paic.workspace.paths import ProjectPaths

# Built-in templates ship inside the package so they're available out of the box.
_BUILTIN_ROOT = Path(__file__).parent / "templates"
# These directory names under _BUILTIN_ROOT are NOT venue templates — they're
# the shared section pool (used as fallback when a template doesn't override).
_RESERVED_BUILTIN_DIRS: frozenset[str] = frozenset({"_shared"})


class TemplateNotFound(LookupError):
    """Raised by ``resolve_template`` when no template matches the given name."""

    def __init__(self, name: str, available: list[str]):
        self.name = name
        self.available = available
        super().__init__(
            f"template '{name}' not found. Available: {', '.join(available) if available else '(none)'}"
        )


@dataclass(frozen=True)
class TemplateRecord:
    """One discovered template — built-in or user-supplied."""

    name: str
    kind: Literal["builtin", "user"]
    root: Path
    display_name: str | None = None
    target_venue: str | None = None
    description: str | None = None
    has_static_assets: bool = False
    overrides_builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "root": str(self.root),
            "display_name": self.display_name or self.name,
            "target_venue": self.target_venue,
            "description": self.description,
            "has_static_assets": self.has_static_assets,
            "overrides_builtin": self.overrides_builtin,
        }


def builtin_root() -> Path:
    """Absolute path to the package's built-in template directory."""
    return _BUILTIN_ROOT


def shared_sections_root() -> Path:
    """Absolute path to the fallback section pool used by every template."""
    return _BUILTIN_ROOT / "_shared"


def _has_static_assets(template_root: Path) -> bool:
    """Return True if the template ships any non-jinja static file.

    Used to populate ``TemplateRecord.has_static_assets`` so the list tool /
    SKILL can surface "this template will auto-copy 3 .sty files" hints.
    """
    if not template_root.is_dir():
        return False
    for path in template_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == "template.yaml":
            continue
        if path.suffix in (".j2", ".jinja", ".jinja2"):
            continue
        # We're being conservative: any non-jinja file counts.
        return True
    return False


def _read_metadata(template_root: Path) -> dict[str, Any]:
    """Read ``template.yaml`` if present; tolerate missing or corrupt files."""
    meta_path = template_root / "template.yaml"
    if not meta_path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (yaml.YAMLError, OSError):
        return {}


def _is_valid_template_dir(path: Path) -> bool:
    """A template directory must contain a ``main.tex.j2`` at its root."""
    return (path / "main.tex.j2").is_file()


def _build_record(name: str, kind: str, root: Path, *, overrides_builtin: bool = False) -> TemplateRecord:
    meta = _read_metadata(root)
    return TemplateRecord(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        root=root,
        display_name=meta.get("display_name") if isinstance(meta.get("display_name"), str) else None,
        target_venue=meta.get("target_venue") if isinstance(meta.get("target_venue"), str) else None,
        description=meta.get("description") if isinstance(meta.get("description"), str) else None,
        has_static_assets=_has_static_assets(root),
        overrides_builtin=overrides_builtin,
    )


def _scan(root: Path, kind: str) -> dict[str, TemplateRecord]:
    """List subdirectories of ``root`` that look like templates."""
    found: dict[str, TemplateRecord] = {}
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in _RESERVED_BUILTIN_DIRS:
            continue
        if entry.name.startswith("."):
            continue
        if not _is_valid_template_dir(entry):
            continue
        found[entry.name] = _build_record(entry.name, kind, entry)
    return found


def discover_templates(paths: ProjectPaths) -> list[TemplateRecord]:
    """List every template available to ``paths`` — built-in + project-local.

    Project-local templates override built-ins on name collision; the
    overriding record is flagged ``overrides_builtin=True`` so callers
    (list tool / doctor) can surface this.
    """
    builtin = _scan(_BUILTIN_ROOT, "builtin")
    user = _scan(paths.templates_dir, "user")

    combined: dict[str, TemplateRecord] = dict(builtin)
    for name, rec in user.items():
        if name in builtin:
            # Project-local wins; flag the override.
            combined[name] = TemplateRecord(
                name=rec.name,
                kind="user",
                root=rec.root,
                display_name=rec.display_name,
                target_venue=rec.target_venue,
                description=rec.description,
                has_static_assets=rec.has_static_assets,
                overrides_builtin=True,
            )
        else:
            combined[name] = rec

    # Sort: user templates first (most likely the user's current target), then builtins,
    # alphabetical within each group. Avoids hiding the user's freshly-uploaded
    # template at the bottom of the list.
    return sorted(
        combined.values(),
        key=lambda r: (0 if r.kind == "user" else 1, r.name),
    )


def resolve_template(name: str, paths: ProjectPaths) -> TemplateRecord:
    """Return the active template record for ``name``.

    Project-local takes precedence. Raises ``TemplateNotFound`` with the list
    of available names so the MCP tool can surface a helpful error.
    """
    records = discover_templates(paths)
    for rec in records:
        if rec.name == name:
            return rec
    raise TemplateNotFound(name=name, available=[r.name for r in records])

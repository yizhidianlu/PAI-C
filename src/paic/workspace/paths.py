"""Project path resolution.

A "PAI-C project" is any directory containing a `.paic/` folder. The CLI/MCP
tools can resolve a project either from an explicit path argument or by walking
up from the current working directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_LAYOUT: tuple[str, ...] = (
    "library",
    "library/notes",
    "library/summaries",
    "library/pdfs",
    "ideas",
    "experiments",
    "reviews",
    "drafts",
    "drafts/sections",
    "drafts/figures",
    "figures",
    "templates",
    "state",
    "logs",
)


@dataclass(frozen=True)
class ProjectPaths:
    """Filesystem layout for a single PAI-C project."""

    root: Path

    @property
    def paic_dir(self) -> Path:
        return self.root / ".paic"

    @property
    def project_yaml(self) -> Path:
        return self.paic_dir / "project.yaml"

    @property
    def library_dir(self) -> Path:
        return self.paic_dir / "library"

    @property
    def selected_yaml(self) -> Path:
        return self.library_dir / "selected.yaml"

    @property
    def notes_dir(self) -> Path:
        return self.library_dir / "notes"

    @property
    def summaries_dir(self) -> Path:
        return self.library_dir / "summaries"

    @property
    def pdfs_dir(self) -> Path:
        """Project-local archive of paper PDFs / markdowns (§24).

        ``/paic-ingest`` writes a copy of each downloaded paper here, named
        ``<cite_key>.<ext>`` so users have stable in-project access to the
        full text without hunting through arxiv MCP / paper-search-mcp's
        platform-specific storage directories.
        """
        return self.library_dir / "pdfs"

    @property
    def ideas_dir(self) -> Path:
        return self.paic_dir / "ideas"

    @property
    def ideas_ranking(self) -> Path:
        return self.ideas_dir / "_ranking.yaml"

    @property
    def experiments_dir(self) -> Path:
        return self.paic_dir / "experiments"

    @property
    def reviews_dir(self) -> Path:
        return self.paic_dir / "reviews"

    @property
    def drafts_dir(self) -> Path:
        return self.paic_dir / "drafts"

    @property
    def figures_dir(self) -> Path:
        """Generated figure assets (raster images from /paic-figure).

        One subdirectory per ``slot`` (e.g. ``teaser`` / ``method``):
        ``<slot>/v<n>.png`` for each generation/edit/variant + a single
        ``meta.yaml`` recording prompt, model, parent version, and
        cite-related context. Separate from ``drafts/figures/`` which
        only holds figures the user explicitly attached to a draft.
        """
        return self.paic_dir / "figures"

    @property
    def templates_dir(self) -> Path:
        """Project-local LaTeX templates (§19). One directory per template,
        e.g. ``.paic/templates/iclr2026/{main.tex.j2, *.sty, ...}``."""
        return self.paic_dir / "templates"

    @property
    def state_dir(self) -> Path:
        return self.paic_dir / "state"

    @property
    def checkpoints_db(self) -> Path:
        return self.state_dir / "checkpoints.sqlite"

    @property
    def runs_yaml(self) -> Path:
        return self.state_dir / "runs.yaml"

    @property
    def logs_dir(self) -> Path:
        return self.paic_dir / "logs"


def find_project_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a PAI-C **project** directory.

    A directory qualifies as a project root if it contains ``.paic/project.yaml``.
    The plain existence of a ``.paic/`` folder is *not* enough: the global
    PAI-C workspace (``~/.paic/``) is also called ``.paic/`` but holds no
    ``project.yaml``, and we don't want walking up from a tmp dir under
    ``~/`` to misidentify the home as a project.
    """
    start = start.expanduser().resolve()
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".paic" / "project.yaml").is_file():
            return candidate
    return None


def resolve_project(project_dir: str | Path | None) -> ProjectPaths:
    """Resolve a project either from an explicit dir or by walking up from cwd."""
    if project_dir is not None:
        root = Path(project_dir).expanduser().resolve()
    else:
        found = find_project_root(Path.cwd())
        if found is None:
            raise FileNotFoundError(
                "No .paic project found at or above the current directory. "
                "Run `/paic-init` first."
            )
        root = found
    return ProjectPaths(root=root)


def ensure_project_layout(paths: ProjectPaths) -> None:
    """Create all expected subdirectories under ``.paic/`` (idempotent)."""
    paths.paic_dir.mkdir(parents=True, exist_ok=True)
    for sub in PROJECT_LAYOUT:
        (paths.paic_dir / sub).mkdir(parents=True, exist_ok=True)

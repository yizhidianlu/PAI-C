"""LaTeX template filler — v0.1 (with §19 user-template support).

Renders a venue-specific ``main.tex`` skeleton plus a shared set of section
files from an ``IdeaCard`` + ``ExperimentPlan`` + the project's library.

The output is a directory laid out as:

```
<project>/.paic/drafts/
├── main.tex                  # the venue skeleton
├── refs.bib                  # auto-generated from library/selected.yaml
├── sections/
│   ├── 00_abstract.tex
│   ├── 01_intro.tex
│   ├── 02_related.tex
│   ├── 03_method.tex
│   ├── 04_experiments.tex
│   └── 05_conclusion.tex
├── figures/                  # placeholder for the user's figures
└── <static assets from the template>     # .sty / .cls / .bst / .pdf, …
```

§19: when the project has user-supplied templates under
``<project>/.paic/templates/<name>/``, those are also discoverable through
``paic.latex.registry`` and override built-ins on name collision. Static
assets (.sty / .cls / .bst / images) bundled with a user template are
auto-copied to ``drafts/`` so the user no longer has to drop them in by
hand before LaTeX compilation.
"""

from __future__ import annotations

import re
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from paic.latex.registry import (
    TemplateRecord,
    resolve_template,
    shared_sections_root,
)
from paic.latex.static_assets import copy_static_assets
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, write_text

# Built-in venue names — kept for backward compatibility with code that
# still imports VENUES (e.g. legacy test code). The authoritative source
# of available templates is now ``registry.discover_templates``.
VENUES: tuple[str, ...] = ("cvpr", "neurips", "ieee")

SECTIONS: tuple[str, ...] = (
    "00_abstract",
    "01_intro",
    "02_related",
    "03_method",
    "04_experiments",
    "05_conclusion",
)


def _make_env(record: TemplateRecord) -> Environment:
    """Build a jinja env with the right precedence chain for ``record``.

    Loader search order (first match wins):
      1. The template's own root — ``main.tex.j2`` lives here.
      2. The template's own ``sections/`` — lets a user override individual sections.
      3. The shared ``_shared/sections/`` pool — fallback for any unoverridden section.
    """
    return Environment(
        loader=FileSystemLoader([
            record.root,
            record.root / "sections",
            shared_sections_root() / "sections",
        ]),
        autoescape=select_autoescape(disabled_extensions=("tex", "j2")),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )


def _slug(value: str | None) -> str:
    if not value:
        return "ref"
    s = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return s or "ref"


def _cite_key(record: dict[str, Any]) -> str:
    """Stable BibTeX key for a paper record from library/selected.yaml."""
    if record.get("arxiv_id"):
        return f"arxiv_{_slug(record['arxiv_id'])}"
    if record.get("doi"):
        return f"doi_{_slug(record['doi'])}"
    if record.get("s2_id"):
        return f"s2_{_slug(record['s2_id'])}"
    title = record.get("title") or "ref"
    year = record.get("year") or "noyear"
    return f"{_slug(title.split()[0] if title else 'ref')}_{year}"


def _bib_entry(record: dict[str, Any]) -> str:
    key = _cite_key(record)
    title = record.get("title", "Untitled")
    authors = record.get("authors") or ["Anonymous"]
    author_field = " and ".join(authors)
    year = record.get("year") or "n.d."
    venue = record.get("venue") or ""
    parts = [
        f"@misc{{{key},",
        f"  author = {{{author_field}}},",
        f"  title = {{{title}}},",
        f"  year = {{{year}}},",
    ]
    if venue:
        parts.append(f"  howpublished = {{{venue}}},")
    if record.get("arxiv_id"):
        parts.append(f"  eprint = {{{record['arxiv_id']}}},")
        parts.append("  archivePrefix = {arXiv},")
    if record.get("doi"):
        parts.append(f"  doi = {{{record['doi']}}},")
    parts.append("}")
    return "\n".join(parts)


def _build_refs(papers: list[dict[str, Any]]) -> tuple[str, dict[str, dict[str, Any]]]:
    """Return (bibtex_text, key_to_record_map)."""
    by_key: dict[str, dict[str, Any]] = {}
    chunks: list[str] = []
    for p in papers:
        key = _cite_key(p)
        if key in by_key:
            continue
        by_key[key] = p
        chunks.append(_bib_entry(p))
    return "\n\n".join(chunks) + ("\n" if chunks else ""), by_key


def _build_cited_groups(
    grounded_in: list[str], by_key: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Group cited papers for the related-work section.

    MVP: a single bucket called "Foundational" containing every paper that the
    idea is grounded in. The user is expected to re-bucket by theme manually.
    """
    foundational: list[dict[str, Any]] = []
    by_id: dict[str, str] = {}  # original_id -> cite_key
    for record in by_key.values():
        for k in ("arxiv_id", "doi", "s2_id"):
            v = record.get(k)
            if v:
                by_id[str(v)] = _cite_key(record)

    for ref_id in grounded_in:
        cite_key = by_id.get(str(ref_id))
        if not cite_key:
            continue
        record = by_key[cite_key]
        foundational.append(
            {
                "cite_key": cite_key,
                "title": record.get("title", "?"),
                "one_line": (record.get("abstract") or "")[:160],
            }
        )
    if not foundational:
        return []
    return [{"theme": "Foundational", "papers": foundational}]


def _resolve_idea(paths: ProjectPaths, idea_id: str) -> dict[str, Any]:
    path = paths.ideas_dir / f"{idea_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Idea not found: {idea_id}")
    return load_yaml(path) or {}


def _resolve_experiment(paths: ProjectPaths, experiment_id: str | None) -> dict[str, Any]:
    if experiment_id:
        path = paths.experiments_dir / f"{experiment_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Experiment not found: {experiment_id}")
        return load_yaml(path) or {}
    # No experiment supplied — render with empty stubs.
    return {
        "research_questions": [],
        "hypotheses": [],
        "datasets": [],
        "baselines": [],
        "proposed_method": "TODO: describe the method.",
        "metrics": [],
        "ablations": [],
        "compute_budget": None,
        "success_criteria": [],
        "threats_to_validity": [],
        "timeline_weeks": None,
    }


# Built-in templates reference upstream .cls / .sty files but cannot ship them
# (license / vendor distribution constraints). When fill_draft copies zero
# assets, ``static_assets_missing_warning`` lists the expected files so the
# user knows compile will fail until they drop them into ``.paic/drafts/``.
_BUILTIN_VENUE_ASSETS: dict[str, tuple[str, ...]] = {
    "ieee": ("IEEEtran.cls",),
    "neurips": ("neurips_2024.sty",),
    "cvpr": ("cvpr.sty",),
}


def fill_draft(
    paths: ProjectPaths,
    *,
    template: str,
    idea_id: str,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Render the resolved template + section files into ``drafts/``.

    ``template`` is resolved via the registry: project-local templates under
    ``<project>/.paic/templates/<name>/`` win over built-ins on name collision.

    Returns ``{"main_tex", "refs_bib", "sections_created", "bib_keys", "template",
    "template_kind", "idea_id", "experiment_id", "static_assets_copied",
    "static_assets_skipped", "static_assets_missing_warning"}``. The last
    field is non-empty only for built-in venue templates whose required
    .cls / .sty file isn't bundled (license-constrained), prompting the
    SKILL to tell the user to drop the file into ``.paic/drafts/``.

    Raises:
        TemplateNotFound: when ``template`` doesn't match any built-in or
            project-local template. Caller should surface ``exc.available``.
    """
    record = resolve_template(template, paths)

    idea = _resolve_idea(paths, idea_id)
    experiment = _resolve_experiment(paths, experiment_id)
    project_meta = load_yaml(paths.project_yaml) or {}

    selected = load_yaml(paths.selected_yaml) or {}
    papers = list(selected.get("papers") or []) if isinstance(selected, dict) else []
    refs_bib, by_key = _build_refs(papers)
    cited_groups = _build_cited_groups(idea.get("grounded_in", []) or [], by_key)

    env = _make_env(record)

    drafts_dir = paths.drafts_dir
    drafts_dir.mkdir(parents=True, exist_ok=True)
    sections_out_dir = drafts_dir / "sections"
    sections_out_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "figures").mkdir(parents=True, exist_ok=True)

    context = {
        "project": project_meta,
        "idea": idea,
        "experiment": experiment,
        "cited_papers": cited_groups,
    }

    sections_created: list[str] = []
    for name in SECTIONS:
        # Loader chain (registry._make_env) tries the template's own
        # sections/<name>.tex.j2 first, then falls back to the shared pool.
        rendered = env.get_template(f"{name}.tex.j2").render(**context)
        out_path = sections_out_dir / f"{name}.tex"
        write_text(out_path, rendered)
        sections_created.append(str(out_path))

    main_rendered = env.get_template("main.tex.j2").render(**context)
    main_path = drafts_dir / "main.tex"
    write_text(main_path, main_rendered)

    write_text(drafts_dir / "refs.bib", refs_bib)

    # Copy non-jinja static assets (.sty / .cls / .bst / .pdf / …) bundled
    # with the template so the user no longer has to manually drop them in.
    asset_report = copy_static_assets(record.root, drafts_dir)

    # Built-in venue templates reference .cls / .sty files that they don't
    # bundle (license). Surface the gap so latex compile failures are
    # diagnosed up front rather than at the build step.
    missing_warning: list[str] = []
    if record.kind == "builtin" and not asset_report["copied"]:
        expected = _BUILTIN_VENUE_ASSETS.get(record.name, ())
        for filename in expected:
            if not (drafts_dir / filename).is_file():
                missing_warning.append(filename)

    return {
        "main_tex": str(main_path),
        "refs_bib": str(drafts_dir / "refs.bib"),
        "sections_created": sections_created,
        "bib_keys": sorted(by_key.keys()),
        "template": record.name,
        "template_kind": record.kind,
        "idea_id": idea_id,
        "experiment_id": experiment_id,
        "static_assets_copied": asset_report["copied"],
        "static_assets_skipped": asset_report["skipped"],
        "static_assets_missing_warning": missing_warning,
    }

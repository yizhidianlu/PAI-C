"""Draft tools: fill (v0.1, §1), §19 user-template support, polish (v0.2, §20).

v0.3 (full draft compose) lands in a later phase.
"""

from __future__ import annotations

import shutil
from typing import Any

import yaml

from paic.config import load_config
from paic.latex.compose import (
    VALID_MODES as COMPOSE_VALID_MODES,
)
from paic.latex.compose import (
    compose_section,
    persist_composed,
)
from paic.latex.filler import fill_draft
from paic.latex.overleaf_sync import mirror_drafts_to_overleaf
from paic.latex.polish import (
    VALID_MODES,
    persist_polished,
    polish_section,
)
from paic.latex.registry import (
    TemplateNotFound,
    builtin_root,
    discover_templates,
)
from paic.llm.host import build_host_directive
from paic.llm.router import LLMRouter
from paic.workspace.paths import resolve_project

# Host-orchestration directive returned when routing.overrides.draft_polish
# is set to "host". The main Claude Code conversation generates the polished
# LaTeX using the prompt + context we hand it, then calls
# paic_draft_polish_persist with the result.
_POLISH_HOST_INSTRUCTIONS = (
    "PAI-C has resolved the section content and idea/experiment context. "
    "Polish the LaTeX in `original` according to `mode` (and `instruction` "
    "if present). Preserve every \\cite{}, \\ref{}, \\label{}; do not "
    "introduce new cite keys; keep \\begin/\\end balanced. Output ONLY the "
    "polished LaTeX, no fences. Then call mcp__paic__paic_draft_polish_persist "
    "with the polished text + original_hash."
)

_COMPOSE_HOST_INSTRUCTIONS = (
    "PAI-C has resolved the idea / experiment / library context. Compose a "
    "complete LaTeX section per the `user_prompt` below. Use ONLY \\cite{} "
    "keys from the 'Library available for citation' list — anything else "
    "will be rejected. Do NOT fabricate experimental results / numbers. "
    "Output ONLY the LaTeX section, no fences. Then call "
    "mcp__paic__paic_draft_compose_persist with the composed text + "
    "original_hash."
)


def draft_fill_tool(
    project_dir: str,
    template: str,
    idea_id: str,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Fill ``template`` (built-in or project-local) and write to ``drafts/``."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    try:
        return fill_draft(
            paths,
            template=template,
            idea_id=idea_id,
            experiment_id=experiment_id,
        )
    except TemplateNotFound:
        return {
            "error": "unknown_template",
            "got": template,
            "available": [
                {"name": r.name, "kind": r.kind, "display_name": r.display_name or r.name}
                for r in discover_templates(paths)
            ],
            "hint": (
                f"Run paic_draft_scaffold(name='{template}', base='neurips') to "
                "create a new project-local template based on the built-in NeurIPS "
                "skeleton, then edit .paic/templates/{name}/main.tex.j2 to match "
                "your venue."
            ),
        }
    except FileNotFoundError as exc:
        return {"error": "not_found", "detail": str(exc)}


def draft_list_templates_tool(project_dir: str) -> dict[str, Any]:
    """List every template available to ``project_dir`` — built-in + project-local."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    records = discover_templates(paths)
    return {
        "project_dir": str(paths.root),
        "templates_dir": str(paths.templates_dir),
        "templates": [r.to_dict() for r in records],
    }


def draft_scaffold_tool(
    project_dir: str,
    name: str,
    base: str = "neurips",
) -> dict[str, Any]:
    """Create a project-local template by copying a built-in as a starting point.

    Writes ``<project>/.paic/templates/<name>/{main.tex.j2, template.yaml}``.
    The user then edits ``main.tex.j2`` (swap ``\\usepackage{...}`` etc.) and
    drops the venue's ``.sty`` / ``.cls`` files alongside before calling
    ``paic_draft_fill``.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    name = name.strip()
    if not name:
        return {"error": "invalid_name", "detail": "template name must be non-empty"}
    if "/" in name or "\\" in name or name.startswith("."):
        return {
            "error": "invalid_name",
            "detail": "template name must not contain path separators or start with '.'",
        }

    base_dir = builtin_root() / base
    if not (base_dir / "main.tex.j2").is_file():
        builtins = sorted(
            d.name for d in builtin_root().iterdir()
            if d.is_dir() and (d / "main.tex.j2").is_file()
        )
        return {"error": "unknown_base", "got": base, "available": builtins}

    target_dir = paths.templates_dir / name
    if target_dir.exists() and any(target_dir.iterdir()):
        return {
            "error": "template_already_exists",
            "name": name,
            "root": str(target_dir),
        }
    target_dir.mkdir(parents=True, exist_ok=True)

    # Copy main.tex.j2 verbatim — the user edits it next.
    src_main = base_dir / "main.tex.j2"
    dst_main = target_dir / "main.tex.j2"
    shutil.copyfile(src_main, dst_main)

    # Seed an empty template.yaml so the user knows where to fill metadata.
    metadata = {
        "display_name": name,
        "target_venue": None,
        "description": (
            f"Project-local template scaffolded from built-in '{base}'. "
            "Edit main.tex.j2 to swap the venue style file and drop the venue's "
            ".sty / .cls / .bst alongside this file."
        ),
    }
    (target_dir / "template.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    return {
        "name": name,
        "root": str(target_dir),
        "base": base,
        "files_created": [
            str(dst_main),
            str(target_dir / "template.yaml"),
        ],
        "next_steps": [
            f"Edit {dst_main} — swap `\\usepackage[final]{{neurips_2024}}` "
            "and the title/author block for your venue's equivalents.",
            f"Drop the venue's static files (.sty / .cls / .bst / images) "
            f"into {target_dir}/ — fill copies them into drafts/ automatically.",
            f"Run paic_draft_fill(template='{name}', idea_id=...) to render.",
        ],
    }


# --- v0.2 paragraph polish (§20) ------------------------------------------


def draft_polish_tool(
    project_dir: str,
    section: str,
    mode: str = "clarify",
    instruction: str | None = None,
    idea_id: str | None = None,
    experiment_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Polish one section file. See ``paic.latex.polish.polish_section``."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    if mode not in VALID_MODES:
        return {
            "error": "invalid_mode",
            "got": mode,
            "valid_modes": list(VALID_MODES),
        }

    # Host orchestration check — same pattern as summarize (§16). If draft_polish
    # is routed to "host", the main Claude Code conversation does the LLM work
    # and calls paic_draft_polish_persist to write the result.
    from paic.config import load_config
    cfg = load_config()
    router = LLMRouter(cfg)
    if router.is_host_orchestrated("draft_polish"):
        from paic.latex.polish import (
            _format_user_prompt,
            _hash,
            resolve_section_path,
        )
        from paic.workspace.store import load_yaml

        target = resolve_section_path(paths, section)
        if not target.is_file():
            return {
                "error": "section_not_found",
                "section": section,
                "looked_at": str(target),
            }
        original = target.read_text(encoding="utf-8")
        if not original.strip():
            return {"error": "section_empty", "section": str(target)}

        idea = experiment = None
        if mode == "expand":
            if idea_id and (paths.ideas_dir / f"{idea_id}.yaml").is_file():
                idea = load_yaml(paths.ideas_dir / f"{idea_id}.yaml")
            if experiment_id and (paths.experiments_dir / f"{experiment_id}.yaml").is_file():
                experiment = load_yaml(paths.experiments_dir / f"{experiment_id}.yaml")

        return build_host_directive(
            node="draft_polish",
            instructions=_POLISH_HOST_INSTRUCTIONS,
            user_prompt=_format_user_prompt(
                mode=mode,
                instruction=instruction,
                section_content=original,
                idea=idea,
                experiment=experiment,
            ),
            next_tool="mcp__paic__paic_draft_polish_persist",
            original_hash=_hash(original),
            metadata={
                "section": str(target),
                "polish_mode": mode,
                "instruction": instruction,
                "original": original,
            },
        ).to_dict()

    return polish_section(
        paths,
        section=section,
        mode=mode,
        instruction=instruction,
        idea_id=idea_id,
        experiment_id=experiment_id,
        dry_run=dry_run,
    )


def draft_polish_persist_tool(
    project_dir: str,
    section: str,
    polished: str,
    original_hash: str,
) -> dict[str, Any]:
    """Persist a host-orchestration-generated polished section. LLM-free."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    return persist_polished(
        paths,
        section=section,
        polished=polished,
        original_hash=original_hash,
    )


# --- v0.3 full draft compose (§21) ----------------------------------------


def draft_compose_tool(
    project_dir: str,
    section: str,
    mode: str = "from_stub",
    idea_id: str | None = None,
    experiment_id: str | None = None,
    target_words: int | None = None,
    instruction: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compose a full section from idea + experiment + library."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    if mode not in COMPOSE_VALID_MODES:
        return {
            "error": "invalid_mode",
            "got": mode,
            "valid_modes": list(COMPOSE_VALID_MODES),
        }

    from paic.config import load_config
    cfg = load_config()
    router = LLMRouter(cfg)
    if router.is_host_orchestrated("draft_compose"):
        from paic.latex.compose import (
            _build_library_context,
            _format_user_prompt,
            _section_canonical_name,
        )
        from paic.latex.polish import _hash, resolve_section_path
        from paic.workspace.store import load_yaml

        target = resolve_section_path(paths, section)
        if not target.is_file():
            return {
                "error": "section_not_found",
                "section": section,
                "looked_at": str(target),
            }
        original = target.read_text(encoding="utf-8")
        section_name = _section_canonical_name(target)
        section_stub = "" if mode == "from_scratch" else original

        idea = experiment = None
        if idea_id and (paths.ideas_dir / f"{idea_id}.yaml").is_file():
            idea = load_yaml(paths.ideas_dir / f"{idea_id}.yaml")
        if experiment_id and (paths.experiments_dir / f"{experiment_id}.yaml").is_file():
            experiment = load_yaml(paths.experiments_dir / f"{experiment_id}.yaml")

        paper_plan: dict | None = None
        if paths.paper_plan_yaml.is_file():
            loaded = load_yaml(paths.paper_plan_yaml)
            if isinstance(loaded, dict):
                paper_plan = loaded
        library_md, library_keys, _ = _build_library_context(
            paths,
            section_name=section_name,
            paper_plan=paper_plan,
            idea=idea,
            experiment=experiment,
        )
        if not library_keys and section_name in {"01_intro", "02_related", "04_experiments"}:
            return {
                "error": "empty_library",
                "section": str(target),
                "section_name": section_name,
            }

        return build_host_directive(
            node="draft_compose",
            instructions=_COMPOSE_HOST_INSTRUCTIONS,
            user_prompt=_format_user_prompt(
                section_name=section_name,
                mode=mode,
                instruction=instruction,
                target_words=target_words,
                section_stub=section_stub,
                idea=idea,
                experiment=experiment,
                library_md=library_md,
                paper_plan=paper_plan,
            ),
            next_tool="mcp__paic__paic_draft_compose_persist",
            original_hash=_hash(original),
            metadata={
                "section": str(target),
                "section_name": section_name,
                "compose_mode": mode,
                "instruction": instruction,
                "target_words": target_words,
                "original": original,
                "library_size": len(library_keys),
                "library_cite_keys": sorted(library_keys),
            },
        ).to_dict()

    return compose_section(
        paths,
        section=section,
        mode=mode,
        idea_id=idea_id,
        experiment_id=experiment_id,
        target_words=target_words,
        instruction=instruction,
        dry_run=dry_run,
    )


def draft_compose_persist_tool(
    project_dir: str,
    section: str,
    composed: str,
    original_hash: str,
) -> dict[str, Any]:
    """Persist a host-orchestration-generated composed section. LLM-free."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    return persist_composed(
        paths,
        section=section,
        composed=composed,
        original_hash=original_hash,
    )


def draft_sync_overleaf_tool(
    project_dir: str,
    target_dir: str | None = None,
    direction: str = "auto",
    dry_run: bool = False,
    conflict_strategy: str | None = None,
    confirm_deletions: bool = False,
) -> dict[str, Any]:
    """Bidirectional sync between drafts/ and the Overleaf-linked Dropbox folder.

    Standard flow: SKILL calls once with ``dry_run=True`` to preview
    pushed/pulled/conflicts/deletions_pending, asks the user, then calls a
    second time without dry_run (and with ``confirm_deletions=True`` if the
    user authorized propagating any unilateral deletes).

    See :func:`paic.latex.overleaf_sync.mirror_drafts_to_overleaf` for the
    full state machine and conflict-resolution semantics.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    if direction not in ("auto", "push_only", "pull_only"):
        return {
            "error": "invalid_direction",
            "got": direction,
            "valid": ["auto", "push_only", "pull_only"],
        }
    if conflict_strategy is not None and conflict_strategy not in (
        "keep_both", "local_wins", "remote_wins", "newer_wins"
    ):
        return {
            "error": "invalid_conflict_strategy",
            "got": conflict_strategy,
            "valid": ["keep_both", "local_wins", "remote_wins", "newer_wins"],
        }

    cfg = load_config()
    return mirror_drafts_to_overleaf(
        paths,
        cfg.overleaf,
        target_dir=target_dir,
        direction=direction,  # type: ignore[arg-type]
        dry_run=dry_run,
        conflict_strategy=conflict_strategy,  # type: ignore[arg-type]
        confirm_deletions=confirm_deletions,
    )

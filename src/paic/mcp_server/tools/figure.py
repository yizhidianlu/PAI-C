"""MCP tools for the /paic-figure pipeline (Phase 1).

5 endpoints exposed:

- ``paic_figure_plan`` — analyze paper context, propose ≤N figure slots
- ``paic_figure_generate`` — render an image for one slot
- ``paic_figure_edit`` — refine a slot's latest version with an instruction
- ``paic_figure_variant`` — produce N variations of a slot's latest version
- ``paic_figure_list`` — return plan + per-slot version counts

All tools return ``dict[str, Any]`` with an ``error`` field on failure
(matches the rest of the PAI-C MCP surface). All tools short-circuit with
``error: images_disabled`` when ``providers.images.enabled=false``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID

from paic.images.backend import (
    ImageBackendUnavailable,
    OpenAICompatibleImageBackend,
    get_default_image_backend,
)
from paic.images.planner import FigureSlot, plan_figures
from paic.images.prompt import synthesize_image_prompt
from paic.images.storage import (
    figure_latex_snippet,
    latest_version,
    latest_version_bytes,
    list_versions,
    load_meta,
    save_version,
    version_bytes,
)
from paic.llm.client import LLMClient, get_default_client
from paic.workspace.paths import ProjectPaths, ensure_project_layout, resolve_project
from paic.workspace.store import load_yaml, save_yaml


# ---------------------------------------------------------------------- helpers
def _open_project(project_dir: str) -> ProjectPaths | dict[str, Any]:
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {
            "error": "project_not_initialized",
            "project_dir": str(paths.root),
            "hint": "Run /paic-init first.",
        }
    ensure_project_layout(paths)
    return paths


def _plan_path(paths: ProjectPaths) -> Path:
    return paths.figures_dir / "_plan.yaml"


def _load_plan(paths: ProjectPaths) -> dict[str, Any] | None:
    p = _plan_path(paths)
    if not p.exists():
        return None
    return load_yaml(p, default={}) or {}


def _slot_from_plan(plan: dict[str, Any], slot_name: str) -> FigureSlot | None:
    for entry in plan.get("slots") or []:
        if entry.get("slot") == slot_name:
            return FigureSlot(
                slot=entry["slot"],
                kind=entry.get("kind", "concept"),
                section_hint=entry.get("section_hint", "intro"),
                position_hint=entry.get("position_hint", ""),
                scene_description=entry.get("scene_description", ""),
                caption_hint=entry.get("caption_hint", ""),
                rationale=entry.get("rationale", ""),
            )
    return None


def _backend_or_error(
    backend: OpenAICompatibleImageBackend | None,
) -> OpenAICompatibleImageBackend | dict[str, Any]:
    if backend is not None:
        return backend
    try:
        return get_default_image_backend()
    except ImageBackendUnavailable as exc:
        return {
            "error": "images_disabled",
            "detail": str(exc),
            "hint": (
                "Add a providers.images block to ~/.paic/config.yaml with "
                "enabled: true, model: gpt-image-1, base_url: <relay>, "
                "api_key_env: <env var>. See docs/config.yaml.example."
            ),
        }


# ---------------------------------------------------------------------- plan
def figure_plan(
    project_dir: str,
    *,
    draft_path: str | None = None,
    max_figures: int = 4,
    overwrite: bool = False,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Analyze paper context and write the figure plan to disk.

    The plan is saved to ``<project>/.paic/figures/_plan.yaml``. By default
    refuses to clobber an existing plan; pass ``overwrite=True`` to replace.
    Returns the plan body so the Skill can render it without a second read.
    """
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    plan_file = _plan_path(paths)
    if plan_file.exists() and not overwrite:
        return {
            "error": "plan_exists",
            "plan_path": str(plan_file),
            "hint": "Pass overwrite=True to replace, or edit _plan.yaml manually.",
        }

    draft_p: Path | None = None
    if draft_path:
        draft_p = Path(draft_path).expanduser().resolve()
        if not draft_p.exists():
            return {"error": "draft_not_found", "draft_path": str(draft_p)}

    client = llm or get_default_client()
    try:
        slots = plan_figures(
            client, paths=paths, draft_path=draft_p, max_figures=max_figures
        )
    except Exception as exc:  # noqa: BLE001 — bubble LLM/router errors as structured
        return {"error": "plan_failed", "detail": repr(exc)}

    # §quality phase 9 — verify every contribution claim has a figure /
    # table / algorithm binding (or an explicit no_visual_reason).
    from paic.images.planner import verify_claim_coverage
    coverage_warnings = verify_claim_coverage(paths, slots)

    plan_payload = {
        "plan_id": str(ULID()),
        "draft_path": str(draft_p) if draft_p else None,
        "created_at": datetime.now(UTC).isoformat(),
        "slots": [s.to_dict() for s in slots],
        "coverage_warnings": coverage_warnings,
    }
    paths.figures_dir.mkdir(parents=True, exist_ok=True)
    save_yaml(plan_file, plan_payload)
    return {
        "plan_id": plan_payload["plan_id"],
        "plan_path": str(plan_file),
        "slot_count": len(slots),
        "slots": plan_payload["slots"],
        "coverage_warnings": coverage_warnings,
    }


# ---------------------------------------------------------------------- generate
def figure_generate(
    project_dir: str,
    slot: str,
    *,
    description: str | None = None,
    free_slot: bool = False,
    n: int = 1,
    llm: LLMClient | None = None,
    backend: OpenAICompatibleImageBackend | None = None,
) -> dict[str, Any]:
    """Render an image for one slot and persist it as ``v<n>.png``.

    By default the slot must exist in ``_plan.yaml``. Pass ``free_slot=True``
    to generate a one-off slot without a planning step (description required).
    """
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    plan = _load_plan(paths)
    slot_obj = _slot_from_plan(plan or {}, slot) if plan else None
    if slot_obj is None:
        if not free_slot:
            return {
                "error": "slot_not_in_plan",
                "slot": slot,
                "hint": (
                    "Run paic_figure_plan first, or pass free_slot=True with "
                    "a description= argument."
                ),
            }
        if not description:
            return {
                "error": "description_required",
                "hint": "free_slot=True requires a non-empty description.",
            }
        slot_obj = FigureSlot(
            slot=slot,
            kind="concept",
            section_hint="",
            position_hint="",
            scene_description=description,
            caption_hint="",
            rationale="",
        )

    backend_or_err = _backend_or_error(backend)
    if isinstance(backend_or_err, dict):
        return backend_or_err
    backend = backend_or_err

    client = llm or get_default_client()
    try:
        image_prompt = synthesize_image_prompt(
            client, slot_obj, extra_instruction=description if not free_slot else None
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": "prompt_synthesis_failed", "detail": repr(exc)}

    try:
        images = backend.generate(image_prompt, n=n)
    except ImageBackendUnavailable as exc:
        return {"error": "image_backend_failed", "detail": str(exc)}

    versions: list[dict[str, Any]] = []
    for img in images:
        label, png_path = save_version(
            paths,
            slot_obj.slot,
            img.png_bytes,
            kind="generate",
            prompt=image_prompt,
            model=backend.model,
            parent_version=None,
            extra={"revised_prompt": img.revised_prompt} if img.revised_prompt else None,
        )
        versions.append(
            {
                "version": label,
                "png_path": str(png_path),
                "latex_snippet": figure_latex_snippet(
                    slot_obj.slot, label, caption=slot_obj.caption_hint
                ),
            }
        )
    primary = versions[0]
    return {
        "slot": slot_obj.slot,
        "version": primary["version"],
        "png_path": primary["png_path"],
        "latex_snippet": primary["latex_snippet"],
        "image_prompt": image_prompt,
        "all_versions": versions if len(versions) > 1 else None,
    }


# ---------------------------------------------------------------------- edit
def figure_edit(
    project_dir: str,
    slot: str,
    instruction: str,
    *,
    parent_version: str | None = None,
    backend: OpenAICompatibleImageBackend | None = None,
) -> dict[str, Any]:
    """Edit an existing slot version with a natural-language instruction.

    Default parent is the latest version. Refuses to operate on a slot with
    no existing versions (must call ``figure_generate`` first).
    """
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    parent = parent_version or latest_version(paths, slot)
    if parent is None:
        return {
            "error": "no_existing_version",
            "slot": slot,
            "hint": "Run paic_figure_generate first to create v1.",
        }
    try:
        parent_bytes = version_bytes(paths, slot, parent)
    except FileNotFoundError:
        return {
            "error": "parent_version_not_found",
            "slot": slot,
            "parent_version": parent,
        }

    backend_or_err = _backend_or_error(backend)
    if isinstance(backend_or_err, dict):
        return backend_or_err
    backend = backend_or_err

    if not backend.supports_edit:
        return {
            "error": "edit_not_supported",
            "model": backend.model,
            "hint": "Switch to gpt-image-1 or dall-e-2 in providers.images.model.",
        }

    try:
        edited = backend.edit(parent_bytes, instruction)
    except ImageBackendUnavailable as exc:
        return {"error": "image_backend_failed", "detail": str(exc)}

    label, png_path = save_version(
        paths,
        slot,
        edited.png_bytes,
        kind="edit",
        prompt=instruction,
        model=backend.model,
        parent_version=parent,
        extra={"revised_prompt": edited.revised_prompt} if edited.revised_prompt else None,
    )
    # Caption hint comes from the original plan if available.
    plan = _load_plan(paths)
    slot_obj = _slot_from_plan(plan or {}, slot)
    caption = slot_obj.caption_hint if slot_obj else None
    return {
        "slot": slot,
        "version": label,
        "png_path": str(png_path),
        "parent_version": parent,
        "latex_snippet": figure_latex_snippet(slot, label, caption=caption),
    }


# ---------------------------------------------------------------------- variant
def figure_variant(
    project_dir: str,
    slot: str,
    *,
    n: int = 2,
    parent_version: str | None = None,
    backend: OpenAICompatibleImageBackend | None = None,
) -> dict[str, Any]:
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    parent = parent_version or latest_version(paths, slot)
    if parent is None:
        return {
            "error": "no_existing_version",
            "slot": slot,
            "hint": "Run paic_figure_generate first to create v1.",
        }
    parent_bytes = latest_version_bytes(paths, slot) if parent_version is None else version_bytes(
        paths, slot, parent
    )

    backend_or_err = _backend_or_error(backend)
    if isinstance(backend_or_err, dict):
        return backend_or_err
    backend = backend_or_err

    try:
        items = backend.variant(parent_bytes, n=n)
    except ImageBackendUnavailable as exc:
        return {"error": "image_backend_failed", "detail": str(exc)}

    plan = _load_plan(paths)
    slot_obj = _slot_from_plan(plan or {}, slot)
    caption = slot_obj.caption_hint if slot_obj else None

    versions = []
    for img in items:
        label, png_path = save_version(
            paths,
            slot,
            img.png_bytes,
            kind="variant",
            prompt="(variant of latest)",
            model=backend.model,
            parent_version=parent,
        )
        versions.append(
            {
                "version": label,
                "png_path": str(png_path),
                "latex_snippet": figure_latex_snippet(slot, label, caption=caption),
            }
        )
    return {"slot": slot, "parent_version": parent, "versions": versions}


# ---------------------------------------------------------------------- list
def figure_list(project_dir: str) -> dict[str, Any]:
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    plan = _load_plan(paths)
    slots_info: list[dict[str, Any]] = []
    seen: set[str] = set()
    if plan:
        for entry in plan.get("slots") or []:
            slot = entry["slot"]
            seen.add(slot)
            versions = list_versions(paths, slot)
            slots_info.append(
                {
                    "slot": slot,
                    "kind": entry.get("kind"),
                    "section_hint": entry.get("section_hint"),
                    "version_count": len(versions),
                    "latest_version": versions[-1] if versions else None,
                }
            )
    # Free slots (not in plan but with files on disk).
    if paths.figures_dir.exists():
        for sub in paths.figures_dir.iterdir():
            if sub.is_dir() and sub.name not in seen:
                versions = list_versions(paths, sub.name)
                if versions:
                    slots_info.append(
                        {
                            "slot": sub.name,
                            "kind": None,
                            "section_hint": None,
                            "version_count": len(versions),
                            "latest_version": versions[-1],
                            "free_slot": True,
                        }
                    )
    return {
        "plan_path": str(_plan_path(paths)) if plan else None,
        "plan_id": plan.get("plan_id") if plan else None,
        "slots": slots_info,
    }

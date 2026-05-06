"""LaTeX section polish — v0.2 (§20).

Reads a section file under ``drafts/sections/``, asks the configured LLM
backend to rewrite it according to a mode (tighten / clarify / formalize /
expand / proofread), validates the output structurally, then overwrites
the file with a timestamped ``.bak`` left alongside.

The host-orchestration branch (``routing.overrides.draft_polish: host``)
is wired in ``mcp_server.tools.draft_polish`` rather than here — this
module is the LLM-driven path.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paic.latex.guard import (
    strip_markdown_fence,
    validate_polished,
)
from paic.llm.prompts import load_prompt
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml

VALID_MODES: tuple[str, ...] = (
    "tighten",
    "clarify",
    "formalize",
    "expand",
    "proofread",
)

# Map common aliases / Chinese keywords / canonical section base names so
# users can say "polish 引言" and we resolve it to drafts/sections/01_intro.tex.
_SECTION_ALIASES: dict[str, str] = {
    "abstract": "00_abstract",
    "intro": "01_intro",
    "introduction": "01_intro",
    "related": "02_related",
    "related_work": "02_related",
    "method": "03_method",
    "methods": "03_method",
    "experiments": "04_experiments",
    "experiment": "04_experiments",
    "results": "04_experiments",
    "conclusion": "05_conclusion",
    "conclusions": "05_conclusion",
}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def resolve_section_path(paths: ProjectPaths, section: str) -> Path:
    """Map a section identifier to an absolute file path under drafts/sections/.

    Accepts:
      - canonical names: "00_abstract", "01_intro", …
      - aliases: "intro", "method", "results"
      - basename with .tex: "01_intro.tex"
      - relative path: "drafts/sections/01_intro.tex"
      - absolute path
    """
    target = section.strip()
    sections_dir = paths.drafts_dir / "sections"

    # Absolute path → use as-is
    candidate = Path(target)
    if candidate.is_absolute():
        return candidate

    # Drop trailing .tex if user included it
    if target.endswith(".tex"):
        target = target[:-4]
    # Strip leading drafts/sections/ if user included it
    for prefix in ("drafts/sections/", "sections/"):
        if target.startswith(prefix):
            target = target[len(prefix):]

    # Lowercase alias resolution
    alias_key = target.lower().replace("-", "_").replace(" ", "_")
    canonical = _SECTION_ALIASES.get(alias_key, target)
    return sections_dir / f"{canonical}.tex"


def _format_user_prompt(
    *,
    mode: str,
    instruction: str | None,
    section_content: str,
    idea: dict[str, Any] | None,
    experiment: dict[str, Any] | None,
) -> str:
    parts: list[str] = [f"Mode: {mode}"]
    if instruction:
        parts.append(f"Additional instruction: {instruction.strip()}")

    if mode == "expand" and (idea or experiment):
        parts.append("")
        parts.append("Idea / experiment context (use to fill TODO placeholders):")
        if idea:
            parts.append("Idea:")
            for key in (
                "title", "one_liner", "motivation", "proposed_approach",
                "novelty_claim", "expected_contribution",
            ):
                value = idea.get(key)
                if value:
                    parts.append(f"  - {key}: {value}")
        if experiment:
            parts.append("Experiment:")
            for key in (
                "research_questions", "hypotheses", "proposed_method",
                "metrics", "ablations", "success_criteria",
            ):
                value = experiment.get(key)
                if value:
                    parts.append(f"  - {key}: {value}")

    parts.append("")
    parts.append("Original section:")
    parts.append("---")
    parts.append(section_content)
    parts.append("---")
    parts.append("")
    parts.append("Output the polished section only. No fences, no commentary.")
    return "\n".join(parts)


def _make_diff(original: str, polished: str, label: str) -> str:
    diff_lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        polished.splitlines(keepends=True),
        fromfile=f"{label} (original)",
        tofile=f"{label} (polished)",
        n=2,
    )
    return "".join(diff_lines)


def _backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + f".bak.{_now_stamp()}")


def polish_section(
    paths: ProjectPaths,
    *,
    section: str,
    mode: str = "clarify",
    instruction: str | None = None,
    idea_id: str | None = None,
    experiment_id: str | None = None,
    dry_run: bool = False,
    llm=None,  # injected for tests
) -> dict[str, Any]:
    """End-to-end polish: read section, call LLM, validate, write back + bak.

    Returns the standard polish payload (see plan §20.4) on success or a
    structured error dict (``section_not_found`` / ``invalid_mode`` /
    ``section_empty`` / ``llm_unavailable`` / ``latex_validation_failed``)
    on failure.
    """
    if mode not in VALID_MODES:
        return {
            "error": "invalid_mode",
            "got": mode,
            "valid_modes": list(VALID_MODES),
        }

    target = resolve_section_path(paths, section)
    if not target.is_file():
        return {
            "error": "section_not_found",
            "section": section,
            "looked_at": str(target),
            "hint": (
                "Run paic_draft_fill first to populate drafts/sections/, or "
                "pass an existing section name (00_abstract, 01_intro, "
                "02_related, 03_method, 04_experiments, 05_conclusion)."
            ),
        }

    original = target.read_text(encoding="utf-8")
    if not original.strip():
        return {
            "error": "section_empty",
            "section": str(target),
            "hint": "Section file exists but is empty — fill it first.",
        }

    idea: dict[str, Any] | None = None
    experiment: dict[str, Any] | None = None
    if mode == "expand":
        if idea_id:
            idea_path = paths.ideas_dir / f"{idea_id}.yaml"
            if idea_path.is_file():
                idea = load_yaml(idea_path)
        if experiment_id:
            exp_path = paths.experiments_dir / f"{experiment_id}.yaml"
            if exp_path.is_file():
                experiment = load_yaml(exp_path)

    system_prompt = load_prompt("polish_section")
    user_prompt = _format_user_prompt(
        mode=mode,
        instruction=instruction,
        section_content=original,
        idea=idea,
        experiment=experiment,
    )

    # Lazy-import the LLM client so tests can stub at the module surface
    # without paying the import cost when mocking.
    if llm is None:
        from paic.llm.backends.base import LLMUnavailable
        from paic.llm.client import get_default_client
        try:
            llm = get_default_client()
        except LLMUnavailable as exc:
            return {"error": "llm_unavailable", "detail": str(exc)}

    try:
        response = llm.complete(
            system=system_prompt,
            user=user_prompt,
            max_tokens=4096,
            temperature=0.3,
            node="draft_polish",
        )
    except Exception as exc:
        # LLMUnavailable, network, anything else — surface as llm_unavailable
        # so the SKILL can offer remediation. Real failures should still log.
        from paic.llm.backends.base import LLMUnavailable
        if isinstance(exc, LLMUnavailable):
            return {"error": "llm_unavailable", "detail": str(exc)}
        raise

    polished = strip_markdown_fence(response.text).strip() + "\n"

    report = validate_polished(original, polished)
    if not report.ok:
        return {
            "error": "latex_validation_failed",
            "section": str(target),
            "mode": mode,
            "validation": report.to_dict(),
            "polished_preview": polished[:1500],
            "hint": (
                "The model's output failed structural checks (cite keys, "
                "begin/end, or braces). Re-run with a different mode, or "
                "tighten the instruction. Original file untouched."
            ),
        }

    diff = _make_diff(original, polished, target.name)
    original_hash = _hash(original)

    payload: dict[str, Any] = {
        "section": str(target),
        "mode": mode,
        "instruction": instruction,
        "original": original,
        "original_hash": original_hash,
        "polished": polished,
        "diff": diff,
        "validation": report.to_dict(),
        "wrote": False,
        "backup_path": None,
    }

    if dry_run:
        return payload

    # Write the backup first so an interrupted write leaves the .bak intact.
    backup = _backup_path(target)
    backup.write_text(original, encoding="utf-8")
    target.write_text(polished, encoding="utf-8")
    payload["wrote"] = True
    payload["backup_path"] = str(backup)
    return payload


def persist_polished(
    paths: ProjectPaths,
    *,
    section: str,
    polished: str,
    original_hash: str,
) -> dict[str, Any]:
    """Host-orchestration counterpart of ``polish_section``.

    Runs the same guard against ``polished``, but trusts the caller (the main
    Claude Code conversation) for the LLM step. ``original_hash`` is the
    sha256 we returned from the original ``paic_draft_polish`` call — we
    re-read the file and refuse to overwrite if its hash has drifted (user
    edited the file between polish and persist).
    """
    target = resolve_section_path(paths, section)
    if not target.is_file():
        return {
            "error": "section_not_found",
            "section": section,
            "looked_at": str(target),
        }

    original = target.read_text(encoding="utf-8")
    current_hash = _hash(original)
    if current_hash != original_hash:
        return {
            "error": "original_hash_mismatch",
            "section": str(target),
            "expected_hash": original_hash,
            "current_hash": current_hash,
            "hint": (
                "The section file has changed since paic_draft_polish was "
                "called. Re-run polish to pick up the current contents, "
                "or discard the in-progress polish."
            ),
        }

    polished_clean = strip_markdown_fence(polished).strip() + "\n"
    report = validate_polished(original, polished_clean)
    if not report.ok:
        return {
            "error": "latex_validation_failed",
            "section": str(target),
            "validation": report.to_dict(),
            "hint": (
                "Polished text failed structural validation. Original file "
                "untouched. Fix the polished output and resubmit."
            ),
        }

    diff = _make_diff(original, polished_clean, target.name)
    backup = _backup_path(target)
    backup.write_text(original, encoding="utf-8")
    target.write_text(polished_clean, encoding="utf-8")

    return {
        "section": str(target),
        "diff": diff,
        "validation": report.to_dict(),
        "backup_path": str(backup),
        "wrote": True,
    }

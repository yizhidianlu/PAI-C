"""``paic_passport_*`` MCP tools — emit boundary / resume / list ledger.

The Material Passport is opt-in (``passport.enable_reset_boundary: true``
in ``~/.paic/config.yaml``). When disabled, the emit tool short-circuits
with a hint instead of silently no-op'ing — that way users see why
their FULL checkpoint didn't produce a boundary tag.
"""

from __future__ import annotations

from typing import Any

from paic.config import load_config
from paic.passport.ledger import (
    LOCK_FILENAME,
    append_boundary,
    list_entries,
    load_ledger,
)
from paic.passport.lock import LockTimeout, passport_lock
from paic.passport.resume import (
    DoubleResumeError,
    HashMismatchError,
    PassportResumeError,
    resolve_resume,
)
from paic.workspace.paths import resolve_project


def passport_emit_tool(
    project_dir: str,
    *,
    stage: int | str,
    deliverables: list[str] | None = None,
    next_stage: int | str | None = None,
    pending_decision: dict | None = None,
    session_marker: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Append a ``kind="boundary"`` entry under lock.

    Returns the finalized entry dict including the 12-char ``hash``
    string the user can copy into a fresh session as
    ``resume_from_passport=<hash>``.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    cfg = load_config()
    if not cfg.passport.enable_reset_boundary:
        return {
            "error": "passport_disabled",
            "hint": (
                "Material Passport is opt-in. Set "
                "`passport.enable_reset_boundary: true` in ~/.paic/config.yaml "
                "and restart Claude Code to enable boundary emit."
            ),
        }

    paths.state_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = paths.passport_yaml
    lock_path = paths.passport_lock

    try:
        with passport_lock(lock_path, timeout_sec=cfg.passport.lock_timeout_sec):
            entry = append_boundary(
                ledger_path,
                stage=stage,
                deliverables=deliverables or [],
                next_stage=next_stage,
                pending_decision=pending_decision,
                session_marker=session_marker,
                notes=notes,
            )
    except LockTimeout as exc:
        return {
            "error": "passport_lock_timeout",
            "detail": str(exc),
            "hint": (
                "Another process is holding the passport lock. Investigate "
                "before retrying — lock_timeout_sec hit means a stuck peer, "
                "not contention."
            ),
        }

    return {
        "ok": True,
        "ledger_path": str(ledger_path),
        "entry": entry.model_dump(mode="json", exclude_none=True),
        "resume_command": f"resume_from_passport={entry.hash}",
        "human_instruction": (
            f"To continue this pipeline in a fresh Claude Code session, run "
            f"`/paic-resume passport={entry.hash}` — token-savings only "
            f"materialise across sessions, not within one."
        ),
    }


def passport_resume_tool(
    project_dir: str,
    *,
    hash: str,
    stage_override: int | str | None = None,
    mode_override: str | None = None,
    chosen_branch: str | None = None,
    session_marker: str | None = None,
) -> dict[str, Any]:
    """Resolve ``resume_from_passport=<hash>`` end-to-end under lock.

    Routing precedence (per ARS iron rule §8):

    1. boundary's ``pending_decision.options[chosen_branch]`` (if set)
    2. boundary's top-level ``next_stage`` (else)
    3. CLI overrides (``stage_override`` / ``mode_override``) replace
       the base values when supplied

    Errors:
    - ``passport_disabled``: passport.enable_reset_boundary=false
    - ``passport_no_ledger``: ledger file missing
    - ``hash_not_found``: no boundary matches the supplied hash
    - ``double_resume``: a prior resume entry already consumed it
    - ``pending_decision_required``: boundary has options but
      chosen_branch is None
    - ``unknown_branch``: chosen_branch not in options[*].value
    - ``passport_lock_timeout``: lock acquisition timed out
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    cfg = load_config()
    if not cfg.passport.enable_reset_boundary:
        return {
            "error": "passport_disabled",
            "hint": (
                "Material Passport is opt-in. Even resume requires the flag "
                "so we don't silently consume a boundary the user never "
                "intended to emit. Set passport.enable_reset_boundary: true."
            ),
        }

    if not paths.passport_yaml.is_file():
        return {
            "error": "passport_no_ledger",
            "ledger_path": str(paths.passport_yaml),
            "hint": "No prior boundaries to resume from.",
        }

    try:
        plan = resolve_resume(
            paths.passport_yaml,
            hash=hash,
            stage_override=stage_override,
            mode_override=mode_override,
            chosen_branch=chosen_branch,
            session_marker=session_marker,
            timeout_sec=cfg.passport.lock_timeout_sec,
        )
    except HashMismatchError as exc:
        return {"error": "hash_not_found", "detail": str(exc)}
    except DoubleResumeError as exc:
        return {"error": "double_resume", "detail": str(exc)}
    except PassportResumeError as exc:
        msg = str(exc)
        kind = (
            "pending_decision_required"
            if "pending_decision" in msg
            else "unknown_branch"
        )
        return {"error": kind, "detail": msg}
    except LockTimeout as exc:
        return {"error": "passport_lock_timeout", "detail": str(exc)}

    return {
        "ok": True,
        "ledger_path": str(paths.passport_yaml),
        "boundary_hash": plan.boundary.hash,
        "next_stage": plan.next_stage,
        "next_mode": plan.next_mode,
        "used_pending_decision": plan.used_pending_decision,
        "boundary_entry": plan.boundary.model_dump(mode="json", exclude_none=True),
        "resume_entry": plan.resume_entry.model_dump(mode="json", exclude_none=True),
        "instruction": (
            f"Resume acknowledged. Recovered stage = {plan.boundary.stage}; "
            f"next stage = {plan.next_stage}"
            + (f" (mode override: {plan.next_mode})" if plan.next_mode else "")
        ),
    }


def passport_list_tool(project_dir: str) -> dict[str, Any]:
    """Read-only ledger snapshot for /paic-status / /paic-resume listings."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    cfg = load_config()
    entries = list_entries(paths.passport_yaml) if paths.passport_yaml.is_file() else []
    awaiting_resume = _compute_awaiting_resume(entries)
    return {
        "ledger_path": str(paths.passport_yaml),
        "exists": paths.passport_yaml.is_file(),
        "enabled": cfg.passport.enable_reset_boundary,
        "entries": entries,
        "boundary_count": sum(1 for e in entries if e.get("kind") == "boundary"),
        "resume_count": sum(1 for e in entries if e.get("kind") == "resume"),
        "awaiting_resume": awaiting_resume,
    }


def _compute_awaiting_resume(entries: list[dict]) -> list[dict]:
    """Return boundary entries that have NOT been consumed by any resume.

    Single-pass algorithm (ARS protocol §"Computing awaiting_resume"):
    a boundary with hash H is awaiting resume iff no later resume entry
    carries ``consumes_hash == H``.
    """
    consumed: set[str] = set()
    for e in entries:
        if e.get("kind") == "resume":
            ch = e.get("consumes_hash")
            if ch:
                consumed.add(ch)
    out: list[dict] = []
    for e in entries:
        if e.get("kind") == "boundary" and e.get("hash") not in consumed:
            out.append(e)
    return out

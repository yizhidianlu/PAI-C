"""``resume_from_passport=<hash>`` resolution + double-resume prevention.

Pipeline (called from ``paic_passport_resume`` MCP tool):

1. Acquire passport lock
2. Load ledger
3. Locate the boundary entry by ``hash``; hard-fail on mismatch
4. Verify no later ``resume`` entry already consumed this hash
5. (Optional) re-prompt the user when the boundary carries a
   ``pending_decision``; record the ``chosen_branch``
6. Append a new ``kind="resume"`` entry
7. Release lock
8. Return the resolved routing (next_stage / next_mode after option lookup)

ARS iron rule §8: when ``pending_decision`` is set, the orchestrator
MUST re-prompt; CLI ``stage=``/``mode=`` overrides apply ONLY after the
user picks a branch. The boundary's top-level ``next`` field is
advisory and is superseded by the matched option's ``next_stage`` /
``next_mode``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paic.passport.ledger import (
    LOCK_FILENAME,
    append_resume,
    load_ledger,
)
from paic.passport.lock import passport_lock
from paic.passport.schema import PassportEntry


class PassportResumeError(RuntimeError):
    """Base class for resume failures."""


class HashMismatchError(PassportResumeError):
    """Requested hash not found in the ledger."""


class DoubleResumeError(PassportResumeError):
    """A prior ``resume`` entry already consumes this boundary."""


@dataclass(frozen=True)
class ResumePlan:
    """The orchestrator's routing decision after one resolve_resume call."""

    boundary: PassportEntry
    resume_entry: PassportEntry
    next_stage: int | str | None
    next_mode: str | None
    used_pending_decision: bool


def resolve_resume(
    ledger_path: Path,
    *,
    hash: str,
    stage_override: int | str | None = None,
    mode_override: str | None = None,
    chosen_branch: str | None = None,
    session_marker: str | None = None,
    timeout_sec: float = 30.0,
) -> ResumePlan:
    """Resolve ``resume_from_passport=<hash>`` end-to-end under lock.

    Args:
        ledger_path: passport ledger file (`<project>/.paic/state/passport.yaml`)
        hash: 12-char hex digest emitted by a prior boundary
        stage_override: CLI override (``stage=<n>``); applies AFTER the
            pending-decision branch has been resolved
        mode_override: CLI override (``mode=<m>``); same precedence
        chosen_branch: when the boundary carries a ``pending_decision``,
            the user must supply the ``value`` of the chosen option
        session_marker: opaque tag to record on the resume entry

    Routing precedence (CLI overrides win **after** branch routing):

    1. If ``pending_decision`` set:
       a. Find option whose ``value == chosen_branch``
       b. Use that option's ``next_stage`` / ``next_mode`` as base
    2. Else:
       a. Use the boundary entry's top-level ``next_stage`` as base
    3. CLI overrides (``stage_override`` / ``mode_override``) replace
       the base values (when supplied)

    Raises:
        HashMismatchError: no boundary entry with matching hash
        DoubleResumeError: an existing resume entry already consumed it
        PassportResumeError: ``pending_decision`` set but
            ``chosen_branch`` missing / not in options
    """
    lock_path = ledger_path.parent / LOCK_FILENAME

    with passport_lock(lock_path, timeout_sec=timeout_sec):
        entries = load_ledger(ledger_path)
        boundary = _find_boundary_or_raise(entries, hash)
        _ensure_not_already_consumed(entries, hash)

        used_pending = False
        next_stage: int | str | None
        next_mode: str | None

        if boundary.pending_decision is not None:
            if not chosen_branch:
                opts = [o.value for o in boundary.pending_decision.options]
                raise PassportResumeError(
                    f"Boundary {hash} carries pending_decision; supply "
                    f"chosen_branch=<one of {opts}> to resume."
                )
            option = _find_option_or_raise(boundary, chosen_branch)
            next_stage = option.next_stage
            next_mode = option.next_mode
            used_pending = True
        else:
            next_stage = boundary.next_stage
            next_mode = None  # boundary entries don't carry a top-level mode

        # CLI overrides apply last
        if stage_override is not None:
            next_stage = stage_override
        if mode_override is not None:
            next_mode = mode_override

        user_override: dict[str, Any] = {}
        if stage_override is not None:
            user_override["stage"] = stage_override
        if mode_override is not None:
            user_override["mode"] = mode_override

        resume_entry = append_resume(
            ledger_path,
            consumes_hash=hash,
            stage=next_stage if next_stage is not None else boundary.stage,
            chosen_branch=chosen_branch,
            user_override=user_override or None,
            session_marker=session_marker,
        )

    return ResumePlan(
        boundary=boundary,
        resume_entry=resume_entry,
        next_stage=next_stage,
        next_mode=next_mode,
        used_pending_decision=used_pending,
    )


def _find_boundary_or_raise(
    entries: list[PassportEntry],
    target_hash: str,
) -> PassportEntry:
    for entry in entries:
        if entry.kind == "boundary" and entry.hash == target_hash:
            return entry
    raise HashMismatchError(
        f"No boundary entry with hash={target_hash} in ledger. "
        f"Known boundaries: {[e.hash for e in entries if e.kind == 'boundary']}"
    )


def _ensure_not_already_consumed(
    entries: list[PassportEntry],
    target_hash: str,
) -> None:
    for entry in entries:
        if entry.kind == "resume" and entry.consumes_hash == target_hash:
            raise DoubleResumeError(
                f"Boundary {target_hash} already consumed by resume entry "
                f"{entry.id} at {entry.generated_at.isoformat()}."
            )


def _find_option_or_raise(boundary: PassportEntry, value: str):
    assert boundary.pending_decision is not None
    for option in boundary.pending_decision.options:
        if option.value == value:
            return option
    valid = [o.value for o in boundary.pending_decision.options]
    raise PassportResumeError(
        f"chosen_branch={value!r} not in pending_decision.options; "
        f"valid={valid}"
    )

"""``paic_pipeline_*`` MCP tools — 11-stage state machine for /paic-pipeline.

The state machine itself lives in the orchestrator agent prompt
(``agents/pipeline_orchestrator.md``); these MCP tools are the IO layer:
read the current state, append a transition. No LLM call; no host
orchestration. Mandatory-checkpoint enforcement is also done here so
the agent prompt cannot accidentally bypass it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from paic.schemas.pipeline_state import (
    MANDATORY_STAGES,
    STAGE_LABELS,
    PipelineState,
    StageHistoryEntry,
    is_mandatory,
    stage_label,
)
from paic.workspace.paths import (
    ProjectPaths,
    ensure_project_layout,
    resolve_project,
)
from paic.workspace.store import load_yaml, save_yaml

# When the user says "just continue" this many times in a row at non-MANDATORY
# checkpoints, the next checkpoint is forced back to FULL regardless. ARS
# adaptive-checkpoint rule (P2-1 will wire this into the orchestrator).
CONSECUTIVE_CONTINUE_FORCE_FULL = 4


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


def _load_state(paths: ProjectPaths) -> PipelineState | None:
    p = paths.pipeline_yaml
    if not p.is_file():
        return None
    raw = load_yaml(p)
    if not isinstance(raw, dict):
        return None
    return PipelineState.model_validate(raw)


def _save_state(paths: ProjectPaths, state: PipelineState) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    save_yaml(paths.pipeline_yaml, state.model_dump(mode="json", exclude_none=True))


# ----------------------------------------------------- paic_pipeline_state


def pipeline_state_tool(project_dir: str) -> dict[str, Any]:
    """Read-only snapshot of the pipeline state file.

    Returns ``{exists, project_dir, state_path, state?, mandatory_stages,
    stage_labels}``. ``state`` is the deserialized PipelineState; absent
    when no pipeline run has been initialized for this project.
    """
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    state = _load_state(paths)
    out: dict[str, Any] = {
        "exists": state is not None,
        "project_dir": str(paths.root),
        "state_path": str(paths.pipeline_yaml),
        "mandatory_stages": sorted(MANDATORY_STAGES),
        "stage_labels": dict(STAGE_LABELS),
    }
    if state is not None:
        out["state"] = state.model_dump(mode="json", exclude_none=True)
        out["current_stage_label"] = stage_label(state.current_stage)
    return out


# ----------------------------------------------------- paic_pipeline_advance


def pipeline_advance_tool(
    project_dir: str,
    *,
    to_stage: int,
    checkpoint_kind: str = "FULL",
    verdict: str | None = None,
    deliverables: list[str] | None = None,
    notes: str | None = None,
    passport_hash: str | None = None,
    mode: str | None = None,
    consecutive_continue: bool = False,
) -> dict[str, Any]:
    """Advance the pipeline to ``to_stage`` and record the transition.

    Behaviour:
    - On first call (no existing state), initializes ``current_stage=to_stage``.
    - On subsequent calls, validates the transition and appends a
      :class:`StageHistoryEntry`.
    - Auto-promotes ``checkpoint_kind`` to ``"MANDATORY"`` when
      ``to_stage`` is in :data:`MANDATORY_STAGES` (the agent prompt
      cannot bypass this even by passing ``"FULL"``).
    - Resets ``consecutive_continues`` on every MANDATORY or FULL
      checkpoint; increments on SLIM.
    - When ``consecutive_continues >= 4``, forces the next checkpoint
      back to FULL regardless of caller intent (ARS awareness guard).

    Args:
        to_stage: 0-10. Validated against :data:`STAGE_LABELS`.
        checkpoint_kind: caller's intent (``"FULL"`` / ``"SLIM"`` /
            ``"MANDATORY"``). May be promoted to MANDATORY automatically.
        verdict: optional user verdict (e.g. review accept/revise/reject;
            integrity pass/fail/with-overrides).
        deliverables: artefact identifiers produced by the just-completed
            stage (paths, IDs).
        passport_hash: when the orchestrator emitted a Material Passport
            boundary at this transition, record the 12-char hash so
            ``paic_pipeline_state`` can show "resume_command available".
        mode: pipeline mode override (``"greenfield"`` / ``"mid_entry"`` /
            ``"revision_only"`` / ``"finalize_only"``). Ignored on
            subsequent calls; mode is locked at first call.
        consecutive_continue: True when the user explicitly said "just
            continue" at the prior checkpoint — drives the SLIM downgrade.
    """
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    if to_stage not in STAGE_LABELS:
        return {
            "error": "invalid_stage",
            "to_stage": to_stage,
            "valid_stages": sorted(STAGE_LABELS),
        }

    if checkpoint_kind not in ("FULL", "SLIM", "MANDATORY"):
        return {
            "error": "invalid_checkpoint_kind",
            "got": checkpoint_kind,
            "valid": ["FULL", "SLIM", "MANDATORY"],
        }

    state = _load_state(paths)
    now = datetime.now(UTC)
    promoted = False
    forced_full = False

    if state is None:
        # Bootstrap: first call sets current_stage = to_stage; mode locked.
        effective_kind = "MANDATORY" if is_mandatory(to_stage) else checkpoint_kind
        promoted = effective_kind != checkpoint_kind
        new_state = PipelineState(
            project_dir=str(paths.root),
            current_stage=to_stage,
            mode=mode or "greenfield",
            consecutive_continues=0,
            awaiting_user=True,
            last_checkpoint_kind=effective_kind,
            last_advanced_at=now,
            stage_history=[StageHistoryEntry(
                from_stage=None,
                to_stage=to_stage,
                transitioned_at=now,
                checkpoint_kind=effective_kind,
                verdict=verdict,
                deliverables=list(deliverables or []),
                notes=notes,
                passport_hash=passport_hash,
            )],
        )
    else:
        # Compute effective checkpoint kind with all promotion rules.
        effective_kind = checkpoint_kind
        if is_mandatory(to_stage):
            effective_kind = "MANDATORY"
            if checkpoint_kind != "MANDATORY":
                promoted = True
        elif (
            consecutive_continue
            and state.consecutive_continues + 1 >= CONSECUTIVE_CONTINUE_FORCE_FULL
        ):
            effective_kind = "FULL"
            forced_full = True

        # Update consecutive_continues per the SLIM downgrade rule.
        if effective_kind == "MANDATORY" or effective_kind == "FULL":
            new_consecutive = 0
        elif effective_kind == "SLIM":
            new_consecutive = state.consecutive_continues + 1
        else:
            new_consecutive = state.consecutive_continues

        new_state = state.model_copy(update={
            "current_stage": to_stage,
            "consecutive_continues": new_consecutive,
            "awaiting_user": True,
            "last_checkpoint_kind": effective_kind,
            "last_advanced_at": now,
            "stage_history": list(state.stage_history) + [StageHistoryEntry(
                from_stage=state.current_stage,
                to_stage=to_stage,
                transitioned_at=now,
                checkpoint_kind=effective_kind,
                verdict=verdict,
                deliverables=list(deliverables or []),
                notes=notes,
                passport_hash=passport_hash,
            )],
        })

    _save_state(paths, new_state)

    out: dict[str, Any] = {
        "ok": True,
        "current_stage": new_state.current_stage,
        "current_stage_label": stage_label(new_state.current_stage),
        "checkpoint_kind": new_state.last_checkpoint_kind,
        "is_mandatory": is_mandatory(new_state.current_stage),
        "consecutive_continues": new_state.consecutive_continues,
        "state_path": str(paths.pipeline_yaml),
    }
    if promoted:
        out["promoted_to_mandatory"] = True
        out["note"] = (
            f"Stage {new_state.current_stage} ({stage_label(new_state.current_stage)}) "
            "is in MANDATORY_STAGES — checkpoint forced to MANDATORY regardless of "
            "caller intent. Cannot be bypassed."
        )
    if forced_full:
        out["forced_full_for_awareness"] = True
        out["note"] = (
            f"4+ consecutive 'continue' responses — checkpoint forced back to FULL "
            "for awareness (ARS adaptive-checkpoint rule). Counter reset."
        )
    return out

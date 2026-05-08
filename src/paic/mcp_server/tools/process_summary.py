"""``paic_process_summary_generate`` — Stage-6 process record renderer (P3-2).

Borrowed from ARS Stage 6 PROCESS SUMMARY. Reads three state files:

- ``pipeline.yaml`` — 11-stage orchestrator history (when /paic-pipeline used)
- ``passport.yaml`` — Material Passport boundaries / resumes (cross-session)
- ``runs.yaml`` — LangGraph runs (ideate / experiment / review)
- ``state/integrity_report.yaml`` — latest integrity gate output

Renders a markdown report covering:

1. Paper creation timeline (which stage, when, by whom session)
2. AI usage timeline (which LLM / backend / node was called when)
3. Collaboration depth scores (when collaboration_depth observer ran)
4. AI failure mode audit log (which modes were SUSPECTED across runs)
5. Decision points (every MANDATORY checkpoint verdict)

Optional ``write_to`` parameter writes the rendered text to disk.
LaTeX→PDF rendering (ARS-style "process record PDF") is left as a
follow-up — V1.1 ships markdown only.

Pure render — no LLM call. Useful as the closing artefact of a
/paic-pipeline run, or as input to the user's reflection on which
stages they delegated heavily and which they kept hands-on.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from paic.passport.ledger import load_ledger
from paic.schemas.pipeline_state import (
    STAGE_LABELS,
    PipelineState,
    stage_label,
)
from paic.workspace.paths import (
    ProjectPaths,
    ensure_project_layout,
    resolve_project,
)
from paic.workspace.store import load_yaml, write_text


def process_summary_tool(
    project_dir: str,
    *,
    write_to: str | None = None,
    include_integrity: bool = True,
) -> dict[str, Any]:
    """Render the paper creation process record as markdown.

    Returns ``{ok, content, written_to?, sections_present, sources}``.

    ``sources`` lists which of the four state files were available;
    missing sources produce a placeholder line in the report rather than
    a hard error (e.g. project that never used /paic-pipeline still
    yields a useful runs+passport+integrity summary).

    ``write_to`` (optional, project-relative or absolute): writes the
    rendered markdown to that path in addition to returning it. Suggested:
    ``drafts/process_summary.md``.

    ``include_integrity=True`` (default): include the latest integrity
    report when present. Set False when the user wants a tighter
    high-level overview without per-citation findings.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    ensure_project_layout(paths)

    pipeline_state = _load_pipeline_state(paths)
    passport_entries = _safe_load_passport(paths)
    runs = _load_runs(paths)
    integrity = _load_integrity(paths) if include_integrity else None

    sources_present: list[str] = []
    if pipeline_state is not None:
        sources_present.append("pipeline.yaml")
    if passport_entries:
        sources_present.append("passport.yaml")
    if runs:
        sources_present.append("runs.yaml")
    if integrity is not None:
        sources_present.append("state/integrity_report.yaml")

    sections_md: list[str] = []
    sections_md.append(_render_header(paths))
    sections_md.append(_render_pipeline_timeline(pipeline_state))
    sections_md.append(_render_passport_section(passport_entries))
    sections_md.append(_render_runs_section(runs))
    if integrity is not None:
        sections_md.append(_render_integrity_section(integrity))
    sections_md.append(_render_footer(paths, sources_present))

    content = "\n\n".join(s for s in sections_md if s).rstrip() + "\n"

    out: dict[str, Any] = {
        "ok": True,
        "content": content,
        "sections_present": sources_present,
        "project_dir": str(paths.root),
    }

    if write_to:
        target = _resolve_path(paths.root, write_to)
        write_text(target, content)
        out["written_to"] = str(target)

    return out


# ---------------------------------------------------- loaders


def _load_pipeline_state(paths: ProjectPaths) -> PipelineState | None:
    if not paths.pipeline_yaml.is_file():
        return None
    raw = load_yaml(paths.pipeline_yaml)
    if not isinstance(raw, dict):
        return None
    try:
        return PipelineState.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def _safe_load_passport(paths: ProjectPaths) -> list:
    if not paths.passport_yaml.is_file():
        return []
    try:
        return load_ledger(paths.passport_yaml)
    except Exception:  # noqa: BLE001 — corrupt passport shouldn't break the report
        return []


def _load_runs(paths: ProjectPaths) -> list[dict]:
    if not paths.runs_yaml.is_file():
        return []
    raw = load_yaml(paths.runs_yaml)
    if isinstance(raw, dict):
        runs = raw.get("runs") or []
    elif isinstance(raw, list):
        runs = raw
    else:
        return []
    return [r for r in runs if isinstance(r, dict)]


def _load_integrity(paths: ProjectPaths) -> dict | None:
    p = paths.state_dir / "integrity_report.yaml"
    if not p.is_file():
        return None
    raw = load_yaml(p)
    return raw if isinstance(raw, dict) else None


# ---------------------------------------------------- renderers


def _render_header(paths: ProjectPaths) -> str:
    project = paths.root.name
    project_yaml = paths.project_yaml
    title: str | None = None
    venue: str | None = None
    if project_yaml.is_file():
        meta = load_yaml(project_yaml) or {}
        if isinstance(meta, dict):
            title = meta.get("title")
            venue = meta.get("venue")
    now = datetime.now().isoformat(timespec="seconds")
    lines = [
        "# Paper Creation Process Record",
        "",
        f"**Project**: `{project}`",
    ]
    if title:
        lines.append(f"**Title**: {title}")
    if venue:
        lines.append(f"**Venue**: {venue}")
    lines.append(f"**Generated**: {now}")
    lines.append("")
    lines.append(
        "Auto-rendered by `/paic-process-summary` (ARS-fusion P3-2). "
        "Reads `pipeline.yaml` + `passport.yaml` + `runs.yaml` + "
        "`integrity_report.yaml`; missing sources produce placeholder "
        "lines rather than errors."
    )
    return "\n".join(lines)


def _render_pipeline_timeline(state: PipelineState | None) -> str:
    if state is None:
        return (
            "## 1. Pipeline timeline\n\n"
            "_No `pipeline.yaml` — `/paic-pipeline` was not used. "
            "Single-SKILL invocations don't write a stage-level timeline._"
        )
    lines = [
        "## 1. Pipeline timeline",
        "",
        f"**Current stage**: {state.current_stage} ({stage_label(state.current_stage)})",
        f"**Mode**: {state.mode}",
        f"**Awaiting user**: {state.awaiting_user}",
        f"**Consecutive continues**: {state.consecutive_continues}",
        "",
        "| → Stage | Checkpoint | Verdict | Deliverables | Passport hash |",
        "|---|---|---|---|---|",
    ]
    for entry in state.stage_history:
        from_label = (
            stage_label(entry.from_stage) if entry.from_stage is not None else "(start)"
        )
        to_label = stage_label(entry.to_stage)
        verdict = entry.verdict or "—"
        deliv_count = len(entry.deliverables)
        passport_marker = entry.passport_hash[:12] if entry.passport_hash else "—"
        lines.append(
            f"| {from_label} → {to_label} | {entry.checkpoint_kind} | "
            f"{verdict} | {deliv_count} | `{passport_marker}` |"
        )
    return "\n".join(lines)


def _render_passport_section(entries: list) -> str:
    if not entries:
        return (
            "## 2. Material Passport\n\n"
            "_No `passport.yaml` — passport.enable_reset_boundary not "
            "enabled, or no FULL checkpoint emitted boundary entries yet._"
        )
    lines = [
        "## 2. Material Passport (cross-session boundaries)",
        "",
        "| Hash | Kind | Stage | Generated | Consumes |",
        "|---|---|---|---|---|",
    ]
    for e in entries:
        consumes = e.consumes_hash or "—"
        gen = e.generated_at.isoformat(timespec="seconds")
        lines.append(
            f"| `{e.hash}` | {e.kind} | {stage_label(e.stage) if isinstance(e.stage, int) else e.stage} | "
            f"{gen} | `{consumes}` |"
        )
    consumed = {ent.consumes_hash for ent in entries if ent.kind == "resume" and ent.consumes_hash}
    pending = [e for e in entries if e.kind == "boundary" and e.hash not in consumed]
    if pending:
        lines.append("")
        lines.append(
            f"**Awaiting resume**: {len(pending)} boundary entries — "
            f"hash list: " + ", ".join(f"`{e.hash}`" for e in pending)
        )
    return "\n".join(lines)


def _render_runs_section(runs: list[dict]) -> str:
    if not runs:
        return (
            "## 3. LangGraph runs\n\n"
            "_No `runs.yaml` entries — `/paic-ideate` / `/paic-experiment` "
            "/ `/paic-review` haven't been run, or all completed runs were "
            "garbage-collected._"
        )
    lines = [
        "## 3. LangGraph runs (intra-session pause/resume)",
        "",
        "| Run id | Kind | Status | Updated |",
        "|---|---|---|---|",
    ]
    for r in runs[-20:]:  # cap at last 20 to keep the report readable
        run_id = (r.get("run_id") or "?")[:12]
        kind = r.get("kind") or "?"
        status = r.get("status") or "?"
        updated = r.get("updated_at") or "—"
        lines.append(f"| `{run_id}` | {kind} | {status} | {updated} |")
    if len(runs) > 20:
        lines.append("")
        lines.append(f"_(+ {len(runs) - 20} earlier runs not shown)_")
    return "\n".join(lines)


def _render_integrity_section(integrity: dict) -> str:
    lines = [
        "## 4. Latest integrity report",
        "",
        f"**Mode**: {integrity.get('mode', 'unknown')}",
        f"**Passed**: {integrity.get('passed')}",
        f"**Issue count**: {integrity.get('issue_count', 0)}",
    ]
    overrides = integrity.get("overrides") or []
    overrides_rejected = integrity.get("overrides_rejected") or []
    if overrides:
        lines.append(f"**Overrides applied**: {overrides}")
    if overrides_rejected:
        lines.append(
            f"**Overrides rejected** (blocker-protected): {len(overrides_rejected)} entry/entries"
        )
    issues = integrity.get("issues") or []
    if issues:
        by_severity: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for issue in issues:
            sev = issue.get("severity", "info")
            kind = issue.get("kind", "unknown")
            by_severity[sev] = by_severity.get(sev, 0) + 1
            by_kind[kind] = by_kind.get(kind, 0) + 1
        lines.append("")
        lines.append("**Issue breakdown**:")
        for sev in ("blocker", "major", "minor", "info"):
            if sev in by_severity:
                lines.append(f"- {sev}: {by_severity[sev]}")
        lines.append("")
        lines.append("**By kind**:")
        for kind, count in sorted(by_kind.items(), key=lambda kv: -kv[1])[:10]:
            lines.append(f"- `{kind}`: {count}")
    notes = integrity.get("notes") or []
    if notes:
        lines.append("")
        lines.append("**Notes**:")
        for n in notes:
            lines.append(f"- {n}")
    return "\n".join(lines)


def _render_footer(paths: ProjectPaths, sources: list[str]) -> str:
    return (
        "---\n\n"
        f"_State files read: {', '.join(sources) if sources else '(none)'}._\n\n"
        "_Process record is auto-generated and best-effort. Caveats: "
        "(1) intra-stage user-AI dialogue is not captured here — see "
        "Claude Code session transcripts; (2) collaboration depth scores "
        "are advisory only (P2-2); (3) plagiarism / originality findings "
        "(P3-1) are heuristic — not a substitute for Turnitin / iThenticate._"
    )


def _resolve_path(project_root: Path, candidate: str) -> Path:
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return p
    return (project_root / p).resolve()

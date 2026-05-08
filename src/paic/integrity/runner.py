"""Integrity runner — orchestrates citation_check + ai_failure_modes.

Two entry points:

- :func:`run_integrity_check` — first call. Reads project state, runs
  S2 batch verify, builds AI failure prompts. Returns
  :class:`IntegrityResult` with ``pending_websearch`` / ``pending_ai_judge``
  populated when host orchestration is required (caller must round-trip).
- :func:`apply_persisted_results` — second call (host orchestration only).
  Receives WebSearch verdicts + AI judge results, finalizes the report,
  applies overrides, computes ``passed``.

Cache lives at ``<project>/.paic/state/integrity_cache.json``; report
output at ``<project>/.paic/state/integrity_report.yaml``. Both are
opt-in artefacts — the runner functions return values regardless.
"""

from __future__ import annotations

import json
from typing import Iterable

from paic.integrity.ai_failure_modes import apply_judgments, build_ai_failure_prompts
from paic.integrity.citation_check import (
    classify_websearch_results,
    verify_library_via_s2,
)
from paic.integrity.originality import run_originality_check
from paic.integrity.types import (
    DEFAULT_MANDATORY_MODES,
    HALLUCINATION_KINDS,
    AIFailurePrompt,
    IntegrityIssue,
    IntegrityResult,
    WebSearchPending,
)
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import save_yaml

_CACHE_FILENAME = "integrity_cache.json"
_REPORT_FILENAME = "integrity_report.yaml"


# ----------------------------------------------------- public API


def run_integrity_check(
    paths: ProjectPaths,
    *,
    mode: str = "pre_review",
    from_scratch: bool = False,
    mandatory_modes: Iterable[int] = DEFAULT_MANDATORY_MODES,
    s2_enabled: bool = True,
    originality_enabled: bool = False,
    originality_sample_rate: float | None = None,
    s2_search_fn=None,
    inline_judge_fn=None,
) -> IntegrityResult:
    """First-pass integrity check.

    ``from_scratch=True`` discards the cache (used by Stage 4.5 final
    integrity to verify independently of Stage 2.5 results — borrowed
    from ARS iron rule "Stage 4.5 verifies from scratch").

    ``inline_judge_fn`` (optional) is called for each non-skipped
    :class:`AIFailurePrompt`. Signature: ``judge_fn(prompt) -> dict``
    (the dict shape :class:`_AIFailureJudgment` validates). When ``None``
    the AI prompts are returned in ``pending_ai_judge`` so the caller
    can dispatch them via host orchestration.

    ``mode="originality"`` (P3-1) runs ONLY the originality scan
    (skips citation S2 + AI failure modes). Useful for a quick
    plagiarism-only check without paying the full integrity cost.
    For ``mode in {"pre_review", "final_check"}``, originality is
    opt-in via ``originality_enabled=True`` so existing pipelines
    don't suddenly start surfacing paragraph-similarity findings.
    """
    if mode == "originality":
        return _run_originality_only(paths, sample_rate=originality_sample_rate)

    mandatory_list = list(mandatory_modes or DEFAULT_MANDATORY_MODES)
    cache = {} if from_scratch else _load_cache(paths)

    verified, pending_web, structural, updated_cache = verify_library_via_s2(
        paths,
        cache=cache,
        s2_search_fn=s2_search_fn,
        enabled=s2_enabled,
    )

    cache_hits = len(cache) - len(updated_cache.keys() - cache.keys())
    if cache_hits < 0:
        cache_hits = 0

    notes: list[str] = []
    issues: list[IntegrityIssue] = list(structural)

    prompts = build_ai_failure_prompts(paths)
    pending_ai: list[AIFailurePrompt] = []
    for prompt in prompts:
        if prompt.skipped_reason:
            notes.append(
                f"AI_FAIL_M{prompt.mode} skipped — {prompt.skipped_reason}"
            )
            continue
        if inline_judge_fn is None:
            pending_ai.append(prompt)
            continue
        try:
            judgment = inline_judge_fn(prompt)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"AI_FAIL_M{prompt.mode} judge failed: {exc!r}")
            continue
        if isinstance(judgment, list):
            issues.extend(apply_judgments(judgment, mandatory_modes=mandatory_list))
        else:
            issues.extend(apply_judgments([judgment], mandatory_modes=mandatory_list))

    if originality_enabled:
        try:
            originality_issues = run_originality_check(
                paths,
                mode=mode,
                sample_rate=originality_sample_rate,
            )
            issues.extend(originality_issues)
        except Exception as exc:  # noqa: BLE001 — never let originality break the gate
            notes.append(f"originality scan failed: {exc!r}")

    _save_cache(paths, updated_cache)

    result = IntegrityResult(
        passed=_compute_passed(issues, []),
        mode=mode,
        issues=issues,
        mandatory_modes=mandatory_list,
        pending_websearch=pending_web,
        pending_ai_judge=pending_ai,
        s2_verified_count=len(verified),
        s2_failed_count=len(pending_web),
        cache_hits=cache_hits,
        notes=notes,
    )
    return result


def _run_originality_only(
    paths: ProjectPaths,
    *,
    sample_rate: float | None,
) -> IntegrityResult:
    """Standalone originality scan — bypasses citation + AI judge.

    ``mode="originality"`` is the only path that hits 100% paragraph
    coverage by default; pre_review / final_check use the configured
    sample rate via :func:`run_originality_check`.
    """
    notes: list[str] = []
    issues: list[IntegrityIssue] = []
    try:
        issues = run_originality_check(
            paths,
            mode="originality",
            sample_rate=sample_rate,
        )
    except Exception as exc:  # noqa: BLE001
        notes.append(f"originality scan failed: {exc!r}")

    return IntegrityResult(
        passed=_compute_passed(issues, []),
        mode="originality",
        issues=issues,
        mandatory_modes=[],
        pending_websearch=[],
        pending_ai_judge=[],
        s2_verified_count=0,
        s2_failed_count=0,
        cache_hits=0,
        notes=notes,
    )


def apply_persisted_results(
    paths: ProjectPaths,
    *,
    mode: str = "pre_review",
    web_search_results: list[dict] | None = None,
    ai_judge_results: list[dict] | None = None,
    structural_issues: list[IntegrityIssue] | None = None,
    overrides: list[str] | None = None,
    mandatory_modes: Iterable[int] = DEFAULT_MANDATORY_MODES,
    write_report: bool = True,
) -> IntegrityResult:
    """Second-pass integrity check — applies host-supplied verdicts.

    Combines:
    - structural issues already detected (passed back from the first call)
    - WebSearch verdicts → 5-type hallucination classification
    - LLM judge verdicts → 7-mode AI failure issues

    Applies ``overrides`` (list of issue kinds) with the same semantic as
    :func:`paic.latex.quality_gate.run_quality_gate` — blocker severity
    can never be overridden.
    """
    mandatory_list = list(mandatory_modes or DEFAULT_MANDATORY_MODES)
    issues: list[IntegrityIssue] = list(structural_issues or [])

    if web_search_results:
        issues.extend(
            classify_websearch_results(web_search_results, paths=paths)
        )

    if ai_judge_results:
        issues.extend(
            apply_judgments(ai_judge_results, mandatory_modes=mandatory_list)
        )

    issues = _promote_mandatory_blockers(issues, mandatory_list)
    filtered, rejected = _apply_overrides(issues, overrides or [])
    passed = _compute_passed(filtered, [])

    result = IntegrityResult(
        passed=passed,
        mode=mode,
        issues=filtered,
        overrides=list(overrides or []),
        overrides_rejected=rejected,
        mandatory_modes=mandatory_list,
    )

    if write_report:
        save_yaml(paths.state_dir / _REPORT_FILENAME, result.to_dict())

    return result


# ----------------------------------------------------- helpers


def _load_cache(paths: ProjectPaths) -> dict[str, dict]:
    p = paths.state_dir / _CACHE_FILENAME
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(paths: ProjectPaths, cache: dict[str, dict]) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    p = paths.state_dir / _CACHE_FILENAME
    try:
        p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Best effort — cache is an optimisation, never a correctness gate.
        pass


def _promote_mandatory_blockers(
    issues: list[IntegrityIssue],
    mandatory_modes: list[int],
) -> list[IntegrityIssue]:
    """Promote SUSPECTED issues from mandatory modes to severity=blocker.

    Mandatory rule per V1.0 design: AI failure modes 1/3/5/6 → blocker on
    SUSPECTED. Citation hallucination kinds (TF/PAC/IH/PH/SH) keep their
    citation_check severity (TF already blocker; rest stay at major).
    """
    mandatory_set = set(mandatory_modes or [])
    promoted: list[IntegrityIssue] = []
    for issue in issues:
        kind = issue.kind
        if kind.startswith("AI_FAIL_M"):
            try:
                m = int(kind.removeprefix("AI_FAIL_M"))
            except ValueError:
                promoted.append(issue)
                continue
            if m in mandatory_set and issue.severity not in {"blocker", "major"}:
                issue = IntegrityIssue(
                    kind=issue.kind,
                    severity="blocker",
                    target=issue.target,
                    detail=issue.detail,
                    actionable_fix=issue.actionable_fix,
                    evidence_url=issue.evidence_url,
                    suggested_correction=issue.suggested_correction,
                )
        promoted.append(issue)
    return promoted


def _apply_overrides(
    issues: list[IntegrityIssue],
    overrides: list[str],
) -> tuple[list[IntegrityIssue], list[dict]]:
    """Mirror :func:`run_quality_gate`'s override semantics (blocker-protected)."""
    overrides_set = set(overrides)
    kept: list[IntegrityIssue] = []
    rejected: list[dict] = []
    for issue in issues:
        if issue.kind in overrides_set:
            if issue.severity == "blocker":
                kept.append(issue)
                rejected.append({"kind": issue.kind, "severity": issue.severity})
            # else silently dropped per user request
            continue
        kept.append(issue)
    return kept, rejected


def _compute_passed(
    issues: list[IntegrityIssue],
    rejected_overrides: list[dict],
) -> bool:
    return not any(i.severity in {"major", "blocker"} for i in issues)


# Re-exports useful for tests / SKILL renderers
__all__ = [
    "HALLUCINATION_KINDS",
    "WebSearchPending",
    "apply_persisted_results",
    "run_integrity_check",
]

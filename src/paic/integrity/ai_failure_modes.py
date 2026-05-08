"""AI Research Failure Mode Checklist (Lu 2026, 7 modes).

Each mode is an LLM judge that reads a slice of paper artefacts and
emits a verdict: VERIFIED / SUSPECTED / INSUFFICIENT_EVIDENCE.

Per V1.0 design (CLAUDE.md ARS fusion plan §决策 4):

- **Mandatory** (severity → blocker on SUSPECTED): modes 1 / 3 / 5 / 6
- **Advisory** (severity → minor on SUSPECTED): modes 2 / 4 / 7

Mode 1 (citation hallucination) is reserved here for completeness but
delegated to :mod:`paic.integrity.citation_check` (S2 batch +
WebSearch). The runner suppresses M1 from the LLM-judge bundle when
``citation_check`` already produced findings.

Inputs per mode (loaded from ``.paic/`` once and reused across modes):

- M2 (implementation bugs): ``paper_plan.symbols`` + ``experiments[*]``
  ``proposed_method`` / pseudocode + ``claims`` of type=methodological.
- M3 (hallucinated results): ``claims[type=numeric|result]`` +
  ``experiments[*].results`` (from ``paic_experiment_record_result``).
- M4 (shortcut reliance): ``experiments[*].datasets`` + ``threats_to_validity``
  + ``proposed_method`` (looking for "majority class baseline" etc.).
- M5 (methodology fabrication): ``experiments[*].proposed_method`` +
  ``claims[type=methodological]`` + ``paper_plan.section_plan[03_method].intent``.
- M6 (frame-lock): ``paper_plan.thesis`` + ``paper_plan.contributions[]`` +
  ``claims[]`` — looking for the paper plan and the claim ledger telling
  inconsistent stories.
- M7 (bug-as-insight): ``experiments[*].results`` + ``claims[type=result]``
  — looking for surprising results presented without sanity-check
  discussion in ``threats_to_validity``.

Skip rules: when ``paper_plan.paper_kind`` is set and a mode's input is
empty (e.g. ``paper_kind=theoretical`` → no numeric results), the runner
records a SKIPPED prompt instead of running the judge. The integrity
report includes these as a ``notes`` line so the user knows what was
intentionally not checked.
"""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from paic.integrity.types import (
    AI_FAILURE_MODE_LABELS,
    DEFAULT_MANDATORY_MODES,
    AIFailurePrompt,
    IntegrityIssue,
)
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml

# Cap large inputs so the LLM judge prompt stays readable / cacheable.
_CLAIMS_CAP = 60
_EXPERIMENTS_CAP = 4


# ----------------------------------------------------- LLM judge schema


class _AIFailureJudgment(BaseModel):
    """Structured response the host LLM (or local judge) returns per mode.

    ``status`` is the trichotomy from the ARS spec — INSUFFICIENT_EVIDENCE
    must be used when the inputs are too thin to decide, never SUSPECTED
    by default (avoids false-positive blocks on theory / position papers).
    """

    model_config = ConfigDict(extra="ignore")

    mode: int
    status: str  # VERIFIED / SUSPECTED / INSUFFICIENT_EVIDENCE
    reasoning: str = ""
    evidence: list[str] = Field(default_factory=list)
    suggested_followup: str | None = None


# ----------------------------------------------------- Public API


def build_ai_failure_prompts(
    paths: ProjectPaths,
    *,
    skip_mode_1: bool = True,
) -> list[AIFailurePrompt]:
    """Construct one prompt bundle per mode 1..7 (default skips M1).

    M1 is delegated to :mod:`paic.integrity.citation_check` so we don't
    double-count citation hallucination. Pass ``skip_mode_1=False`` if
    you want the LLM judge's view as a sanity check (advisory only).

    Modes whose inputs would be empty record a ``skipped_reason`` and
    are returned with empty ``inputs``; the runner uses this to emit a
    note instead of dispatching the judge.
    """
    paper_plan = _safe_load(paths.paper_plan_yaml)
    claims = _safe_load(paths.claims_yaml)
    experiments = _load_experiments(paths)
    paper_kind = (paper_plan.get("paper_kind") if paper_plan else None) or "empirical"

    modes_to_run = [1, 2, 3, 4, 5, 6, 7]
    if skip_mode_1:
        modes_to_run = modes_to_run[1:]

    builders = {
        1: _build_m1,
        2: _build_m2,
        3: _build_m3,
        4: _build_m4,
        5: _build_m5,
        6: _build_m6,
        7: _build_m7,
    }
    out: list[AIFailurePrompt] = []
    for mode in modes_to_run:
        prompt = builders[mode](paper_plan, claims, experiments, paper_kind)
        out.append(prompt)
    return out


def apply_judgments(
    judgments: Iterable[dict | _AIFailureJudgment],
    *,
    mandatory_modes: Iterable[int] = DEFAULT_MANDATORY_MODES,
) -> list[IntegrityIssue]:
    """Convert LLM judge verdicts into :class:`IntegrityIssue` entries.

    Promotion rule: ``status=SUSPECTED`` AND ``mode in mandatory_modes`` →
    severity=blocker; otherwise SUSPECTED is severity=minor (advisory).
    VERIFIED and INSUFFICIENT_EVIDENCE are dropped (no issue emitted).
    """
    mandatory = set(mandatory_modes or [])
    issues: list[IntegrityIssue] = []
    for raw in judgments:
        if isinstance(raw, dict):
            try:
                model = _AIFailureJudgment.model_validate(raw)
            except Exception:  # noqa: BLE001 — bad input → skip rather than crash
                continue
        else:
            model = raw

        status = (model.status or "").upper()
        if status in {"VERIFIED", "INSUFFICIENT_EVIDENCE"}:
            continue
        if status != "SUSPECTED":
            continue

        kind = f"AI_FAIL_M{model.mode}"
        severity = "blocker" if model.mode in mandatory else "minor"
        label = AI_FAILURE_MODE_LABELS.get(kind, f"AI failure mode {model.mode}")
        actionable = (
            model.suggested_followup
            or _default_followup(model.mode)
        )
        issues.append(IntegrityIssue(
            kind=kind,
            severity=severity,
            target=None,
            detail=f"{label}. {model.reasoning}".strip(),
            actionable_fix=actionable,
            evidence_url=list(model.evidence),
        ))
    return issues


# ----------------------------------------------------- mode prompt builders


def _build_m1(plan, claims, exps, kind) -> AIFailurePrompt:
    return AIFailurePrompt(
        mode=1,
        kind="AI_FAIL_M1",
        label=AI_FAILURE_MODE_LABELS["AI_FAIL_M1"],
        inputs={
            "claims": _slice_claims(claims),
            "note": "Citation hallucination detection — also covered by S2 + WebSearch.",
        },
    )


def _build_m2(plan, claims, exps, kind) -> AIFailurePrompt:
    method_claims = [
        c for c in _slice_claims(claims)
        if c.get("type") == "methodological"
    ]
    if not exps and not method_claims:
        return _skipped(2, "no methodological claims and no experiments")
    return AIFailurePrompt(
        mode=2,
        kind="AI_FAIL_M2",
        label=AI_FAILURE_MODE_LABELS["AI_FAIL_M2"],
        inputs={
            "experiments": _slice_experiments(exps),
            "methodological_claims": method_claims,
            "symbols": (plan.get("symbols") if plan else {}) or {},
        },
    )


def _build_m3(plan, claims, exps, kind) -> AIFailurePrompt:
    if kind == "theoretical":
        return _skipped(3, "paper_kind=theoretical → no numeric results to verify")
    numeric_claims = [
        c for c in _slice_claims(claims)
        if c.get("type") in {"numeric", "result"}
    ]
    results_by_exp = {
        e.get("experiment_id") or e.get("id"): e.get("results") or []
        for e in exps
    }
    if not numeric_claims and not any(results_by_exp.values()):
        return _skipped(3, "no numeric/result claims and no recorded results")
    return AIFailurePrompt(
        mode=3,
        kind="AI_FAIL_M3",
        label=AI_FAILURE_MODE_LABELS["AI_FAIL_M3"],
        inputs={
            "numeric_claims": numeric_claims,
            "experiment_results": results_by_exp,
        },
    )


def _build_m4(plan, claims, exps, kind) -> AIFailurePrompt:
    if not exps:
        return _skipped(4, "no experiments to analyse for shortcuts")
    return AIFailurePrompt(
        mode=4,
        kind="AI_FAIL_M4",
        label=AI_FAILURE_MODE_LABELS["AI_FAIL_M4"],
        inputs={
            "experiments": [
                {
                    "id": e.get("experiment_id") or e.get("id"),
                    "datasets": e.get("datasets") or [],
                    "proposed_method": e.get("proposed_method") or "",
                    "threats_to_validity": e.get("threats_to_validity") or [],
                }
                for e in _slice_experiments(exps)
            ],
        },
    )


def _build_m5(plan, claims, exps, kind) -> AIFailurePrompt:
    method_section_intent = _section_intent(plan, "03_method")
    method_text = "\n\n".join(
        e.get("proposed_method") or "" for e in _slice_experiments(exps)
    ).strip()
    if not method_text and not method_section_intent:
        return _skipped(5, "no method text or method section intent recorded")
    return AIFailurePrompt(
        mode=5,
        kind="AI_FAIL_M5",
        label=AI_FAILURE_MODE_LABELS["AI_FAIL_M5"],
        inputs={
            "method_section_intent": method_section_intent,
            "proposed_method_text": method_text,
            "methodological_claims": [
                c for c in _slice_claims(claims)
                if c.get("type") == "methodological"
            ],
        },
    )


def _build_m6(plan, claims, exps, kind) -> AIFailurePrompt:
    if not plan and not claims:
        return _skipped(6, "no paper plan or claims ledger to compare")
    return AIFailurePrompt(
        mode=6,
        kind="AI_FAIL_M6",
        label=AI_FAILURE_MODE_LABELS["AI_FAIL_M6"],
        inputs={
            "thesis": (plan.get("thesis") if plan else "") or "",
            "contributions": (plan.get("contributions") if plan else []) or [],
            "claims": _slice_claims(claims),
        },
    )


def _build_m7(plan, claims, exps, kind) -> AIFailurePrompt:
    if not exps:
        return _skipped(7, "no experiments recorded")
    has_results = any(e.get("results") for e in exps)
    if not has_results:
        return _skipped(7, "no recorded experiment results")
    return AIFailurePrompt(
        mode=7,
        kind="AI_FAIL_M7",
        label=AI_FAILURE_MODE_LABELS["AI_FAIL_M7"],
        inputs={
            "experiment_results": {
                e.get("experiment_id") or e.get("id"): e.get("results") or []
                for e in _slice_experiments(exps)
            },
            "result_claims": [
                c for c in _slice_claims(claims) if c.get("type") == "result"
            ],
            "threats_to_validity": [
                {
                    "experiment": e.get("experiment_id") or e.get("id"),
                    "threats": e.get("threats_to_validity") or [],
                }
                for e in _slice_experiments(exps)
            ],
        },
    )


# ----------------------------------------------------- helpers


def _safe_load(path) -> dict:
    raw = load_yaml(path) if path.is_file() else None
    return raw if isinstance(raw, dict) else {}


def _load_experiments(paths: ProjectPaths) -> list[dict]:
    if not paths.experiments_dir.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(paths.experiments_dir.iterdir()):
        if p.is_file() and p.suffix == ".yaml":
            data = load_yaml(p)
            if isinstance(data, dict):
                out.append(data)
    return out


def _slice_claims(claims: dict | None) -> list[dict]:
    if not isinstance(claims, dict):
        return []
    raw = claims.get("claims") or []
    out: list[dict] = []
    for c in raw[:_CLAIMS_CAP]:
        if not isinstance(c, dict):
            continue
        out.append({
            "id": c.get("id"),
            "text": (c.get("text") or "")[:240],
            "type": c.get("type"),
            "status": c.get("status"),
            "supporting_papers": list(c.get("supporting_papers") or []),
            "supporting_experiments": list(c.get("supporting_experiments") or []),
        })
    return out


def _slice_experiments(experiments: list[dict]) -> list[dict]:
    return experiments[:_EXPERIMENTS_CAP]


def _section_intent(plan: dict | None, section_name: str) -> str:
    if not isinstance(plan, dict):
        return ""
    for entry in plan.get("section_plan") or []:
        if isinstance(entry, dict) and entry.get("name") == section_name:
            return str(entry.get("intent") or "")
    return ""


def _skipped(mode: int, reason: str) -> AIFailurePrompt:
    return AIFailurePrompt(
        mode=mode,
        kind=f"AI_FAIL_M{mode}",
        label=AI_FAILURE_MODE_LABELS[f"AI_FAIL_M{mode}"],
        inputs={},
        skipped_reason=reason,
    )


def _default_followup(mode: int) -> str:
    table = {
        1: "Re-verify cited papers via Semantic Scholar / WebSearch (run /paic-integrity).",
        2: "Audit pseudocode / proposed_method against the claimed behaviour. Add unit tests.",
        3: "Trace each numeric claim back to a recorded experiment_id via paic_experiment_record_result.",
        4: "Run an ablation that breaks the suspected shortcut; document in threats_to_validity.",
        5: "Rewrite the method section so each step is reproducible without unstated assumptions.",
        6: "Reconcile paper_plan.contributions and claims.yaml so each contribution has matching claims.",
        7: "Add a sanity-check ablation; record outcome in threats_to_validity.",
    }
    return table.get(mode, "Investigate this finding before proceeding.")

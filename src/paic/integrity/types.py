"""Integrity gate types — issues, results, and shared constants.

The shape mirrors :class:`paic.latex.quality_gate.GateResult` so SKILL
renderers can iterate ``.issues`` uniformly across the structural gate
(``run_quality_gate``) and the truthfulness gate (``run_integrity_check``).

Issue ``kind`` namespace is **disjoint** from the 9-class quality gate —
hallucination kinds are 2-letter codes (``TF`` / ``PAC`` / ``IH`` / ``PH`` /
``SH``); AI-failure-mode kinds are ``AI_FAIL_M{1..7}``. SKILL output that
rolls up both gates can therefore key on ``kind`` without collision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ----------------------------------------------------- Citation hallucination

# Five-type taxonomy from GPTZero × NeurIPS 2025 (Adams et al., 2026).
# Frequencies are population-level priors used to seed advisory severity.
HALLUCINATION_KINDS: tuple[str, ...] = ("TF", "PAC", "IH", "PH", "SH")

# ----------------------------------------------------- Originality (P3-1)

# Verbatim / close-match / paraphrase kinds emitted by
# :mod:`paic.integrity.originality`. Disjoint from HALLUCINATION_KINDS
# and AI_FAILURE_MODE_KINDS so SKILL renderers can group by namespace.
ORIGINALITY_KINDS: tuple[str, ...] = (
    "ORIGINALITY_VERBATIM",
    "ORIGINALITY_CLOSE_MATCH",
    "ORIGINALITY_PARAPHRASE",
)

HALLUCINATION_LABELS: dict[str, str] = {
    "TF": "Total Fabrication — paper does not exist anywhere",
    "PAC": "Plausible Author/Conference — real authors never wrote this",
    "IH": "Incomplete Hallucination — missing DOI / volume / pages",
    "PH": "Partial Hallucination — mashup of real elements",
    "SH": "Subtle Hallucination — minor distortion of a real paper",
}

# ----------------------------------------------------- AI failure modes (Lu 2026)

# Seven-mode taxonomy. Per V1.0 design (CLAUDE.md ARS fusion plan), modes
# 1 / 3 / 5 / 6 are mandatory blockers; modes 2 / 4 / 7 are advisory only.
AI_FAILURE_MODE_KINDS: tuple[str, ...] = (
    "AI_FAIL_M1",  # Citation hallucination (also covered by HALLUCINATION_KINDS)
    "AI_FAIL_M2",  # Implementation bugs
    "AI_FAIL_M3",  # Hallucinated results (numbers without backing)
    "AI_FAIL_M4",  # Shortcut reliance
    "AI_FAIL_M5",  # Methodology fabrication
    "AI_FAIL_M6",  # Frame-lock (paper plan vs claims drift)
    "AI_FAIL_M7",  # Bug-as-insight (results misinterpreted)
)

AI_FAILURE_MODE_LABELS: dict[str, str] = {
    "AI_FAIL_M1": "Citation hallucination — fabricated or distorted references",
    "AI_FAIL_M2": "Implementation bugs — code/algorithm doesn't match described method",
    "AI_FAIL_M3": "Hallucinated results — numerical claims without experimental backing",
    "AI_FAIL_M4": "Shortcut reliance — proposed method exploits dataset artifact",
    "AI_FAIL_M5": "Methodology fabrication — described procedure cannot be executed as written",
    "AI_FAIL_M6": "Frame-lock — paper plan / claims drift from each other or experiment",
    "AI_FAIL_M7": "Bug-as-insight — anomalous result presented as discovery without sanity check",
}

# Default mandatory set per V1.0 design decision (折中策略: mandatory = M1/M3/M5/M6).
DEFAULT_MANDATORY_MODES: tuple[int, ...] = (1, 3, 5, 6)


# Verdict statuses an LLM judge may emit per mode. INSUFFICIENT_EVIDENCE means
# "prior insufficient to decide" — never blocks (advisory) so we don't fail
# theory / position papers that lack the inputs a mode needs.
JudgeStatus = Literal["VERIFIED", "SUSPECTED", "INSUFFICIENT_EVIDENCE"]


# ----------------------------------------------------- Issues / results


@dataclass
class IntegrityIssue:
    """One finding from the integrity gate.

    ``kind`` is one of HALLUCINATION_KINDS or AI_FAILURE_MODE_KINDS.
    ``severity`` follows the same scale as :class:`GateIssue` (info /
    minor / major / blocker). Citation hallucination defaults to ``major``
    until the user verifies; AI-failure modes default to ``major`` for
    SUSPECTED in mandatory modes (promoted to ``blocker`` by the runner)
    and ``minor`` for SUSPECTED in advisory modes.
    """

    kind: str
    severity: str
    target: str | None
    detail: str
    actionable_fix: str
    evidence_url: list[str] = field(default_factory=list)
    suggested_correction: dict | None = None

    def to_dict(self) -> dict:
        out: dict = {
            "kind": self.kind,
            "severity": self.severity,
            "target": self.target,
            "detail": self.detail,
            "actionable_fix": self.actionable_fix,
        }
        if self.evidence_url:
            out["evidence_url"] = list(self.evidence_url)
        if self.suggested_correction:
            out["suggested_correction"] = dict(self.suggested_correction)
        return out


@dataclass
class WebSearchPending:
    """A library citation S2 batch verify could not confirm — needs WebSearch.

    Surfaced in :class:`IntegrityResult.pending_websearch` so the caller
    (or host-orchestration directive) can dispatch a real WebSearch and
    feed the results back via ``apply_persisted_results``.
    """

    cite_key: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    expected_doi: str | None = None
    expected_arxiv_id: str | None = None
    s2_attempted: bool = True
    reason: str = "s2_no_match"  # or "s2_unavailable" / "s2_error"

    def to_dict(self) -> dict:
        return {
            "cite_key": self.cite_key,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "expected_doi": self.expected_doi,
            "expected_arxiv_id": self.expected_arxiv_id,
            "s2_attempted": self.s2_attempted,
            "reason": self.reason,
        }


@dataclass
class AIFailurePrompt:
    """A bundle the LLM judge must consume to decide one mode.

    ``mode`` is the int 1..7 for human readability; ``kind`` is the
    string the resulting issue carries (``AI_FAIL_M{mode}``).

    ``inputs`` is a dict of pre-loaded paper artefacts (paper_plan,
    claims, experiments, sections) that the judge prompt template
    references. Inline so the host directive can pass them through
    without re-reading project files in the main conversation.
    """

    mode: int
    kind: str
    label: str
    inputs: dict
    skipped_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "kind": self.kind,
            "label": self.label,
            "inputs": dict(self.inputs),
            "skipped_reason": self.skipped_reason,
        }


@dataclass
class IntegrityResult:
    """Aggregate result of one integrity gate run.

    ``passed`` is True iff no surviving issue has severity in
    {major, blocker} — same convention as :class:`GateResult`.

    Two pending lists are non-empty in host-orchestration mode (caller
    must round-trip via WebSearch + LLM judge):

    - ``pending_websearch`` — citations S2 batch verify could not
      confirm; main conversation should run WebSearch per entry.
    - ``pending_ai_judge`` — mode 1..7 LLM-judge prompts the host
      conversation must execute and feed back via
      ``apply_persisted_results``.
    """

    passed: bool
    mode: str  # "pre_review" / "final_check" / "originality"
    issues: list[IntegrityIssue]
    overrides: list[str] = field(default_factory=list)
    overrides_rejected: list[dict] = field(default_factory=list)
    mandatory_modes: list[int] = field(default_factory=lambda: list(DEFAULT_MANDATORY_MODES))
    pending_websearch: list[WebSearchPending] = field(default_factory=list)
    pending_ai_judge: list[AIFailurePrompt] = field(default_factory=list)
    s2_verified_count: int = 0
    s2_failed_count: int = 0
    cache_hits: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def has_pending_work(self) -> bool:
        """True when the caller still needs to feed WebSearch / judge results back."""
        return bool(self.pending_websearch or self.pending_ai_judge)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "mode": self.mode,
            "issue_count": len(self.issues),
            "issues": [i.to_dict() for i in self.issues],
            "overrides": list(self.overrides),
            "overrides_rejected": list(self.overrides_rejected),
            "mandatory_modes": list(self.mandatory_modes),
            "pending_websearch": [p.to_dict() for p in self.pending_websearch],
            "pending_ai_judge": [p.to_dict() for p in self.pending_ai_judge],
            "s2_verified_count": self.s2_verified_count,
            "s2_failed_count": self.s2_failed_count,
            "cache_hits": self.cache_hits,
            "notes": list(self.notes),
            "has_pending_work": self.has_pending_work,
        }

"""Integrity gate package — paper-level truthfulness checks.

Complements ``paic.latex.quality_gate`` (structural / consistency) with two
truthfulness layers borrowed from `academic-research-skills` v3.7.0:

1. **Citation hallucination** (5-type taxonomy: TF / PAC / IH / PH / SH).
   First pass via Semantic Scholar batch verify — survivors are returned as
   ``pending_websearch`` so the caller (or host orchestration) can run
   WebSearch and feed results back through ``apply_persisted_results``.

2. **AI Research Failure Modes** (7-mode checklist from Lu 2026). Each mode
   is an LLM judge over paper plan / claims / experiments. Per the V1.0
   design, modes 1 / 3 / 5 / 6 are **mandatory blockers** (M1 always
   bundles with citation hallucination above), modes 2 / 4 / 7 emit
   advisory issues.
"""

from paic.integrity.types import (
    AI_FAILURE_MODE_KINDS,
    AI_FAILURE_MODE_LABELS,
    DEFAULT_MANDATORY_MODES,
    HALLUCINATION_KINDS,
    AIFailurePrompt,
    IntegrityIssue,
    IntegrityResult,
    WebSearchPending,
)

__all__ = [
    "AI_FAILURE_MODE_KINDS",
    "AI_FAILURE_MODE_LABELS",
    "DEFAULT_MANDATORY_MODES",
    "HALLUCINATION_KINDS",
    "AIFailurePrompt",
    "IntegrityIssue",
    "IntegrityResult",
    "WebSearchPending",
]

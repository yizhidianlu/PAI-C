"""MCP tools for the /paic-integrity gate (P0-1, ARS fusion §integrity).

Two endpoints:

- ``paic_integrity_check`` — first-pass run. Performs S2 batch verification
  on the project library, builds AI-failure-mode prompts, and (when host
  orchestration is configured for the ``integrity_judge`` node) returns a
  host directive packaging the pending WebSearch list + AI-judge prompt
  bundle. When host orchestration is NOT enabled, the call runs the AI
  judge inline via the LLM client and returns the final report.

- ``paic_integrity_persist`` — second-pass companion. Receives the host
  conversation's WebSearch verdicts + AI-judge results, finalizes the
  :class:`IntegrityResult`, applies overrides, computes ``passed``, and
  writes ``<project>/.paic/state/integrity_report.yaml``.

The integrity gate is **complementary** to the structural quality gate —
they share neither code nor issue-kind namespace. The Skill / pipeline
orchestrator runs both at Stage 6 / Stage 9 boundaries.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from paic.config import load_config
from paic.integrity.runner import (
    apply_persisted_results,
    run_integrity_check,
)
from paic.integrity.types import DEFAULT_MANDATORY_MODES
from paic.llm.client import LLMClient, get_default_client
from paic.llm.host import build_host_directive
from paic.llm.router import LLMRouter
from paic.workspace.paths import (
    ProjectPaths,
    ensure_project_layout,
    resolve_project,
)

INTEGRITY_NODE = "integrity_judge"


# ----------------------------------------------------- Schemas (host directive)


class _WebSearchVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cite_key: str
    verdict: str  # VERIFIED / NOT_FOUND / MISMATCH
    evidence_url: list[str] = Field(default_factory=list)
    matched_title: str | None = None
    matched_authors: list[str] = Field(default_factory=list)
    matched_year: int | None = None
    matched_doi: str | None = None
    notes: str | None = None


class _AIJudgeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: int
    status: str  # VERIFIED / SUSPECTED / INSUFFICIENT_EVIDENCE
    reasoning: str = ""
    evidence: list[str] = Field(default_factory=list)
    suggested_followup: str | None = None


class _IntegrityHostOutput(BaseModel):
    """Schema the host conversation must produce for ``paic_integrity_persist``."""

    model_config = ConfigDict(extra="ignore")
    web_search_results: list[_WebSearchVerdict] = Field(default_factory=list)
    ai_judge_results: list[_AIJudgeResult] = Field(default_factory=list)


# ----------------------------------------------------- helpers


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


def _normalize_modes(mandatory_modes: list[int] | None) -> list[int]:
    if not mandatory_modes:
        return list(DEFAULT_MANDATORY_MODES)
    out: list[int] = []
    for m in mandatory_modes:
        try:
            mi = int(m)
        except (TypeError, ValueError):
            continue
        if 1 <= mi <= 7:
            out.append(mi)
    return out or list(DEFAULT_MANDATORY_MODES)


def _build_inline_judge(client: LLMClient):
    """Return a callable mapping :class:`AIFailurePrompt` → judgment dict.

    Used when host orchestration is not enabled — runs the LLM judge
    locally via the configured ``integrity_judge`` backend.
    """
    from paic.llm.prompts import load_prompt

    system_template = load_prompt("integrity_ai_failure")

    def _judge(prompt) -> dict:
        from paic.integrity.ai_failure_modes import _AIFailureJudgment
        user = (
            f"Mode: {prompt.mode} — {prompt.label}\n\n"
            f"Inputs (JSON):\n{prompt.inputs}\n\n"
            f"Return a single JSON object matching the schema."
        )
        try:
            judged = client.complete_json(
                system=system_template,
                user=user,
                schema=_AIFailureJudgment,
                node=INTEGRITY_NODE,
                max_tokens=1024,
                temperature=0.0,
            )
            return judged.model_dump()
        except Exception as exc:  # noqa: BLE001
            # Surface as INSUFFICIENT_EVIDENCE so failure doesn't block —
            # caller sees the note via integrity result.
            return {
                "mode": prompt.mode,
                "status": "INSUFFICIENT_EVIDENCE",
                "reasoning": f"LLM judge unavailable: {exc!r}",
                "evidence": [],
            }

    return _judge


# ----------------------------------------------------- paic_integrity_check


def integrity_check_tool(
    project_dir: str,
    *,
    mode: str = "pre_review",
    from_scratch: bool = False,
    mandatory_modes: list[int] | None = None,
    s2_enabled: bool = True,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Run the first-pass integrity gate (S2 batch + AI judge prompts).

    Behaviour by routing config:

    - **Host orchestration** (``routing.overrides.integrity_judge: host``):
      returns a host directive carrying the pending WebSearch list + AI
      judge prompt bundle. Skill must run WebSearch + LLM judge and call
      ``paic_integrity_persist``.
    - **Cloud / fixed backend**: runs AI judge inline via the configured
      backend, returns the partial report. Caller still needs to feed
      back WebSearch results (no built-in WebSearch backend exists in
      PAI-C — host orchestration is the canonical path for that).

    ``mode`` ∈ ``{"pre_review", "final_check", "originality"}``. The
    runner does not branch on mode itself; the value is stamped on the
    report so downstream tooling can distinguish Stage 6 vs Stage 9 runs.
    Use ``from_scratch=True`` at Stage 9 to invalidate cached S2
    verdicts and verify independently (per ARS iron rule).
    """
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    mandatory_list = _normalize_modes(mandatory_modes)

    cfg = load_config()
    router = LLMRouter(cfg)

    if router.is_host_orchestrated(INTEGRITY_NODE):
        partial = run_integrity_check(
            paths,
            mode=mode,
            from_scratch=from_scratch,
            mandatory_modes=mandatory_list,
            s2_enabled=s2_enabled,
            inline_judge_fn=None,
        )
        directive = build_host_directive(
            node=INTEGRITY_NODE,
            instructions=(
                "Two parallel tasks bundled together:\n\n"
                "1. **Citation verification (5-type hallucination check)** — "
                "for each entry in `metadata.pending_websearch`, run "
                "WebSearch with the title + first author + year. Decide "
                "VERIFIED / NOT_FOUND / MISMATCH and emit one entry under "
                "`web_search_results`. Use the strongest-evidence URL "
                "(publisher page > DOI > Google Scholar). For MISMATCH, "
                "include the corrected title / authors / year / doi so "
                "the persist tool can suggest a correction.\n\n"
                "2. **AI failure modes (Lu 2026)** — for each prompt under "
                "`metadata.pending_ai_judge`, take the role of a strict "
                "research-integrity reviewer and emit one judgment under "
                "`ai_judge_results`. Use INSUFFICIENT_EVIDENCE rather than "
                "SUSPECTED when the inputs lack the signal a mode needs "
                "(prevents false-positive blocks on theory / position "
                "papers).\n\n"
                "Then call mcp__paic__paic_integrity_persist with "
                "`mode=<integrity_mode from metadata>`, the structural "
                "issues already detected (passed through verbatim under "
                "`structural_issues`), and your two result lists."
            ),
            user_prompt=(
                f"Project: {paths.root}\n"
                f"Integrity mode: {mode}\n"
                f"Mandatory AI failure modes: {mandatory_list}\n"
                f"S2 verified so far: {partial.s2_verified_count} papers\n"
                f"Pending WebSearch: {len(partial.pending_websearch)} citations\n"
                f"Pending AI judge: {len(partial.pending_ai_judge)} modes\n"
            ),
            schema_hint=_IntegrityHostOutput.model_json_schema(),
            next_tool="mcp__paic__paic_integrity_persist",
            metadata={
                # Renamed from `mode` to avoid collision with the directive's
                # own top-level `mode: "host_orchestration"` field — the
                # HostOrchestrationDirective.to_dict() serializer rejects
                # metadata keys that shadow top-level fields.
                "integrity_mode": mode,
                "mandatory_modes": mandatory_list,
                "pending_websearch": [p.to_dict() for p in partial.pending_websearch],
                "pending_ai_judge": [p.to_dict() for p in partial.pending_ai_judge],
                "structural_issues": [i.to_dict() for i in partial.issues],
                "integrity_notes": list(partial.notes),
                "s2_verified_count": partial.s2_verified_count,
                "s2_failed_count": partial.s2_failed_count,
                "cache_hits": partial.cache_hits,
            },
        )
        return directive.to_dict()

    # Cloud / fixed-backend path — run AI judge inline. WebSearch still
    # has to come from the user via a follow-up persist call.
    client = llm or get_default_client()
    judge_fn = _build_inline_judge(client)
    partial = run_integrity_check(
        paths,
        mode=mode,
        from_scratch=from_scratch,
        mandatory_modes=mandatory_list,
        s2_enabled=s2_enabled,
        inline_judge_fn=judge_fn,
    )
    out = partial.to_dict()
    if partial.has_pending_work:
        out["next_tool"] = "mcp__paic__paic_integrity_persist"
    return out


# ----------------------------------------------------- paic_integrity_persist


def integrity_persist_tool(
    project_dir: str,
    *,
    mode: str = "pre_review",
    web_search_results: list[dict] | None = None,
    ai_judge_results: list[dict] | None = None,
    structural_issues: list[dict] | None = None,
    overrides: list[str] | None = None,
    mandatory_modes: list[int] | None = None,
) -> dict[str, Any]:
    """Finalize an integrity report after host WebSearch + LLM judge.

    Re-applies the runner with host-supplied verdicts; persists the
    final :class:`IntegrityResult` to
    ``<project>/.paic/state/integrity_report.yaml``.

    ``structural_issues`` is the list ``paic_integrity_check`` returned
    in its directive metadata (under ``structural_issues``) — pass it
    through verbatim so the final report contains both pre-WebSearch and
    post-WebSearch findings.
    """
    paths_or_err = _open_project(project_dir)
    if isinstance(paths_or_err, dict):
        return paths_or_err
    paths = paths_or_err

    mandatory_list = _normalize_modes(mandatory_modes)

    structural_objs = []
    for raw in structural_issues or []:
        if not isinstance(raw, dict):
            continue
        from paic.integrity.types import IntegrityIssue
        try:
            structural_objs.append(IntegrityIssue(
                kind=raw["kind"],
                severity=raw["severity"],
                target=raw.get("target"),
                detail=raw.get("detail", ""),
                actionable_fix=raw.get("actionable_fix", ""),
                evidence_url=list(raw.get("evidence_url") or []),
                suggested_correction=raw.get("suggested_correction"),
            ))
        except KeyError:
            continue

    result = apply_persisted_results(
        paths,
        mode=mode,
        web_search_results=web_search_results or [],
        ai_judge_results=ai_judge_results or [],
        structural_issues=structural_objs,
        overrides=overrides or [],
        mandatory_modes=mandatory_list,
        write_report=True,
    )
    return result.to_dict()

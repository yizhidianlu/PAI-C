"""``paic_claims_*`` MCP tools — claim ledger init / extract / validate / list."""

from __future__ import annotations

from typing import Any

from paic.library.claims import (
    _ExtractFields,
    _extract_inline_cites,
    extract_claims_from_section,
    init_claims_from_paper_plan,
    load_ledger,
    merge_claims,
    save_ledger,
    validate_ledger,
)
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.llm.host import build_host_directive
from paic.llm.prompts import load_prompt
from paic.llm.router import LLMRouter
from paic.schemas.claim import Claim, ClaimsLedger
from paic.schemas.paper_plan import PaperPlan
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml


def claims_init_tool(project_dir: str) -> dict[str, Any]:
    """Seed ``claims.yaml`` from ``paper_plan.yaml`` contributions.

    Errors if no paper plan exists. Idempotent: re-running on a project
    that already has a ledger merges new contribution-derived claims
    rather than overwriting existing ones.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    if not paths.paper_plan_yaml.is_file():
        return {
            "error": "paper_plan_not_found",
            "hint": "Run /paic-paper-plan first.",
        }
    raw = load_yaml(paths.paper_plan_yaml) or {}
    plan = PaperPlan.from_yaml_dict(raw)
    seed = init_claims_from_paper_plan(plan)

    existing = load_ledger(paths)
    merged = merge_claims(existing.claims, seed)
    ledger = ClaimsLedger(claims=merged)
    save_ledger(paths, ledger)

    return {
        "claims_count": len(ledger.claims),
        "added": len(merged) - len(existing.claims),
        "path": str(paths.claims_yaml),
    }


def claims_extract_tool(
    project_dir: str,
    section_name: str,
    section_text: str,
    contribution_id: str | None = None,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Extract claim-shaped sentences from one composed section's text.

    Merges newly-extracted claims into ``claims.yaml`` (deduped by exact
    text). Returns the new claims plus a count of how many had no support.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    if not section_text.strip():
        return _claims_extract_response(paths, section_name, [])

    from paic.config import load_config
    cfg = load_config()
    router = LLMRouter(cfg)
    if router.is_host_orchestrated("claim_extract"):
        return build_host_directive(
            node="claim_extract",
            instructions=(
                "Extract claim-shaped sentences from `user_prompt` matching "
                "`schema_hint`. Then call mcp__paic__paic_claims_extract_persist "
                "with `extracted=<your JSON>` plus `section_name` / "
                "`section_text` / `contribution_id` from `metadata`."
            ),
            user_prompt=(
                f"### SECTION: {section_name}\n\n"
                f"### TEXT\n```\n{section_text.strip()}\n```\n"
            ),
            schema_hint=_ExtractFields.model_json_schema(),
            next_tool="mcp__paic__paic_claims_extract_persist",
            metadata={
                "section_name": section_name,
                "section_text": section_text,
                "contribution_id": contribution_id,
            },
        ).to_dict()

    client = llm or get_default_client()
    try:
        new_claims = extract_claims_from_section(
            section_text,
            section_name,
            llm=client,
            contribution_id=contribution_id,
        )
    except LLMUnavailable as exc:
        return {"error": "llm_unavailable", "detail": str(exc)}

    return _claims_extract_response(paths, section_name, new_claims)


def claims_extract_persist_tool(
    project_dir: str,
    extracted: dict[str, Any],
    section_name: str,
    section_text: str,
    contribution_id: str | None = None,
) -> dict[str, Any]:
    """Persist host-generated claim extraction (LLM-free).

    Validates ``extracted`` against ``_ExtractFields`` and runs the same
    inline-cite augmentation + ledger merge as the LLM path.
    """
    from datetime import UTC, datetime
    from ulid import ULID
    from pydantic import ValidationError

    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    try:
        fields = _ExtractFields.model_validate(extracted)
    except ValidationError as exc:
        return {"error": "schema_validation_failed", "detail": exc.errors()}

    inline_cites = _extract_inline_cites(section_text)
    now = datetime.now(UTC)
    new_claims: list[Claim] = []
    for i, raw in enumerate(fields.claims, start=1):
        claim_id = f"CL_{section_name}_{i}_{ULID()}"
        required = list(raw.required_citations)
        for cite in inline_cites:
            if cite not in required:
                required.append(cite)
        new_claims.append(Claim(
            id=claim_id,
            text=raw.text,
            type=raw.type,
            status=raw.status,
            contribution_id=contribution_id,
            required_citations=required,
            appears_in_sections=[section_name],
            notes=raw.notes,
            created_at=now,
            updated_at=now,
        ))
    return _claims_extract_response(paths, section_name, new_claims)


def _claims_extract_response(paths, section_name, new_claims):
    existing = load_ledger(paths)
    merged = merge_claims(existing.claims, new_claims)
    ledger = ClaimsLedger(claims=merged)
    save_ledger(paths, ledger)

    needs_evidence = [
        c for c in new_claims
        if c.status == "needs_evidence" and c.type in {"novelty", "comparative", "numeric", "result"}
    ]
    return {
        "section_name": section_name,
        "extracted_count": len(new_claims),
        "needs_evidence_strong": [
            {"id": c.id, "type": c.type, "text": c.text} for c in needs_evidence
        ],
        "claims_count_total": len(ledger.claims),
        "path": str(paths.claims_yaml),
    }


def claims_validate_tool(
    project_dir: str,
    semantic: bool = False,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Cross-check every claim's references against the project library / experiments.

    ``semantic=True`` enables LLM-as-judge: for each ``required_citation`` whose
    paper summary exists on disk, asks whether the cited paper actually
    supports the claim. ``unrelated`` verdicts become ``unrelated_citation``
    blockers; ``partially_supports`` become ``partial_citation`` minor warnings.
    Results are cached on disk by ``sha(claim || cite || summary)`` so
    iteration is cheap.

    Returns ``{issues, by_claim, claims_count, issues_count, ok, semantic}``.
    Each issue has ``{claim_id, kind, severity, detail}``.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    client = llm
    if semantic and client is None:
        try:
            client = get_default_client()
        except LLMUnavailable as exc:
            return {"error": "llm_unavailable", "detail": str(exc)}

    ledger = load_ledger(paths)
    try:
        result = validate_ledger(ledger, paths, semantic=semantic, llm=client)
    except LLMUnavailable as exc:
        return {"error": "llm_unavailable", "detail": str(exc)}

    return {
        "claims_count": len(ledger.claims),
        "issues_count": len(result.issues),
        "ok": len(result.issues) == 0,
        "semantic": semantic,
        "issues": [
            {
                "claim_id": i.claim_id,
                "kind": i.kind,
                "severity": i.severity,
                "detail": i.detail,
            }
            for i in result.issues
        ],
        "by_claim": {
            cid: [
                {"kind": i.kind, "severity": i.severity, "detail": i.detail}
                for i in issues
            ]
            for cid, issues in result.by_claim.items()
        },
    }


def claims_list_tool(
    project_dir: str,
    status_filter: str | None = None,
) -> dict[str, Any]:
    """Read ``claims.yaml`` and optionally filter by status."""
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    ledger = load_ledger(paths)
    claims = ledger.claims
    if status_filter:
        claims = [c for c in claims if c.status == status_filter]

    return {
        "claims_count": len(claims),
        "total_in_ledger": len(ledger.claims),
        "claims": [c.model_dump(mode="json") for c in claims],
        "path": str(paths.claims_yaml),
    }

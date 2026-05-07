"""``paic_claims_*`` MCP tools — claim ledger init / extract / validate / list."""

from __future__ import annotations

from typing import Any

from paic.library.claims import (
    extract_claims_from_section,
    init_claims_from_paper_plan,
    load_ledger,
    merge_claims,
    save_ledger,
    validate_ledger,
)
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.schemas.claim import ClaimsLedger
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


def claims_validate_tool(project_dir: str) -> dict[str, Any]:
    """Cross-check every claim's references against the project library / experiments.

    Returns ``{issues, by_claim, claims_count, ok}``.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    ledger = load_ledger(paths)
    result = validate_ledger(ledger, paths)

    return {
        "claims_count": len(ledger.claims),
        "issues_count": len(result.issues),
        "ok": len(result.issues) == 0,
        "issues": [
            {"claim_id": i.claim_id, "kind": i.kind, "detail": i.detail}
            for i in result.issues
        ],
        "by_claim": {
            cid: [{"kind": i.kind, "detail": i.detail} for i in issues]
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

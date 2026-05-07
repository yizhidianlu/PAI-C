"""Claim ledger helpers (§quality phase 4).

Three distinct uses:

1. **Init from paper plan**: every paper_plan contribution → 1 Claim with
   ``type="novelty"`` and ``status="needs_evidence"``. Seeds the ledger
   so compose / polish know which strong assertions need backing.

2. **Extract from composed text**: after compose_section / polish_section
   produce LaTeX, an LLM pass classifies sentences into Claims (novelty /
   comparative / numeric / etc) and dumps any cite_keys mentioned.

3. **Validate**: cross-reference each claim's ``supporting_papers`` with
   the project library and ``supporting_experiments`` with experiments/.
   Returns a list of issues (missing cite_keys / unknown experiment_id /
   numeric claim with no provenance) for the SKILL / quality gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from ulid import ULID

from paic.llm.client import LLMClient
from paic.llm.prompts import load_prompt
from paic.schemas.claim import Claim, ClaimsLedger, ClaimStatus, ClaimType
from paic.schemas.paper_plan import ContributionEntry, PaperPlan
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, save_yaml

_CITE_KEY_RE = re.compile(r"\\cite[a-z]*\{([^}]+)\}", re.IGNORECASE)


# --- LLM-facing extraction schema -----------------------------------------

class _ExtractedClaim(BaseModel):
    """One claim parsed out of a composed section by the LLM."""

    text: str
    type: ClaimType = "factual"
    status: ClaimStatus = "needs_evidence"
    required_citations: list[str] = Field(default_factory=list)
    """Cite_keys the LLM recommends to support this claim. May or may not
    actually be in the library — we cross-reference at validate time."""

    notes: str | None = None


class _ExtractFields(BaseModel):
    claims: list[_ExtractedClaim] = Field(default_factory=list)


# --- Init from paper plan --------------------------------------------------

def init_claims_from_paper_plan(plan: PaperPlan) -> list[Claim]:
    """Seed one Claim per ContributionEntry. Each gets ``type="novelty"``
    and ``status="needs_evidence"`` until the user (or a later extract
    pass) provides supporting evidence.
    """
    now = datetime.now(UTC)
    claims: list[Claim] = []
    for i, contrib in enumerate(plan.contributions, start=1):
        claim_id = f"CL{i}"
        contrib_id = contrib.id if isinstance(contrib, ContributionEntry) else (
            contrib.get("id") if isinstance(contrib, dict) else None
        )
        contrib_text = contrib.title if isinstance(contrib, ContributionEntry) else (
            contrib.get("title", "") if isinstance(contrib, dict) else ""
        )
        contrib_desc = contrib.description if isinstance(contrib, ContributionEntry) else (
            contrib.get("description", "") if isinstance(contrib, dict) else ""
        )
        claim_text = contrib_desc or contrib_text
        claims.append(Claim(
            id=claim_id,
            text=claim_text,
            type="novelty",
            status="needs_evidence",
            contribution_id=contrib_id,
            created_at=now,
            updated_at=now,
        ))
    return claims


# --- Extract from composed text -------------------------------------------

def _extract_inline_cites(text: str) -> list[str]:
    """Pull out cite_keys from any ``\\cite{a,b}`` / ``\\citet{...}`` calls."""
    keys: list[str] = []
    for match in _CITE_KEY_RE.finditer(text):
        for key in match.group(1).split(","):
            key = key.strip()
            if key:
                keys.append(key)
    seen: set[str] = set()
    deduped: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            deduped.append(k)
    return deduped


def extract_claims_from_section(
    section_text: str,
    section_name: str,
    *,
    llm: LLMClient,
    contribution_id: str | None = None,
) -> list[Claim]:
    """Run an LLM pass over ``section_text`` to extract claim-shaped sentences.

    Returns Claims tagged with ``appears_in_sections=[section_name]`` and
    ``required_citations`` populated either from inline ``\\cite{}`` calls
    in the text or from the LLM's recommendation.
    """
    if not section_text.strip():
        return []
    user_msg = (
        f"### SECTION: {section_name}\n\n"
        f"### TEXT\n```\n{section_text.strip()}\n```\n"
    )
    fields = llm.complete_json(
        system=load_prompt("claim_extract"),
        user=user_msg,
        schema=_ExtractFields,
        max_tokens=2048,
        temperature=0.1,
        node="claim_extract",
    )
    inline_cites = _extract_inline_cites(section_text)
    now = datetime.now(UTC)
    claims: list[Claim] = []
    for i, raw in enumerate(fields.claims, start=1):
        # Stable, sortable id: ulid prefix to keep ordering / prevent collision
        # when extracting from multiple sections in one batch.
        claim_id = f"CL_{section_name}_{i}_{ULID()}"
        required = list(raw.required_citations)
        # If the LLM didn't surface inline cites, copy them in — they're
        # still ground-truth from the actual draft.
        for cite in inline_cites:
            if cite not in required:
                required.append(cite)
        claims.append(Claim(
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
    return claims


# --- Validate against library + experiments --------------------------------

@dataclass
class ValidationIssue:
    claim_id: str
    kind: str
    """``missing_cite`` / ``unknown_experiment`` / ``unsupported_strong_claim``."""

    detail: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue]
    """All issues across all claims."""

    by_claim: dict[str, list[ValidationIssue]]
    """Convenience grouping for SKILL rendering."""


_STRONG_TYPES: frozenset[ClaimType] = frozenset({
    "novelty", "comparative", "numeric", "result",
})


def validate_claim(
    claim: Claim,
    *,
    library_cite_keys: set[str],
    experiment_ids: set[str],
) -> list[ValidationIssue]:
    """Validate a single claim. Returns 0+ issues."""
    issues: list[ValidationIssue] = []

    # Required cites must exist in the library.
    for cite in claim.required_citations:
        if cite not in library_cite_keys:
            issues.append(ValidationIssue(
                claim_id=claim.id,
                kind="missing_cite",
                detail=f"Claim references cite_key '{cite}' which is not in the library.",
            ))

    # Supporting experiments must exist on disk.
    for exp_id in claim.supporting_experiments:
        if exp_id not in experiment_ids:
            issues.append(ValidationIssue(
                claim_id=claim.id,
                kind="unknown_experiment",
                detail=f"Claim references experiment_id '{exp_id}' which does not exist.",
            ))

    # Strong claim with no support → flag.
    if (
        claim.type in _STRONG_TYPES
        and claim.status not in ("supported", "todo", "rejected")
        and not claim.supporting_papers
        and not claim.supporting_experiments
        and not claim.required_citations
    ):
        issues.append(ValidationIssue(
            claim_id=claim.id,
            kind="unsupported_strong_claim",
            detail=(
                f"Strong claim of type '{claim.type}' has no supporting_papers, "
                f"supporting_experiments, or required_citations."
            ),
        ))
    return issues


def validate_ledger(ledger: ClaimsLedger, paths: ProjectPaths) -> ValidationResult:
    """Validate every claim in the ledger against the project state."""
    selected = load_yaml(paths.selected_yaml) or {}
    papers = list(selected.get("papers") or []) if isinstance(selected, dict) else []
    library_cite_keys: set[str] = set()
    for paper in papers:
        from paic.latex.filler import _cite_key
        library_cite_keys.add(_cite_key(paper))
    experiment_ids: set[str] = set()
    if paths.experiments_dir.exists():
        for path in paths.experiments_dir.iterdir():
            if path.is_file() and path.suffix == ".yaml":
                experiment_ids.add(path.stem)

    all_issues: list[ValidationIssue] = []
    by_claim: dict[str, list[ValidationIssue]] = {}
    for claim in ledger.claims:
        issues = validate_claim(
            claim,
            library_cite_keys=library_cite_keys,
            experiment_ids=experiment_ids,
        )
        if issues:
            by_claim[claim.id] = issues
            all_issues.extend(issues)
    return ValidationResult(issues=all_issues, by_claim=by_claim)


# --- Persistence helpers ---------------------------------------------------

def load_ledger(paths: ProjectPaths) -> ClaimsLedger:
    """Load ``claims.yaml`` if it exists, else return an empty ledger."""
    if not paths.claims_yaml.is_file():
        return ClaimsLedger()
    raw = load_yaml(paths.claims_yaml) or {}
    if not isinstance(raw, dict):
        return ClaimsLedger()
    return ClaimsLedger.from_yaml_dict(raw)


def save_ledger(paths: ProjectPaths, ledger: ClaimsLedger) -> None:
    paths.plans_dir.mkdir(parents=True, exist_ok=True)
    ledger.last_updated_at = datetime.now(UTC)
    save_yaml(paths.claims_yaml, ledger.model_dump(mode="json"))


def merge_claims(existing: list[Claim], new: list[Claim]) -> list[Claim]:
    """Append ``new`` claims to ``existing``, deduplicating by exact text +
    section overlap. Updates ``appears_in_sections`` / ``updated_at`` on hits."""
    out = list(existing)
    by_text: dict[str, Claim] = {c.text.strip().lower(): c for c in out}
    now = datetime.now(UTC)
    for claim in new:
        key = claim.text.strip().lower()
        if key in by_text:
            target = by_text[key]
            # Merge appears_in_sections + required_citations + supporting_papers.
            for sec in claim.appears_in_sections:
                if sec not in target.appears_in_sections:
                    target.appears_in_sections.append(sec)
            for cite in claim.required_citations:
                if cite not in target.required_citations:
                    target.required_citations.append(cite)
            for cite in claim.supporting_papers:
                if cite not in target.supporting_papers:
                    target.supporting_papers.append(cite)
            target.updated_at = now
        else:
            out.append(claim)
            by_text[key] = claim
    return out

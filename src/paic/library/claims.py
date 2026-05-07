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

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

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


# --- Semantic claim ↔ citation judging ------------------------------------

_JudgeVerdict = Literal["supports", "partially_supports", "unrelated"]


class _JudgeResult(BaseModel):
    """One LLM judgment of whether a cited paper supports a claim."""

    verdict: _JudgeVerdict
    rationale: str


def _summary_text_for(paths: ProjectPaths, cite_key: str) -> str | None:
    """Read ``library/summaries/<cite_key>.md`` (the structured summary). Returns
    None if the summary doesn't exist on disk yet."""
    if not paths.summaries_dir.exists():
        return None
    md_path = paths.summaries_dir / f"{cite_key}.md"
    if not md_path.is_file():
        return None
    try:
        return md_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _paper_context_for(
    paths: ProjectPaths,
    cite_key: str,
    claim_text: str,
    *,
    explicit_chunk_ids: list[str] | None = None,
    top_k: int = 3,
) -> str | None:
    """Build the paper-context text fed to the claim judge (P0 #1).

    Priority order:
    1. ``explicit_chunk_ids`` — when the claim already records which chunks
       it was extracted against, use those exactly. Skips chunks not on disk.
    2. BM25 over the paper's chunks against ``claim_text`` — pick the top-K
       most relevant passages. Tighter than dumping a whole summary.
    3. Fall back to ``library/summaries/<cite_key>.md`` (legacy path).

    Returns None when none of those yield text — caller silently skips
    judging that cite_key.
    """
    from paic.library.chunker import load_chunk_index
    from paic.library.retrieval import _tokenize  # local import — avoid cycle at module load

    chunks = load_chunk_index(paths, cite_key)
    if chunks:
        if explicit_chunk_ids:
            wanted = {cid for cid in explicit_chunk_ids if cid}
            picked = [c for c in chunks if c.chunk_id in wanted]
            if picked:
                return "\n\n".join(c.text for c in picked)
        # Rank-and-pick by query-token overlap (BM25 as tiebreaker).
        # Per-paper corpora are tiny (often 5-30 chunks); BM25 IDF
        # collapses to 0 when half the chunks share a token, which
        # would silently drop matching chunks. Token overlap stays
        # meaningful at any corpus size.
        try:
            from rank_bm25 import BM25Okapi
            token_lists = [_tokenize(c.text) for c in chunks]
            if any(token_lists):
                claim_tokens = set(_tokenize(claim_text))
                try:
                    bm25 = BM25Okapi(token_lists)
                    bm25_scores = bm25.get_scores(list(claim_tokens))
                except (ZeroDivisionError, ValueError):
                    bm25_scores = [0.0] * len(chunks)
                ranked: list[tuple[int, int, float]] = []
                for i, _chunk in enumerate(chunks):
                    overlap = len(claim_tokens & set(token_lists[i]))
                    if overlap == 0:
                        continue
                    ranked.append((i, overlap, float(bm25_scores[i])))
                ranked.sort(key=lambda r: (r[1], r[2]), reverse=True)
                picked = [chunks[i] for i, _, _ in ranked[:top_k]]
                if picked:
                    return "\n\n".join(c.text for c in picked)
        except Exception:  # noqa: BLE001 — never block validate on chunk-rank failure
            pass

    return _summary_text_for(paths, cite_key)


def _judge_cache_dir() -> Path:
    """Per-user cache for judge results — keyed by ``sha(claim || cite || summary)``.

    Result reuse across re-runs avoids hitting the LLM for every
    ``/paic-finalize`` when the user iterates on overrides.
    """
    base = Path(os.environ.get("PAIC_HOME", str(Path.home() / ".paic")))
    cache = base / "cache" / "claim_judge"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _judge_cache_key(claim_text: str, cite_key: str, summary: str) -> str:
    payload = f"{claim_text.strip()}|{cite_key}|{summary.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def judge_claim_against_summary(
    claim_text: str,
    paper_summary: str,
    cite_key: str,
    *,
    llm: LLMClient,
    use_cache: bool = True,
) -> _JudgeResult:
    """Ask an LLM whether ``paper_summary`` (the cited paper's structured
    summary) actually supports ``claim_text``. Cached on disk by
    ``sha(claim || cite || summary)`` so repeated runs are free.
    """
    cache_dir = _judge_cache_dir()
    cache_key = _judge_cache_key(claim_text, cite_key, paper_summary)
    cache_path = cache_dir / f"{cache_key}.json"

    if use_cache and cache_path.is_file():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return _JudgeResult.model_validate(payload)
        except Exception:
            pass  # corrupt cache → fall through to a fresh call

    user_msg = (
        f"### CLAIM\n{claim_text.strip()}\n\n"
        f"### CITED PAPER (cite_key: {cite_key})\n"
        f"### PAPER SUMMARY\n{paper_summary.strip() or '(no summary)'}\n"
    )
    result = llm.complete_json(
        system=load_prompt("claim_judge"),
        user=user_msg,
        schema=_JudgeResult,
        max_tokens=512,
        temperature=0.0,
        node="claim_judge",
    )

    if use_cache:
        try:
            cache_path.write_text(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass  # cache best-effort

    return result


# --- Validate against library + experiments --------------------------------

@dataclass
class ValidationIssue:
    claim_id: str
    kind: str
    """``missing_cite`` / ``unknown_experiment`` / ``unsupported_strong_claim`` /
    ``unrelated_citation`` / ``partial_citation``."""

    detail: str
    severity: str = "major"
    """``minor`` / ``major`` / ``blocker``. Defaults to ``major`` for
    backward compatibility; semantic-check kinds set this explicitly."""


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


def validate_ledger(
    ledger: ClaimsLedger,
    paths: ProjectPaths,
    *,
    semantic: bool = False,
    llm: LLMClient | None = None,
) -> ValidationResult:
    """Validate every claim in the ledger against the project state.

    ``semantic=True`` adds an LLM-as-judge pass: for each claim, every cite_key
    in ``required_citations`` whose summary exists on disk is fed to the
    ``claim_judge`` prompt; the verdict drives a new issue:

    - ``unrelated`` → ``unrelated_citation`` (severity=blocker — paper truly
      doesn't support the claim, citation is misplaced)
    - ``partially_supports`` → ``partial_citation`` (severity=minor — paper
      is related but the claim is stronger than what it shows)
    - ``supports`` → no issue

    Verdicts are cached on disk so repeated calls (typical iteration loop)
    are free. Pass ``semantic=False`` (the default) for the structural-only
    fast path used by the original v0.1 callers.
    """
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

        if semantic and llm is not None:
            # For every cite_key the claim relies on, build a paper-context
            # text (chunk-ranked when available, summary fallback) and ask
            # the LLM judge whether that paper supports the claim. Cite_keys
            # without any context are silently skipped (we can't judge what
            # we can't read).
            chunk_ids_for_paper: dict[str, list[str]] = {}
            for chunk_id in claim.supporting_chunks:
                # ``<cite_key>__c<NNN>`` — split on the first '__'.
                if "__" not in chunk_id:
                    continue
                ck = chunk_id.split("__", 1)[0]
                chunk_ids_for_paper.setdefault(ck, []).append(chunk_id)
            for cite_key in claim.required_citations:
                if cite_key not in library_cite_keys:
                    continue  # already flagged as missing_cite above
                paper_text = _paper_context_for(
                    paths,
                    cite_key,
                    claim.text,
                    explicit_chunk_ids=chunk_ids_for_paper.get(cite_key),
                )
                if paper_text is None or not paper_text.strip():
                    continue
                verdict = judge_claim_against_summary(
                    claim.text, paper_text, cite_key, llm=llm
                )
                if verdict.verdict == "unrelated":
                    issues.append(ValidationIssue(
                        claim_id=claim.id,
                        kind="unrelated_citation",
                        severity="blocker",
                        detail=(
                            f"Cite '{cite_key}' does not support the claim. "
                            f"Judge rationale: {verdict.rationale}"
                        ),
                    ))
                elif verdict.verdict == "partially_supports":
                    issues.append(ValidationIssue(
                        claim_id=claim.id,
                        kind="partial_citation",
                        severity="minor",
                        detail=(
                            f"Cite '{cite_key}' partially supports the claim. "
                            f"Consider weakening the claim or adding a stronger "
                            f"citation. Judge rationale: {verdict.rationale}"
                        ),
                    ))

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

"""Paragraph-level compose pipeline (§quality phase 6).

Where ``compose_section(mode="from_stub" | "from_scratch")`` does a single
LLM call to generate a whole section, paragraph-mode breaks the work into
three smaller steps so long sections retain structure, cite weakly-bound
claims explicitly, and reject repetitive content:

1. ``outline_section`` — LLM emits a list of ``ParagraphSpec`` (role +
   intent + claim_ids + cite_key candidates + target_words).
2. ``write_paragraph`` — for each spec, LLM generates that paragraph's
   LaTeX with previous paragraphs as context.
3. ``coherence_polish`` — final LLM pass smooths transitions and
   eliminates repeated phrasings across paragraphs.

The whole pipeline is N + 2 LLM calls (where N = paragraph count, typically
3-6) — heavier than single-shot but produces sections grounded in the
plan, claims, and retrieval evidence rather than re-derived per call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from paic.llm.client import LLMClient
from paic.llm.prompts import load_prompt

ParagraphRole = Literal[
    "motivation", "background", "contrast", "method", "result", "discussion",
    "summary", "transition",
]


class ParagraphSpec(BaseModel):
    """A single paragraph's plan, output by :func:`outline_section`."""

    id: str
    """Stable id like ``P1`` / ``P2`` for cross-referencing."""

    role: ParagraphRole
    intent: str
    """One sentence: what this paragraph is supposed to accomplish."""

    claim_ids: list[str] = Field(default_factory=list)
    """Claim ids this paragraph advances. Sourced from ``claims.yaml``."""

    cite_key_candidates: list[str] = Field(default_factory=list)
    """Cite_keys the paragraph *may* draw from — surfaced by Phase 2 retrieval.
    The LLM is told to pick from this list; it's not forced to use all of them."""

    target_words: int = 100


class _OutlineFields(BaseModel):
    paragraphs: list[ParagraphSpec] = Field(default_factory=list)


# --- Step 1: outline ------------------------------------------------------


def outline_section(
    *,
    section: str,
    paper_plan: dict[str, Any] | None,
    idea: dict[str, Any] | None,
    experiment: dict[str, Any] | None,
    retrieval_hits: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    target_words: int,
    llm: LLMClient,
    instruction: str | None = None,
) -> list[ParagraphSpec]:
    """Run the outline-generation LLM call.

    ``retrieval_hits`` is a list of ``{cite_key, title, snippet, match_reason}``
    dicts (the shape ``paic_library_retrieve`` returns). ``claims`` is a
    list of ``Claim`` dicts (the shape ``paic_claims_list`` returns).
    """
    user_msg = _format_outline_prompt(
        section=section,
        paper_plan=paper_plan,
        idea=idea,
        experiment=experiment,
        retrieval_hits=retrieval_hits,
        claims=claims,
        target_words=target_words,
        instruction=instruction,
    )
    fields = llm.complete_json(
        system=load_prompt("paragraph_outline"),
        user=user_msg,
        schema=_OutlineFields,
        max_tokens=2048,
        temperature=0.2,
        node="paragraph_outline",
    )
    return list(fields.paragraphs)


def _format_outline_prompt(
    *,
    section: str,
    paper_plan: dict[str, Any] | None,
    idea: dict[str, Any] | None,
    experiment: dict[str, Any] | None,
    retrieval_hits: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    target_words: int,
    instruction: str | None,
) -> str:
    parts: list[str] = [
        f"Section: {section}",
        f"Total target length: ~{target_words} words across all paragraphs (soft).",
    ]
    if instruction:
        parts.append(f"User instruction: {instruction.strip()}")

    parts.append("")
    parts.append("Paper plan:")
    if paper_plan:
        if thesis := paper_plan.get("thesis"):
            parts.append(f"  - thesis: {thesis}")
        for entry in (paper_plan.get("section_plan") or []):
            if isinstance(entry, dict) and entry.get("name") == section:
                parts.append(f"  - this section's intent: {entry.get('intent', '')}")
                supports = entry.get("supports_contributions") or []
                if supports:
                    parts.append(f"  - supports contributions: {supports}")
                break
    else:
        parts.append("  (no paper plan — outline generically)")

    parts.append("")
    parts.append("Idea (high-level):")
    if idea:
        for key in ("title", "one_liner", "novelty_claim", "expected_contribution"):
            v = idea.get(key)
            if v:
                parts.append(f"  - {key}: {v}")

    if experiment and section in {"03_method", "04_experiments"}:
        parts.append("")
        parts.append("Experiment summary:")
        for key in ("proposed_method", "datasets", "baselines", "metrics", "ablations"):
            v = experiment.get(key)
            if v:
                parts.append(f"  - {key}: {v}")

    parts.append("")
    parts.append("Available cite_keys (from section-targeted retrieval):")
    if retrieval_hits:
        for hit in retrieval_hits[:25]:
            cite_key = hit.get("cite_key", "?")
            title = hit.get("title", "")
            snippet = hit.get("snippet") or ""
            parts.append(f"  - [{cite_key}] {title} — {snippet[:120]}")
    else:
        parts.append("  (no retrieval hits)")

    parts.append("")
    parts.append("Existing claims that may need surfacing in this section:")
    if claims:
        for c in claims[:15]:
            parts.append(
                f"  - [{c.get('id', '?')}] ({c.get('type', '?')}, "
                f"{c.get('status', '?')}): {c.get('text', '')[:160]}"
            )
    else:
        parts.append("  (no claims yet)")

    return "\n".join(parts)


# --- Step 2: write each paragraph -----------------------------------------


def write_paragraph(
    *,
    spec: ParagraphSpec,
    prev_paragraphs: list[str],
    section: str,
    paper_plan: dict[str, Any] | None,
    idea: dict[str, Any] | None,
    retrieval_hits: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    llm: LLMClient,
) -> str:
    """Generate one paragraph's LaTeX from a spec.

    ``prev_paragraphs`` is the list of previously-written paragraphs in the
    same section, so the LLM can avoid repeating earlier phrasing and
    maintain logical flow.
    """
    parts: list[str] = [
        f"Section: {section}",
        f"Paragraph id: {spec.id} (role: {spec.role})",
        f"Intent: {spec.intent}",
        f"Target length: ~{spec.target_words} words.",
        "",
        f"Claim ids this paragraph must advance: {spec.claim_ids}",
        f"Cite_key candidates (pick 0-3 most relevant; you MAY cite others if needed): {spec.cite_key_candidates}",
    ]

    if paper_plan:
        if terminology := paper_plan.get("terminology"):
            if isinstance(terminology, dict) and terminology:
                parts.append("")
                parts.append("Terminology to use exactly:")
                for term, defn in terminology.items():
                    parts.append(f"  - \"{term}\": {defn}")

    parts.append("")
    parts.append("Claim texts (for context):")
    claim_by_id = {c.get("id"): c for c in claims if isinstance(c, dict)}
    for cid in spec.claim_ids:
        c = claim_by_id.get(cid)
        if c:
            parts.append(f"  - [{cid}] ({c.get('type', '?')}): {c.get('text', '')}")

    parts.append("")
    parts.append("Cite_key snippets (for grounding; pick the relevant ones):")
    hit_by_key = {h.get("cite_key"): h for h in retrieval_hits if isinstance(h, dict)}
    for key in spec.cite_key_candidates:
        h = hit_by_key.get(key)
        if h:
            snippet = h.get("snippet") or ""
            parts.append(f"  - [{key}]: {snippet[:200]}")

    if idea:
        parts.append("")
        parts.append("Paper-level context:")
        for key in ("title", "one_liner", "novelty_claim"):
            v = idea.get(key)
            if v:
                parts.append(f"  - {key}: {v}")

    parts.append("")
    parts.append("Previously-written paragraphs in this section (do NOT repeat their phrasing):")
    if prev_paragraphs:
        for i, prev in enumerate(prev_paragraphs[-5:], start=1):
            parts.append(f"  --- prev #{i} ---")
            parts.append(prev[:600])
    else:
        parts.append("  (this is the first paragraph)")

    parts.append("")
    parts.append(
        "Output the paragraph as LaTeX. Single paragraph only — no headings, "
        "no fences, no commentary. If you cite, use \\cite{key} format."
    )

    response = llm.complete(
        system=load_prompt("paragraph_write"),
        user="\n".join(parts),
        max_tokens=900,
        temperature=0.3,
        node="paragraph_write",
    )
    return response.text.strip()


# --- Step 3: coherence polish ---------------------------------------------


def coherence_polish(
    *,
    section: str,
    paragraphs: list[str],
    paper_plan: dict[str, Any] | None,
    llm: LLMClient,
) -> str:
    """Smoothing pass over the joined paragraphs.

    Catches repeated short phrases / weak transitions / orphan
    pronouns. Does NOT re-cite or change \\cite calls.
    """
    if not paragraphs:
        return ""
    joined = "\n\n".join(paragraphs)

    parts = [f"Section: {section}", ""]
    if paper_plan:
        if terminology := paper_plan.get("terminology"):
            if isinstance(terminology, dict) and terminology:
                parts.append("Terminology to keep exact:")
                for term in terminology:
                    parts.append(f"  - {term}")
                parts.append("")
    parts.append("Existing draft:")
    parts.append("```latex")
    parts.append(joined)
    parts.append("```")
    parts.append("")
    parts.append(
        "Output the polished section as LaTeX. Preserve all \\cite{} calls "
        "verbatim. Do NOT add new claims, do NOT remove paragraphs. "
        "No fences, no commentary."
    )

    response = llm.complete(
        system=load_prompt("section_coherence_polish"),
        user="\n".join(parts),
        max_tokens=2400,
        temperature=0.2,
        node="section_coherence_polish",
    )
    return response.text.strip()


# --- Top-level orchestrator -----------------------------------------------


@dataclass
class ParagraphComposeResult:
    section: str
    section_text: str
    paragraphs: list[str]
    """Per-paragraph LaTeX (post-polish — i.e. the polish output split back).
    Whether to use ``section_text`` or ``paragraphs`` is up to the caller;
    they're functionally equivalent."""

    specs: list[ParagraphSpec]


def compose_section_paragraphs(
    *,
    section: str,
    paper_plan: dict[str, Any] | None,
    idea: dict[str, Any] | None,
    experiment: dict[str, Any] | None,
    retrieval_hits: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    target_words: int,
    llm: LLMClient,
    instruction: str | None = None,
    skip_polish: bool = False,
) -> ParagraphComposeResult:
    """Run the full paragraph-mode pipeline. Used by ``compose_section`` when
    ``mode="paragraph"``."""
    specs = outline_section(
        section=section,
        paper_plan=paper_plan,
        idea=idea,
        experiment=experiment,
        retrieval_hits=retrieval_hits,
        claims=claims,
        target_words=target_words,
        llm=llm,
        instruction=instruction,
    )
    paragraphs: list[str] = []
    for spec in specs:
        text = write_paragraph(
            spec=spec,
            prev_paragraphs=paragraphs,
            section=section,
            paper_plan=paper_plan,
            idea=idea,
            retrieval_hits=retrieval_hits,
            claims=claims,
            llm=llm,
        )
        paragraphs.append(text)
    if skip_polish or not paragraphs:
        return ParagraphComposeResult(
            section=section,
            section_text="\n\n".join(paragraphs).strip() + "\n",
            paragraphs=paragraphs,
            specs=specs,
        )
    polished = coherence_polish(
        section=section,
        paragraphs=paragraphs,
        paper_plan=paper_plan,
        llm=llm,
    )
    # Normalize trailing newline.
    if not polished.endswith("\n"):
        polished += "\n"
    return ParagraphComposeResult(
        section=section,
        section_text=polished,
        paragraphs=[p.strip() for p in polished.split("\n\n") if p.strip()],
        specs=specs,
    )

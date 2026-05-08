"""Image-prompt synthesis — turn a single FigureSlot into a string the
image API can consume. Handled by the same LLM router as planning, under
node tag ``figure_prompt``."""

from __future__ import annotations

from pydantic import BaseModel

from paic.images.planner import FigureSlot
from paic.llm.client import LLMClient
from paic.llm.prompts import load_prompt


class _ImagePromptOutput(BaseModel):
    prompt: str


# Caps on the brief blocks — image prompt models accept ~4000 char prompts,
# but the figure_prompt LLM compresses everything into ≤500 char output, so
# inflating the input past ~2000 char wastes tokens without helping the
# downstream image. The numbers below leave ~600 char headroom for the
# scene_description + system prompt.
_CLAIM_TEXT_CAP = 200
_TERM_CAP = 12             # number of terminology entries
_TERM_GLOSS_CAP = 60       # chars per gloss


def build_figure_user_prompt(
    slot: FigureSlot,
    *,
    paper_plan: dict | None = None,
    claims_by_id: dict[str, dict] | None = None,
    extra_instruction: str | None = None,
) -> str:
    """Compose the user message fed to figure_prompt LLM (and host directive).

    Single source of truth for the cloud and host paths — keeps them in
    lockstep so behavior change requires touching one function. ``paper_plan``
    and ``claims_by_id`` are raw yaml dicts (not pydantic models) so the
    function can be called from MCP tools without importing the schema layer.

    The output stacks blocks in priority order; image-prompt LLMs read top-down
    and weight earlier instructions more heavily.
    """
    parts = [
        f"slot: {slot.slot}",
        f"kind: {slot.kind}",
        f"section: {slot.section_hint}",
        f"scene_description: {slot.scene_description}",
        f"caption_hint: {slot.caption_hint}",
    ]

    # [Supporting claims] — split PRIMARY (primary_claim_id) from ALSO/secondary
    if slot.supporting_claims and claims_by_id:
        claim_lines: list[str] = []
        primary_id = slot.primary_claim_id
        ordered: list[str] = []
        if primary_id and primary_id in claims_by_id:
            ordered.append(primary_id)
        for cid in slot.supporting_claims:
            if cid != primary_id and cid in claims_by_id:
                ordered.append(cid)
        for cid in ordered:
            claim = claims_by_id.get(cid) or {}
            text = (claim.get("text") or "")[:_CLAIM_TEXT_CAP]
            ctype = claim.get("type") or "claim"
            tag = "PRIMARY" if cid == primary_id else "ALSO"
            claim_lines.append(f"- {tag} [{cid}] ({ctype}): {text}")
        if claim_lines:
            parts.append("[Supporting claims]")
            parts.extend(claim_lines)

    # [Section intent] — from paper_plan.section_plan, matched by section_hint
    if paper_plan and slot.section_hint:
        section_plan = paper_plan.get("section_plan") or []
        for s in section_plan:
            if not isinstance(s, dict):
                continue
            if s.get("name") == slot.section_hint:
                intent = s.get("intent") or ""
                if intent:
                    parts.append(f"[Section intent] {slot.section_hint}: {intent}")
                break

    # [Paper terminology] — vocabulary lock; capped to keep prompt size sane
    if paper_plan:
        terminology = paper_plan.get("terminology") or {}
        if isinstance(terminology, dict) and terminology:
            term_lines = ["[Paper terminology — use verbatim, do not substitute]"]
            for term, gloss in list(terminology.items())[:_TERM_CAP]:
                short = (gloss or "")[:_TERM_GLOSS_CAP]
                term_lines.append(f"- {term}: {short}")
            parts.extend(term_lines)

    if extra_instruction:
        parts.append(f"extra_instruction: {extra_instruction}")
    return "\n".join(parts)


def synthesize_image_prompt(
    llm: LLMClient,
    slot: FigureSlot,
    *,
    paper_plan: dict | None = None,
    claims_by_id: dict[str, dict] | None = None,
    extra_instruction: str | None = None,
) -> str:
    """Run the prompt-synthesis LLM call and return the API-ready prompt.

    ``paper_plan`` and ``claims_by_id`` are optional grounding sources —
    the user message is always valid even when both are None (back-compat
    with callers that don't have a paper plan or claims ledger yet).
    """
    system = load_prompt("figure_prompt")
    user = build_figure_user_prompt(
        slot,
        paper_plan=paper_plan,
        claims_by_id=claims_by_id,
        extra_instruction=extra_instruction,
    )
    raw = llm.complete_json(
        system=system,
        user=user,
        schema=_ImagePromptOutput,
        max_tokens=512,
        temperature=0.4,
        node="figure_prompt",
    )
    return raw.prompt.strip()

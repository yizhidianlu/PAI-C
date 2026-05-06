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


def synthesize_image_prompt(
    llm: LLMClient,
    slot: FigureSlot,
    *,
    extra_instruction: str | None = None,
) -> str:
    """Run the prompt-synthesis LLM call and return the API-ready prompt.

    ``extra_instruction`` is an optional caller hint appended to the user
    message (e.g. ``/paic-figure generate`` overrides the scene description).
    """
    system = load_prompt("figure_prompt")
    user_parts = [
        f"slot: {slot.slot}",
        f"kind: {slot.kind}",
        f"section: {slot.section_hint}",
        f"scene_description: {slot.scene_description}",
        f"caption_hint: {slot.caption_hint}",
    ]
    if extra_instruction:
        user_parts.append(f"extra_instruction: {extra_instruction}")
    raw = llm.complete_json(
        system=system,
        user="\n".join(user_parts),
        schema=_ImagePromptOutput,
        max_tokens=512,
        temperature=0.4,
        node="figure_prompt",
    )
    return raw.prompt.strip()

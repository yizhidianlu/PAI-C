"""Figure planner — picks how many figures to draw and where they go.

Reads a polished draft (or, if absent, the idea + experiment context)
and returns a structured list of :class:`FigureSlot` proposals. The
output is persisted to ``<project>/.paic/figures/_plan.yaml`` so the
user can review/edit before generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from paic.llm.client import LLMClient
from paic.llm.prompts import load_prompt
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml

FigureKind = Literal["teaser", "concept", "domain"]
_SLUG_RE = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class FigureSlot:
    slot: str
    kind: FigureKind
    section_hint: str
    position_hint: str
    scene_description: str
    caption_hint: str
    rationale: str

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "kind": self.kind,
            "section_hint": self.section_hint,
            "position_hint": self.position_hint,
            "scene_description": self.scene_description,
            "caption_hint": self.caption_hint,
            "rationale": self.rationale,
        }


class _FigureSlotOut(BaseModel):
    slot: str
    kind: FigureKind = "concept"
    section_hint: str = "intro"
    position_hint: str = ""
    scene_description: str
    caption_hint: str = ""
    rationale: str = ""


class _FigurePlanOutput(BaseModel):
    slots: list[_FigureSlotOut] = Field(default_factory=list)


def _slugify(name: str) -> str:
    """Force a slot name into [a-z0-9_] (paths + LaTeX labels both demand it)."""
    s = _SLUG_RE.sub("_", name.lower()).strip("_")
    return s or "fig"


def _ensure_unique(slots: list[_FigureSlotOut]) -> list[_FigureSlotOut]:
    seen: dict[str, int] = {}
    out: list[_FigureSlotOut] = []
    for s in slots:
        slug = _slugify(s.slot)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        if n > 0:
            slug = f"{slug}_{n + 1}"
        out.append(s.model_copy(update={"slot": slug}))
    return out


def _gather_paper_context(
    paths: ProjectPaths, draft_path: Path | None
) -> str:
    """Compose the planner's user message body.

    Priority: explicit draft → idea+experiment fallback → minimal
    project.yaml info. Truncates each piece so the call fits a reasonable
    token budget; the planner doesn't need every word, just the gist.
    """
    chunks: list[str] = []
    if draft_path and draft_path.exists():
        text = draft_path.read_text(encoding="utf-8", errors="replace")
        chunks.append(f"=== POLISHED DRAFT ({draft_path.name}) ===\n{text[:8000]}")
        return "\n\n".join(chunks)

    project_yaml = load_yaml(paths.project_yaml, default={}) or {}
    title = project_yaml.get("title")
    venue = project_yaml.get("venue")
    if title or venue:
        chunks.append(f"=== PROJECT ===\ntitle: {title}\nvenue: {venue}")

    # Pick the highest-scored idea (or the only one) as a stand-in.
    ideas = sorted(paths.ideas_dir.glob("*.yaml"))
    for idea_path in ideas:
        if idea_path.name == "_ranking.yaml":
            continue
        idea = load_yaml(idea_path, default={}) or {}
        chunks.append(
            "=== IDEA ===\n"
            f"title: {idea.get('title')}\n"
            f"one_liner: {idea.get('one_liner')}\n"
            f"motivation: {idea.get('motivation')}\n"
            f"proposed_approach: {idea.get('proposed_approach')}"
        )
        break

    # Pick the most recent experiment.
    exps = sorted(paths.experiments_dir.glob("*.yaml"))
    if exps:
        exp = load_yaml(exps[-1], default={}) or {}
        chunks.append(
            "=== EXPERIMENT ===\n"
            f"proposed_method: {exp.get('proposed_method')}\n"
            f"datasets: {[d.get('name') for d in (exp.get('datasets') or [])]}\n"
            f"metrics: {[m.get('name') for m in (exp.get('metrics') or [])]}"
        )

    if not chunks:
        chunks.append("(no project / idea / experiment context found)")
    return "\n\n".join(chunks)


def plan_figures(
    llm: LLMClient,
    *,
    paths: ProjectPaths,
    draft_path: Path | None = None,
    max_figures: int = 4,
) -> list[FigureSlot]:
    """Run the planner LLM call and return validated FigureSlot list."""
    system = load_prompt("figure_plan")
    body = _gather_paper_context(paths, draft_path)
    user = (
        f"max_figures: {max_figures}\n\n"
        f"PAPER CONTEXT:\n{body}\n\n"
        "Return the JSON object now."
    )
    raw = llm.complete_json(
        system=system,
        user=user,
        schema=_FigurePlanOutput,
        max_tokens=2048,
        temperature=0.3,
        node="figure_plan",
    )
    capped = raw.slots[:max_figures]
    deduped = _ensure_unique(capped)
    return [
        FigureSlot(
            slot=s.slot,
            kind=s.kind,
            section_hint=s.section_hint,
            position_hint=s.position_hint,
            scene_description=s.scene_description,
            caption_hint=s.caption_hint,
            rationale=s.rationale,
        )
        for s in deduped
    ]

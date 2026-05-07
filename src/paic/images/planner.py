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
    # §quality phase 9 — claim-driven figure planning. Defaults preserve
    # backward compatibility with pre-phase-9 plans on disk.
    supporting_claims: tuple[str, ...] = ()
    no_visual_reason: str | None = None

    def to_dict(self) -> dict:
        d = {
            "slot": self.slot,
            "kind": self.kind,
            "section_hint": self.section_hint,
            "position_hint": self.position_hint,
            "scene_description": self.scene_description,
            "caption_hint": self.caption_hint,
            "rationale": self.rationale,
            "supporting_claims": list(self.supporting_claims),
        }
        if self.no_visual_reason:
            d["no_visual_reason"] = self.no_visual_reason
        return d


class _FigureSlotOut(BaseModel):
    slot: str
    kind: FigureKind = "concept"
    section_hint: str = "intro"
    position_hint: str = ""
    scene_description: str
    caption_hint: str = ""
    rationale: str = ""
    supporting_claims: list[str] = Field(default_factory=list)
    no_visual_reason: str | None = None


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


def _gather_claim_context(paths: ProjectPaths) -> str:
    """§quality phase 9 — surface the claim ledger so the planner can bind
    each figure slot to the contributions / claims it supports."""
    if not paths.claims_yaml.is_file():
        return ""
    raw = load_yaml(paths.claims_yaml, default={}) or {}
    if not isinstance(raw, dict):
        return ""
    claims = raw.get("claims") or []
    if not claims:
        return ""
    lines = ["=== CLAIM LEDGER (bind figure slots to these claim ids when applicable) ==="]
    for c in claims[:25]:
        if not isinstance(c, dict):
            continue
        cid = c.get("id", "?")
        ctype = c.get("type", "?")
        text = (c.get("text") or "")[:160]
        lines.append(f"- [{cid}] ({ctype}): {text}")
    return "\n".join(lines)


def _gather_paper_plan_context(paths: ProjectPaths) -> str:
    """§quality phase 9 — surface contributions so the planner ensures every
    contribution has at least one figure / table / algorithm or a stated
    no_visual_reason."""
    if not paths.paper_plan_yaml.is_file():
        return ""
    raw = load_yaml(paths.paper_plan_yaml, default={}) or {}
    if not isinstance(raw, dict):
        return ""
    contribs = raw.get("contributions") or []
    if not contribs:
        return ""
    lines = ["=== PAPER CONTRIBUTIONS (each MUST have >=1 figure / table / algorithm or no_visual_reason) ==="]
    for c in contribs:
        if not isinstance(c, dict):
            continue
        lines.append(f"- [{c.get('id', '?')}] {c.get('title', '')}: {c.get('description', '')}")
    return "\n".join(lines)


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
    body_parts = [_gather_paper_context(paths, draft_path)]
    plan_ctx = _gather_paper_plan_context(paths)
    if plan_ctx:
        body_parts.append(plan_ctx)
    claims_ctx = _gather_claim_context(paths)
    if claims_ctx:
        body_parts.append(claims_ctx)
    body = "\n\n".join(p for p in body_parts if p)
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
            supporting_claims=tuple(s.supporting_claims),
            no_visual_reason=s.no_visual_reason,
        )
        for s in deduped
    ]


def verify_claim_coverage(
    paths: ProjectPaths,
    slots: list[FigureSlot],
) -> list[str]:
    """§quality phase 9 — return one warning per contribution claim that
    has no figure binding and no explicit no_visual_reason. Returns empty
    list when paper_plan is missing (then there's nothing to bind)."""
    warnings: list[str] = []
    if not paths.paper_plan_yaml.is_file():
        return warnings
    raw = load_yaml(paths.paper_plan_yaml, default={}) or {}
    if not isinstance(raw, dict):
        return warnings
    contribs = raw.get("contributions") or []
    if not contribs:
        return warnings

    # If a paper plan also lists figure_plan items with explicit
    # no_visual_reason, those contributions are "intentionally no-figure".
    plan_no_visual: dict[str, str] = {}
    for fp in (raw.get("figure_plan") or []):
        if not isinstance(fp, dict):
            continue
        if fp.get("no_visual_reason"):
            for cl in fp.get("supporting_claims") or []:
                plan_no_visual[cl] = fp.get("no_visual_reason", "")

    # Build {contribution_id -> claim_ids}: scan claims.yaml.
    claims_by_contrib: dict[str, list[str]] = {}
    if paths.claims_yaml.is_file():
        claims_raw = load_yaml(paths.claims_yaml, default={}) or {}
        if isinstance(claims_raw, dict):
            for c in (claims_raw.get("claims") or []):
                if not isinstance(c, dict):
                    continue
                cid = c.get("contribution_id")
                if cid:
                    claims_by_contrib.setdefault(cid, []).append(c.get("id", ""))

    # Set of claim_ids that any slot supports (or that have no_visual_reason).
    supported: set[str] = set()
    for slot in slots:
        if slot.no_visual_reason:
            for cl in slot.supporting_claims:
                supported.add(cl)
        else:
            for cl in slot.supporting_claims:
                supported.add(cl)
    supported.update(plan_no_visual.keys())

    for c in contribs:
        if not isinstance(c, dict):
            continue
        contrib_id = c.get("id")
        title = c.get("title", "?")
        if not contrib_id:
            continue
        # The contribution is "covered" if any of its claims (or the
        # contribution id itself, when claim ledger is empty) is in supported.
        ledger_ids = claims_by_contrib.get(contrib_id, [])
        coverage_keys = set(ledger_ids) | {contrib_id}
        if coverage_keys & supported:
            continue
        warnings.append(
            f"contribution_uncovered: contribution '{contrib_id}' ({title}) has no "
            "figure / table / algorithm binding and no explicit no_visual_reason. "
            "Add a slot with supporting_claims=['{cid}'] or a paper_plan figure entry "
            "with no_visual_reason set.".format(cid=contrib_id)
        )
    return warnings

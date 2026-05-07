"""Multi-agent review graph (4 personas, configurable rounds).

```
START
  │
  ▼
retrieve_context        # build a small related-work pack from library/summaries
  │
  ▼
init_round              # round += 1, persona_queue = [...]
  │
  ▼
persona_critic ◀────────┐
  │ (queue非空)         │  (advance to next persona)
  └─────────────────────┘
  │ (queue空)
  ▼
moderator_synthesize    # LLM aggregates this round's critiques
  │
  ▼
await_user (interrupt)  # author may rebut / patch / skip
  │
  ▼
apply_rebuttal          # records rebuttal; optionally patches experiment
  │
  ▼
decide_next_round
  ├── continue ──────────► init_round
  └── terminate
        │
        ▼
      verdict           # final accept / minor / major / reject
        │
        ▼
       END
```

State is a TypedDict; persisted to ``checkpoints.sqlite`` per-run by LangGraph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from paic.llm.client import LLMClient
from paic.llm.host import llm_or_interrupt
from paic.llm.prompts import load_prompt
from paic.llm.router import LLMRouter
from paic.personas import PERSONA_NAMES, load_persona
from paic.schemas.review import (
    Critique,
    PersonaName,
    Rebuttal,
    ReviewVerdict,
)
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, save_yaml


# --- Schemas the LLM is asked to produce ------------------------------------

class _PersonaCritique(BaseModel):
    severity: str
    category: str
    quote: str | None = None
    issue: str
    suggestion: str
    cited_papers: list[str] = Field(default_factory=list)


class _PersonaCritiqueOutput(BaseModel):
    critiques: list[_PersonaCritique]


class _ModeratorIssue(BaseModel):
    rank: int
    severity: str
    title: str
    description: str
    raised_by: list[str] = Field(default_factory=list)
    suggested_resolution: str


class _ModeratorOutput(BaseModel):
    issues: list[_ModeratorIssue]
    open_questions_for_author: list[str] = Field(default_factory=list)
    panel_summary: str


class _VerdictOutput(BaseModel):
    decision: str
    rationale: str
    must_fix: list[str] = Field(default_factory=list)
    nice_to_fix: list[str] = Field(default_factory=list)


# --- State ----------------------------------------------------------------

class ReviewState(TypedDict, total=False):
    project_dir: str
    experiment_id: str
    experiment: dict[str, Any]
    related_papers: list[dict[str, Any]]
    personas: list[str]                          # configurable subset of PERSONA_NAMES
    round: int
    max_rounds: int
    persona_queue: list[str]
    critiques_by_round: list[dict[str, list[dict[str, Any]]]]
    moderator_notes: list[dict[str, Any]]        # one entry per round
    rebuttals: list[dict[str, Any]]
    last_user_intervention: dict[str, Any] | None
    verdict: dict[str, Any] | None
    run_id: str


@dataclass
class ReviewDeps:
    llm: LLMClient
    paths: ProjectPaths
    router: LLMRouter | None = None
    """When set and a node routes to ``host``, the LLM call is replaced by a
    LangGraph ``interrupt(...)`` carrying a ``HostOrchestrationDirective``.
    The Skill resumes via ``paic_review_step(host_response=...)``."""


# --- Helpers --------------------------------------------------------------

def _format_experiment(exp: dict[str, Any]) -> str:
    parts = [
        f"### EXPERIMENT PROPOSAL (id: {exp.get('id')})",
        f"Idea id: {exp.get('idea_id')}",
        "",
        "Research questions:",
        *(f"- {q}" for q in exp.get("research_questions", []) or []),
        "",
        "Hypotheses:",
        *(f"- {h}" for h in exp.get("hypotheses", []) or []),
        "",
        "Proposed method:",
        exp.get("proposed_method", ""),
        "",
        "Datasets:",
        *(f"- {d.get('name')}: {d.get('rationale')}" for d in exp.get("datasets", []) or []),
        "",
        "Baselines:",
        *(
            f"- {b.get('name')} (paper: {b.get('paper_ref') or 'n/a'}): {b.get('why')}"
            for b in exp.get("baselines", []) or []
        ),
        "",
        "Metrics:",
        *(
            f"- {m.get('name')} ({m.get('direction')}, primary={m.get('primary')})"
            for m in exp.get("metrics", []) or []
        ),
        "",
        "Ablations:",
        *(
            f"- {a.get('factor')}: levels={a.get('levels')} — {a.get('purpose')}"
            for a in exp.get("ablations", []) or []
        ),
        "",
        f"Compute budget: {exp.get('compute_budget')}",
        f"Timeline (weeks): {exp.get('timeline_weeks')}",
        "",
        "Success criteria:",
        *(f"- {c}" for c in exp.get("success_criteria", []) or []),
        "",
        "Threats to validity:",
        *(f"- {t}" for t in exp.get("threats_to_validity", []) or []),
    ]
    return "\n".join(parts)


def _format_related_papers(related: list[dict[str, Any]]) -> str:
    if not related:
        return "(no related-paper summaries available)"
    lines = []
    for s in related:
        pid = s.get("_paper_id", "?")
        title = (s.get("paper") or {}).get("title", "?")
        lines.append(f"### {pid} — {title}")
        lines.append(f"Problem: {s.get('problem', '').strip()}")
        lines.append(f"Method: {s.get('method', '').strip()}")
        lines.append(
            f"Limitations: {'; '.join(s.get('limitations', []) or []) or '(none listed)'}"
        )
        lines.append("")
    return "\n".join(lines)


def _format_round_history(state: ReviewState) -> str:
    """A compressed transcript of prior rounds for the moderator/verdict to read."""
    history = state.get("critiques_by_round", [])
    if not history:
        return "(this is round 1 — no prior history)"
    out = []
    for i, round_map in enumerate(history, start=1):
        out.append(f"== Round {i} ==")
        for persona, items in round_map.items():
            out.append(f"  [{persona}]")
            for c in items:
                out.append(
                    f"    - {c.get('severity')}/{c.get('category')}: {c.get('issue')}"
                )
    notes = state.get("moderator_notes") or []
    if notes:
        out.append("")
        out.append("== Moderator panel summaries ==")
        for i, n in enumerate(notes, start=1):
            out.append(f"  Round {i}: {n.get('panel_summary')}")
    rebuttals = state.get("rebuttals") or []
    if rebuttals:
        out.append("")
        out.append("== Author rebuttals ==")
        for r in rebuttals:
            out.append(f"  Round {r.get('round')}: {r.get('text')}")
    return "\n".join(out)


def _format_previous_round_context(state: ReviewState) -> str:
    """For round ≥ 2, surface prior moderator summary + author rebuttal text
    so personas (and the moderator) don't "forget" what was discussed last
    round. Returns ``""`` on round 1 — no prior history exists.

    This is the §quality "cross-round visibility" minimum patch: prompts
    are short (panel summary + top issues + rebuttal), token cost is low,
    and personas can now react to what the author actually argued instead
    of seeing only the patched experiment yaml.
    """
    round_num = state.get("round", 1)
    if round_num <= 1:
        return ""

    parts: list[str] = []

    notes = state.get("moderator_notes") or []
    if notes:
        last_note = notes[-1]
        panel_summary = (last_note.get("panel_summary") or "").strip()
        if panel_summary:
            parts.append(
                "### PREVIOUS ROUND PANEL SUMMARY\n\n" + panel_summary
            )
        issues = last_note.get("issues") or []
        if issues:
            issue_lines = []
            for it in issues[:8]:
                issue_lines.append(
                    f"- [{it.get('severity', '?')}|{it.get('category', '?')}] "
                    f"{it.get('issue', '')}"
                )
            parts.append(
                "### PREVIOUS ROUND TOP ISSUES\n\n" + "\n".join(issue_lines)
            )

    rebuttals = state.get("rebuttals") or []
    if rebuttals:
        last_reb = rebuttals[-1]
        text = (last_reb.get("text") or "").strip()
        if text:
            parts.append(
                "### PREVIOUS ROUND AUTHOR REBUTTAL\n\n" + text
            )

    if not parts:
        return ""

    intro = (
        "Use the context below from the previous round to focus your critique:\n"
        "- Address residual issues the author has not adequately answered.\n"
        "- Avoid re-litigating points the author already conceded or fixed.\n"
        "- If the rebuttal raises new methodological doubts, surface them.\n\n"
    )
    return intro + "\n\n".join(parts) + "\n\n"


# --- Nodes ----------------------------------------------------------------

def _retrieve_context(state: ReviewState, deps: ReviewDeps) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    for yaml_path in sorted(deps.paths.summaries_dir.glob("*.yaml")):
        record = load_yaml(yaml_path) or {}
        if not record:
            continue
        record["_paper_id"] = (
            (record.get("paper") or {}).get("arxiv_id")
            or (record.get("paper") or {}).get("doi")
            or yaml_path.stem
        )
        summaries.append(record)
    # MVP: cap at 10. A richer retrieval (TF-IDF or semantic) can replace this later.
    summaries = summaries[:10]

    # Load the experiment if not already loaded into state.
    exp = state.get("experiment")
    if not exp:
        exp_path = deps.paths.experiments_dir / f"{state['experiment_id']}.yaml"
        if not exp_path.exists():
            raise FileNotFoundError(f"Experiment not found: {state['experiment_id']}")
        exp = load_yaml(exp_path) or {}

    return {"experiment": exp, "related_papers": summaries}


def _init_round(state: ReviewState, deps: ReviewDeps) -> dict[str, Any]:
    next_round = state.get("round", 0) + 1
    personas = state.get("personas") or list(PERSONA_NAMES)
    history = state.get("critiques_by_round", [])
    history.append({})  # empty bucket for the new round
    return {
        "round": next_round,
        "persona_queue": list(personas),
        "critiques_by_round": history,
    }


def _persona_critic(state: ReviewState, deps: ReviewDeps) -> dict[str, Any]:
    queue = list(state.get("persona_queue") or [])
    if not queue:
        return {}
    persona = queue.pop(0)
    history = list(state.get("critiques_by_round") or [{}])
    current_round = history[-1]

    user_msg = (
        _format_previous_round_context(state)
        + _format_experiment(state["experiment"])
        + "\n\n### RELATED PAPERS\n\n"
        + _format_related_papers(state.get("related_papers", []))
    )
    output = llm_or_interrupt(
        deps,
        node=f"review_persona_{persona}",
        system=load_persona(persona),
        user=user_msg,
        schema=_PersonaCritiqueOutput,
        max_tokens=2048,
        temperature=0.3,
        run_id=state.get("run_id"),
        resume_tool="mcp__paic__paic_review_step",
        extra_metadata={"persona": persona, "round": state.get("round")},
    )
    current_round[persona] = [c.model_dump() for c in output.critiques]
    history[-1] = current_round
    return {"persona_queue": queue, "critiques_by_round": history}


def _route_after_persona_critic(state: ReviewState) -> str:
    return "persona_critic" if state.get("persona_queue") else "moderator_synthesize"


def _moderator_synthesize(state: ReviewState, deps: ReviewDeps) -> dict[str, Any]:
    history = state.get("critiques_by_round", [])
    current_round = history[-1] if history else {}
    critiques_block = []
    for persona, items in current_round.items():
        critiques_block.append(f"## {persona}")
        for c in items:
            critiques_block.append(
                f"- [{c.get('severity')}|{c.get('category')}] {c.get('issue')} "
                f"=> suggestion: {c.get('suggestion')}"
            )
    user_msg = (
        _format_previous_round_context(state)
        + _format_experiment(state["experiment"])
        + "\n\n### THIS ROUND'S CRITIQUES\n\n"
        + "\n".join(critiques_block)
    )
    output = llm_or_interrupt(
        deps,
        node="review_moderator",
        system=load_prompt("review_moderator"),
        user=user_msg,
        schema=_ModeratorOutput,
        max_tokens=2048,
        temperature=0.2,
        run_id=state.get("run_id"),
        resume_tool="mcp__paic__paic_review_step",
        extra_metadata={"round": state.get("round")},
    )
    notes = list(state.get("moderator_notes") or [])
    notes.append(output.model_dump())
    return {"moderator_notes": notes}


def _await_user(state: ReviewState, deps: ReviewDeps) -> dict[str, Any]:
    """Pause for author's decision before the next round.

    Resume payload (Command(resume=...)):
        {
          "rebuttal": "<text>",        # optional — author response
          "plan_diff": "<patched experiment yaml as a string>",   # optional
          "skip_to_verdict": true|false  # optional — terminate after this round
        }
    """
    notes = state.get("moderator_notes") or []
    last_note = notes[-1] if notes else {}
    payload = interrupt(
        {
            "stage": "await_user",
            "round": state.get("round"),
            "max_rounds": state.get("max_rounds"),
            "panel_summary": last_note.get("panel_summary"),
            "issues": last_note.get("issues", []),
            "open_questions_for_author": last_note.get("open_questions_for_author", []),
        }
    )
    return {"last_user_intervention": payload if isinstance(payload, dict) else {}}


def _apply_rebuttal(state: ReviewState, deps: ReviewDeps) -> dict[str, Any]:
    intervention = state.get("last_user_intervention") or {}
    out: dict[str, Any] = {}

    text = (intervention.get("rebuttal") or "").strip()
    if text:
        rebuttal = Rebuttal(
            persona="author",
            round=state.get("round", 1),
            text=text,
            plan_diff=intervention.get("plan_diff"),
        )
        rebuttals = list(state.get("rebuttals") or [])
        rebuttals.append(rebuttal.model_dump())
        out["rebuttals"] = rebuttals

    # If the author supplied a fully-patched experiment yaml, swap state["experiment"].
    plan_diff_yaml = intervention.get("plan_diff")
    if plan_diff_yaml and isinstance(plan_diff_yaml, str):
        try:
            import yaml

            patched = yaml.safe_load(plan_diff_yaml)
            if isinstance(patched, dict):
                out["experiment"] = patched
        except Exception:
            # silently ignore — better to keep the existing experiment than crash
            pass

    return out


def _decide_next_round(state: ReviewState) -> str:
    intervention = state.get("last_user_intervention") or {}
    if intervention.get("skip_to_verdict"):
        return "verdict"
    if state.get("round", 0) >= state.get("max_rounds", 2):
        return "verdict"
    return "init_round"


def _verdict(state: ReviewState, deps: ReviewDeps) -> dict[str, Any]:
    user_msg = (
        _format_experiment(state["experiment"])
        + "\n\n### REVIEW HISTORY\n\n"
        + _format_round_history(state)
    )
    output = llm_or_interrupt(
        deps,
        node="review_verdict",
        system=load_prompt("review_verdict"),
        user=user_msg,
        schema=_VerdictOutput,
        max_tokens=2048,
        temperature=0.1,
        run_id=state.get("run_id"),
        resume_tool="mcp__paic__paic_review_step",
    )
    verdict = ReviewVerdict.model_validate(output.model_dump())

    # Persist the full transcript to disk under reviews/<experiment_id>/
    out_dir = deps.paths.reviews_dir / state["experiment_id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    transcript = _build_transcript_yaml(state, verdict)
    save_yaml(out_dir / "transcript.yaml", transcript)
    save_yaml(out_dir / "verdict.yaml", verdict.model_dump(mode="json"))
    _write_human_readable_rounds(state, out_dir)

    return {"verdict": verdict.model_dump(mode="json")}


def _build_transcript_yaml(state: ReviewState, verdict: ReviewVerdict) -> dict[str, Any]:
    flat_critiques: list[dict[str, Any]] = []
    for round_idx, round_map in enumerate(state.get("critiques_by_round", []), start=1):
        for persona, items in round_map.items():
            for c in items:
                flat_critiques.append(
                    Critique(
                        persona=persona,  # type: ignore[arg-type]
                        round=round_idx,
                        severity=c.get("severity", "minor"),  # type: ignore[arg-type]
                        category=c.get("category", "general"),
                        quote=c.get("quote"),
                        issue=c.get("issue", ""),
                        suggestion=c.get("suggestion", ""),
                        cited_papers=c.get("cited_papers") or [],
                        created_at=datetime.now(UTC),
                    ).model_dump(mode="json")
                )
    return {
        "run_id": state.get("run_id"),
        "experiment_id": state.get("experiment_id"),
        "rounds_completed": state.get("round", 0),
        "critiques": flat_critiques,
        "rebuttals": state.get("rebuttals") or [],
        "moderator_notes": state.get("moderator_notes") or [],
        "verdict": verdict.model_dump(mode="json"),
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
    }


def _write_human_readable_rounds(state: ReviewState, out_dir) -> None:
    notes = state.get("moderator_notes") or []
    for i, round_map in enumerate(state.get("critiques_by_round", []), start=1):
        lines = [f"# Review — Round {i}", ""]
        for persona, items in round_map.items():
            lines.append(f"## {persona}")
            lines.append("")
            for c in items:
                lines.append(
                    f"- **{c.get('severity', 'minor').upper()}** / {c.get('category')}: "
                    f"{c.get('issue')}\n  - **Suggest:** {c.get('suggestion')}"
                )
            lines.append("")
        if i - 1 < len(notes):
            n = notes[i - 1]
            lines.append("## Moderator synthesis")
            lines.append("")
            lines.append(n.get("panel_summary", ""))
            lines.append("")
            for issue in n.get("issues", []) or []:
                lines.append(
                    f"- **#{issue.get('rank')} {issue.get('severity', '').upper()}**: "
                    f"{issue.get('title')} — {issue.get('description')}"
                )
            lines.append("")
        (out_dir / f"round_{i}.md").write_text("\n".join(lines), encoding="utf-8")


# --- Builder --------------------------------------------------------------

def build_review_graph(deps: ReviewDeps):
    from paic.graphs.checkpointer import get_checkpointer

    g = StateGraph(ReviewState)
    g.add_node("retrieve_context", lambda s: _retrieve_context(s, deps))
    g.add_node("init_round", lambda s: _init_round(s, deps))
    g.add_node("persona_critic", lambda s: _persona_critic(s, deps))
    g.add_node("moderator_synthesize", lambda s: _moderator_synthesize(s, deps))
    g.add_node("await_user", lambda s: _await_user(s, deps))
    g.add_node("apply_rebuttal", lambda s: _apply_rebuttal(s, deps))
    g.add_node("verdict", lambda s: _verdict(s, deps))

    g.add_edge(START, "retrieve_context")
    g.add_edge("retrieve_context", "init_round")
    g.add_edge("init_round", "persona_critic")
    g.add_conditional_edges(
        "persona_critic",
        _route_after_persona_critic,
        {"persona_critic": "persona_critic", "moderator_synthesize": "moderator_synthesize"},
    )
    g.add_edge("moderator_synthesize", "await_user")
    g.add_edge("await_user", "apply_rebuttal")
    g.add_conditional_edges(
        "apply_rebuttal",
        _decide_next_round,
        {"init_round": "init_round", "verdict": "verdict"},
    )
    g.add_edge("verdict", END)

    return g.compile(checkpointer=get_checkpointer(deps.paths))

"""Ideate graph — §26 v2 (multi-round refine + 4-persona panel scoring).

```
START
  ↓
gather_corpus
  ↓
brainstorm ──────────┐  ← prompt mode = (round 1) | regenerate | refine
  ↓                   │
score_panel           │  ← 4-persona evaluation; memoizes by content hash
  ↓                   │
await_user_decision   │  ← interrupt; resume payload selects next action
  │                   │
  ├─ regenerate / refine AND round < max_rounds ──┘
  │
  └─ finalize ─→ finalize → END
```

Every node entry first runs ``_normalize_state`` so v1 SQLite checkpoints
(saved before §26) survive resume into the v2 graph. New v1 → v2 mappings:
``keep_indices`` → ``last_keep_indices``; ``user_feedback`` → ``last_feedback``.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean, stdev
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from ulid import ULID

from paic.llm.client import LLMClient
from paic.llm.prompts import load_prompt
from paic.schemas.idea import IdeaCard
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml, save_yaml

DEFAULT_PERSONAS: tuple[str, ...] = ("methodology", "novelty", "impact", "reviewer2")
DEFAULT_MAX_ROUNDS = 3

# §26.6.2 — hardcoded per-persona temperatures so even when all 4 personas
# resolve to the same backend, sampling distributions diverge. Don't expose
# in config: users who want different temps should add a persona, not retune
# existing ones.
PERSONA_TEMPERATURES: dict[str, float] = {
    "methodology": 0.1,    # strict, narrow
    "novelty": 0.5,         # exploratory
    "impact": 0.3,          # balanced
    "reviewer2": 0.2,       # consistent harshness
}

# §26.5.1 token budget — prompt context capped at this many estimated tokens
# (chars ÷ 4 heuristic). Above this, oldest history entry is dropped until
# we fit. Conservative — leaves ~120K headroom even on Opus 200K context.
PROMPT_TOKEN_BUDGET = 8000

# §26.6.1 panel scoring tokens — output is short (rationale ≤2 sent + 0-3 flags),
# so cap aggressively to cut output cost ~50%.
PANEL_MAX_TOKENS = 1024


# --- LLM-facing schemas ---------------------------------------------------


class _IdeaDraft(BaseModel):
    title: str
    one_liner: str = Field(..., max_length=240)
    motivation: str
    proposed_approach: str
    novelty_claim: str
    expected_contribution: str
    grounded_in: list[str] = Field(default_factory=list)
    contrasts_with: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)


class _BrainstormOutput(BaseModel):
    ideas: list[_IdeaDraft]


class _PersonaIdeaScore(BaseModel):
    idx: int
    feasibility: float = Field(0.0, ge=0.0, le=1.0)
    novelty: float = Field(0.0, ge=0.0, le=1.0)
    impact: float = Field(0.0, ge=0.0, le=1.0)
    rationale: str = ""
    red_flags: list[str] = Field(default_factory=list)


class _PanelScoreOutput(BaseModel):
    scores: list[_PersonaIdeaScore]


# --- State ---------------------------------------------------------------


class IdeateState(TypedDict, total=False):
    # Inputs
    project_dir: str
    focus: str | None
    n_candidates: int
    paper_ids: list[str] | None
    max_rounds: int
    personas: list[str]

    # Cumulative
    corpus: list[dict[str, Any]]
    round: int
    history: list[dict[str, Any]]
    user_feedback_chain: list[str]

    # Score memoization (§26.6.1)
    score_cache: dict[str, dict[str, Any]]   # content_hash → aggregated panel_score

    # Current round (overwritten each round)
    drafts: list[dict[str, Any]]
    panel_scores: list[dict[str, Any]]

    # User decision (last interrupt)
    last_action: str | None
    last_feedback: str | None
    last_keep_indices: list[int]

    # Output
    finalized_ids: list[str]
    run_id: str

    # v1 legacy fields (preserved during _normalize_state for checkpoint compat)
    keep_indices: list[int]
    user_feedback: str | None


@dataclass
class IdeateDeps:
    llm: LLMClient
    paths: ProjectPaths


# --- v1 → v2 state adapter (§26.10 R92) ----------------------------------


def _normalize_state(state: IdeateState) -> IdeateState:
    """Fill v2 defaults onto a state that may be a v1 checkpoint.

    Idempotent. Maps legacy keys (keep_indices / user_feedback) into their
    v2 equivalents (last_keep_indices / last_feedback) so paused v1 runs
    resume cleanly.
    """
    out: IdeateState = dict(state)  # type: ignore[assignment]
    out.setdefault("round", 1)
    out.setdefault("max_rounds", DEFAULT_MAX_ROUNDS)
    out.setdefault("history", [])
    out.setdefault("user_feedback_chain", [])
    out.setdefault("score_cache", {})
    out.setdefault("personas", list(DEFAULT_PERSONAS))
    out.setdefault("panel_scores", [])
    if "last_keep_indices" not in out:
        out["last_keep_indices"] = list(state.get("keep_indices") or [])
    if "last_feedback" not in out:
        out["last_feedback"] = state.get("user_feedback")
    out.setdefault("last_action", None)
    return out


# --- Helpers --------------------------------------------------------------


def _content_hash(draft: dict[str, Any]) -> str:
    """sha256(title + one_liner + proposed_approach), first 16 hex.

    Stable across rounds for the same draft text — keys the score_cache so
    refine flows don't re-score unchanged seeds.
    """
    payload = "\n".join([
        str(draft.get("title", "")),
        str(draft.get("one_liner", "")),
        str(draft.get("proposed_approach", "")),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _estimate_tokens(text: str) -> int:
    """4-chars-per-token heuristic. Stable enough for budget enforcement."""
    return len(text) // 4


def _format_corpus_for_llm(corpus: list[dict[str, Any]]) -> str:
    if not corpus:
        return "(no summaries available — the library is empty)"
    parts: list[str] = []
    for s in corpus:
        paper = s.get("paper") or {}
        pid = s.get("_paper_id", "?")
        title = paper.get("title", "?")
        parts.append(
            f"### {pid} — {title}\n"
            f"- Problem: {s.get('problem', '').strip()}\n"
            f"- Method: {s.get('method', '').strip()}\n"
            f"- Key results: {'; '.join(s.get('key_results', []) or [])}\n"
            f"- Limitations: {'; '.join(s.get('limitations', []) or [])}\n"
            f"- Techniques: {', '.join(s.get('techniques', []) or [])}\n"
        )
    return "\n".join(parts)


def _compact_history(history: list[dict[str, Any]]) -> str:
    """JSON-serialized compact history (§26.5.1 R89).

    Keeps only the last 2 rounds with top-3 drafts each; older rounds → 1-line.
    Returns empty string when there's no history (round 1).
    """
    if not history:
        return ""
    blocks: list[dict[str, Any]] = []
    if len(history) > 2:
        earlier = [{"round": e["round"], "n_drafts": e.get("n_drafts", 0)} for e in history[:-2]]
        blocks.append({"earlier_rounds": earlier})
    for entry in history[-2:]:
        scores = entry.get("scores") or []
        drafts = entry.get("drafts") or []
        pairs = sorted(zip(drafts, scores), key=lambda x: -x[1])[:3]
        blocks.append({
            "round": entry["round"],
            "kept_count": len(entry.get("kept", []) or []),
            "feedback": entry.get("feedback") or "(none)",
            "top_drafts": [{"title": t, "composite": s} for t, s in pairs],
        })
    return json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))


def _format_seed_drafts(drafts: list[dict[str, Any]], indices: list[int]) -> str:
    """Refine-mode seed payload — title + one_liner + 三维分 only (§26.5.1 (d))."""
    if not indices:
        return ""
    seeds: list[dict[str, Any]] = []
    for i in indices:
        if 0 <= i < len(drafts):
            d = drafts[i]
            seeds.append({
                "seed_idx": i,
                "title": d.get("title"),
                "one_liner": d.get("one_liner"),
                "feasibility": d.get("feasibility_score"),
                "novelty": d.get("novelty_score"),
                "impact": d.get("impact_score"),
            })
    return json.dumps(seeds, ensure_ascii=False, separators=(",", ":"))


def _build_brainstorm_user_msg(state: IdeateState) -> str:
    """Compose the user message for brainstorm, mode-dependent + token-bounded."""
    n = state.get("n_candidates", 8)
    focus = state.get("focus") or "(no specific focus — let the user pick)"
    corpus_text = _format_corpus_for_llm(state.get("corpus", []))
    last_action = state.get("last_action")
    feedback_chain = state.get("user_feedback_chain", [])
    feedback_compact = feedback_chain[-3:]   # keep last 3 verbatim
    history = state.get("history", [])

    parts = [f"FOCUS: {focus}", f"N: {n}", "", "CORPUS SUMMARIES:", corpus_text]

    if last_action == "regenerate":
        parts += [
            "",
            "MODE: regenerate (this is round " + str(state.get("round", 1)) + ")",
            "Generate N **new, distinct** candidates. Avoid the directions present "
            "in PREVIOUS_ROUNDS_TOP_DRAFTS below.",
            "",
            "PREVIOUS_ROUNDS_TOP_DRAFTS:",
            _compact_history(history),
            "",
            "USER_FEEDBACK_RECENT (apply these constraints):",
            json.dumps(feedback_compact, ensure_ascii=False),
        ]
    elif last_action == "refine":
        seeds_json = _format_seed_drafts(
            state.get("drafts", []),
            state.get("last_keep_indices") or [],
        )
        parts += [
            "",
            "MODE: refine (this is round " + str(state.get("round", 1)) + ")",
            "Preserve the **core thesis** of each SEED_DRAFT and produce a refined / "
            "sharpened version. Then generate enough new candidates to reach N total. "
            "Don't change a seed's central claim; expand specifics / fix the red_flags "
            "given.",
            "",
            "SEED_DRAFTS (refine these):",
            seeds_json,
            "",
            "PREVIOUS_ROUNDS_TOP_DRAFTS (context):",
            _compact_history(history),
            "",
            "USER_FEEDBACK_RECENT:",
            json.dumps(feedback_compact, ensure_ascii=False),
        ]
    # else round 1 — base prompt is enough

    msg = "\n".join(parts)

    # §26.5.1 (c) token budget enforcement — drop oldest history until fit
    while _estimate_tokens(msg) > PROMPT_TOKEN_BUDGET and len(history) > 1:
        history = history[1:]
        # rebuild only the history-dependent parts
        compact = _compact_history(history)
        msg = msg.replace(_compact_history(state.get("history", [])), compact, 1)

    return msg


# --- Nodes ----------------------------------------------------------------


def _gather_corpus(state: IdeateState, deps: IdeateDeps) -> dict[str, Any]:
    state = _normalize_state(state)
    summaries: list[dict[str, Any]] = []
    summaries_dir = deps.paths.summaries_dir
    target_ids = state.get("paper_ids")
    for yaml_path in sorted(summaries_dir.glob("*.yaml")):
        record = load_yaml(yaml_path) or {}
        if not record:
            continue
        paper_id = (
            record.get("paper", {}).get("arxiv_id")
            or record.get("paper", {}).get("doi")
            or yaml_path.stem
        )
        if target_ids and paper_id not in target_ids:
            continue
        record["_paper_id"] = paper_id
        summaries.append(record)
    return {"corpus": summaries}


def _brainstorm(state: IdeateState, deps: IdeateDeps) -> dict[str, Any]:
    state = _normalize_state(state)
    user_msg = _build_brainstorm_user_msg(state)
    output = deps.llm.complete_json(
        system=load_prompt("ideate_brainstorm"),
        user=user_msg,
        schema=_BrainstormOutput,
        max_tokens=4096,
        temperature=0.6,
        node="ideate_brainstorm",
    )
    drafts = [d.model_dump() for d in output.ideas]
    # No scoring here — score_panel does it. Bump round counter.
    return {
        "drafts": drafts,
        "round": state.get("round", 0) + 1,
    }


def _looks_serial_only(llm: LLMClient, persona: str) -> bool:
    """Detect whether the persona's resolved backend is single-threaded
    (e.g. ``claude_agent_sdk`` spawns a CLI child — concurrent invocations
    duplicate processes and lose more time than they save).

    Defensive: if the router can't resolve the backend, fall back to serial.
    """
    try:
        backend = llm._backend_for(f"idea_score_{persona}")  # noqa: SLF001
    except Exception:
        return True
    name = getattr(backend, "name", "")
    return "claude_agent_sdk" in name


def _aggregate_breakdown(breakdown: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Turn per-persona scores into one panel_score entry.

    breakdown: {persona: {feasibility, novelty, impact, rationale, red_flags}}
    """
    if not breakdown:
        return {
            "persona_breakdown": {},
            "feasibility_avg": 0.0, "feasibility_std": 0.0,
            "novelty_avg": 0.0, "novelty_std": 0.0,
            "impact_avg": 0.0, "impact_std": 0.0,
            "composite": 0.0,
            "panel_consensus": None,
            "red_flags": [],
        }

    out: dict[str, Any] = {"persona_breakdown": breakdown}
    max_std = 0.0
    for dim in ("feasibility", "novelty", "impact"):
        vals = [b[dim] for b in breakdown.values()]
        avg = mean(vals)
        std = stdev(vals) if len(vals) >= 2 else 0.0
        out[f"{dim}_avg"] = avg
        out[f"{dim}_std"] = std
        max_std = max(max_std, std)
    out["composite"] = round(
        0.4 * out["feasibility_avg"]
        + 0.35 * out["novelty_avg"]
        + 0.25 * out["impact_avg"],
        4,
    )
    if max_std < 0.08:
        out["panel_consensus"] = "high_agreement"
    elif max_std < 0.18:
        out["panel_consensus"] = "moderate"
    else:
        out["panel_consensus"] = "diverged"
    red_flags: set[str] = set()
    for b in breakdown.values():
        for f in b.get("red_flags") or []:
            if f and isinstance(f, str):
                red_flags.add(f.strip())
    out["red_flags"] = sorted(red_flags)
    return out


def _format_drafts_for_scoring(drafts: list[dict[str, Any]], indices: list[int]) -> str:
    """User message for panel scoring — only the drafts at given indices.

    `idx` keeps the absolute indices from the full drafts list so the LLM's
    output preserves global indexing.
    """
    parts: list[str] = ["IDEAS_TO_SCORE:"]
    for i in indices:
        d = drafts[i]
        parts.append(
            f"### idx={i}\n"
            f"- title: {d.get('title')}\n"
            f"- one_liner: {d.get('one_liner')}\n"
            f"- motivation: {d.get('motivation')}\n"
            f"- proposed_approach: {d.get('proposed_approach')}\n"
            f"- novelty_claim: {d.get('novelty_claim')}\n"
            f"- expected_contribution: {d.get('expected_contribution')}\n"
            f"- grounded_in: {', '.join(d.get('grounded_in') or [])}\n"
        )
    parts.append(
        "\nReturn one entry per idea (idx unchanged). Output schema: "
        "{scores: [{idx, feasibility, novelty, impact, rationale, red_flags}]}"
    )
    return "\n".join(parts)


def _call_persona(
    persona: str,
    drafts: list[dict[str, Any]],
    score_indices: list[int],
    llm: LLMClient,
) -> tuple[str, _PanelScoreOutput | Exception]:
    try:
        out = llm.complete_json(
            system=load_prompt(f"idea_score_{persona}"),
            user=_format_drafts_for_scoring(drafts, score_indices),
            schema=_PanelScoreOutput,
            max_tokens=PANEL_MAX_TOKENS,
            temperature=PERSONA_TEMPERATURES.get(persona, 0.2),
            node=f"idea_score_{persona}",
        )
        return (persona, out)
    except Exception as exc:  # noqa: BLE001
        return (persona, exc)


def _score_panel(state: IdeateState, deps: IdeateDeps) -> dict[str, Any]:
    state = _normalize_state(state)
    drafts = state["drafts"]
    personas = state.get("personas") or list(DEFAULT_PERSONAS)
    cache: dict[str, dict[str, Any]] = dict(state.get("score_cache") or {})

    panel_scores: list[dict[str, Any] | None] = [None] * len(drafts)
    cache_hits = 0
    score_indices: list[int] = []
    for i, d in enumerate(drafts):
        h = _content_hash(d)
        if h in cache:
            panel_scores[i] = dict(cache[h])
            cache_hits += 1
        else:
            score_indices.append(i)

    # If everything cached, skip LLM calls entirely.
    if score_indices:
        # Decide serial vs parallel based on backend
        any_serial = any(_looks_serial_only(deps.llm, p) for p in personas)
        results: dict[str, _PanelScoreOutput] = {}
        if any_serial or len(personas) <= 1:
            for persona in personas:
                _, res = _call_persona(persona, drafts, score_indices, deps.llm)
                if isinstance(res, Exception):
                    raise res
                results[persona] = res
        else:
            with ThreadPoolExecutor(max_workers=len(personas)) as ex:
                futures = {
                    ex.submit(_call_persona, p, drafts, score_indices, deps.llm): p
                    for p in personas
                }
                for fut in as_completed(futures):
                    persona, res = fut.result()
                    if isinstance(res, Exception):
                        # Cancel remaining futures and propagate
                        for f in futures:
                            f.cancel()
                        raise res
                    results[persona] = res

        # Build per-draft persona_breakdown for the un-cached indices
        breakdowns: dict[int, dict[str, dict[str, Any]]] = {i: {} for i in score_indices}
        for persona, output in results.items():
            for s in output.scores:
                if s.idx not in breakdowns:
                    continue
                breakdowns[s.idx][persona] = {
                    "feasibility": s.feasibility,
                    "novelty": s.novelty,
                    "impact": s.impact,
                    "rationale": s.rationale,
                    "red_flags": list(s.red_flags),
                }

        for i in score_indices:
            agg = _aggregate_breakdown(breakdowns.get(i, {}))
            panel_scores[i] = agg
            # Cache by content hash for next round's refine reuse
            cache[_content_hash(drafts[i])] = dict(agg)

    # Mirror aggregate scores into draft fields for backward compat with
    # callers that read draft["feasibility_score"] etc.
    for i, d in enumerate(drafts):
        ps = panel_scores[i] or _aggregate_breakdown({})
        d["feasibility_score"] = ps.get("feasibility_avg", 0.0)
        d["novelty_score"] = ps.get("novelty_avg", 0.0)
        d["impact_score"] = ps.get("impact_avg", 0.0)
        d["composite_score"] = ps.get("composite", 0.0)

    drafts.sort(key=lambda r: r["composite_score"], reverse=True)
    # After sort, re-fetch each draft's panel_score from the cache so the
    # returned panel_scores list aligns with the (re-ordered) drafts list.
    panel_aligned: list[dict[str, Any]] = []
    for d in drafts:
        h = _content_hash(d)
        panel_aligned.append(dict(cache.get(h) or _aggregate_breakdown({})))
    return {
        "drafts": drafts,
        "panel_scores": panel_aligned,
        "score_cache": cache,
        "_score_panel_cache_hits": cache_hits,
    }


def _await_user_decision(state: IdeateState, deps: IdeateDeps) -> dict[str, Any]:
    state = _normalize_state(state)
    drafts = state.get("drafts", [])
    panel_scores = state.get("panel_scores", [])
    round_n = state.get("round", 1)
    max_r = state.get("max_rounds", DEFAULT_MAX_ROUNDS)
    round_limit_reached = round_n >= max_r
    available_actions = ["finalize"] if round_limit_reached else ["regenerate", "refine", "finalize"]

    drafts_with_scores: list[dict[str, Any]] = []
    for i, d in enumerate(drafts):
        ps = panel_scores[i] if i < len(panel_scores) else _aggregate_breakdown({})
        drafts_with_scores.append({
            "idx": i,
            "title": d.get("title"),
            "one_liner": d.get("one_liner"),
            "feasibility": ps.get("feasibility_avg", 0.0),
            "novelty": ps.get("novelty_avg", 0.0),
            "impact": ps.get("impact_avg", 0.0),
            "composite": ps.get("composite", 0.0),
            "panel_consensus": ps.get("panel_consensus"),
            "red_flags": (ps.get("red_flags") or [])[:5],
            "persona_breakdown": ps.get("persona_breakdown") or {},
        })

    payload = interrupt({
        "stage": "await_user_decision",
        "round": round_n,
        "max_rounds": max_r,
        "round_limit_reached": round_limit_reached,
        "drafts_with_scores": drafts_with_scores,
        "history_summary": [
            {"round": h.get("round"), "n_drafts": h.get("n_drafts", len(h.get("drafts", [])))}
            for h in state.get("history", [])
        ],
        "available_actions": available_actions,
    })

    action = (payload.get("action") if isinstance(payload, dict) else None)
    feedback = payload.get("feedback") if isinstance(payload, dict) else None
    keep = payload.get("keep") if isinstance(payload, dict) else None

    # Force-finalize when round limit reached, regardless of user action
    if round_limit_reached and action != "finalize":
        action = "finalize"

    if action is None:
        # Defensive default — finalize all drafts (legacy v1 behavior shape)
        action = "finalize"

    if keep is None:
        keep = list(range(len(drafts))) if action == "finalize" else []

    history = list(state.get("history", []))
    history.append({
        "round": round_n,
        "n_drafts": len(drafts),
        "drafts": [d.get("title") for d in drafts],
        "scores": [ps.get("composite", 0.0) for ps in panel_scores],
        "kept": keep,
        "feedback": feedback,
    })

    feedback_chain = list(state.get("user_feedback_chain", []))
    if feedback:
        feedback_chain.append(feedback)

    return {
        "last_action": action,
        "last_feedback": feedback,
        "last_keep_indices": keep,
        "history": history,
        "user_feedback_chain": feedback_chain,
    }


def _route_after_decision(state: IdeateState) -> str:
    """Conditional edge: regenerate/refine loops back to brainstorm; else finalize."""
    state = _normalize_state(state)
    action = state.get("last_action")
    round_n = state.get("round", 1)
    max_r = state.get("max_rounds", DEFAULT_MAX_ROUNDS)
    if action == "finalize":
        return "finalize"
    if round_n >= max_r:
        return "finalize"
    if action in ("regenerate", "refine"):
        return "brainstorm"
    return "finalize"


def _finalize(state: IdeateState, deps: IdeateDeps) -> dict[str, Any]:
    state = _normalize_state(state)
    drafts = state.get("drafts", [])
    panel_scores = state.get("panel_scores", [])
    keep_indices = state.get("last_keep_indices") or list(range(len(drafts)))
    feedback_chain = state.get("user_feedback_chain", [])
    rounds_used = state.get("round", 1)

    finalized: list[str] = []
    ranking: list[dict[str, Any]] = []
    now = datetime.now(UTC)

    deps.paths.ideas_dir.mkdir(parents=True, exist_ok=True)
    for idx in keep_indices:
        if idx < 0 or idx >= len(drafts):
            continue
        draft = drafts[idx]
        ps = panel_scores[idx] if idx < len(panel_scores) else _aggregate_breakdown({})
        idea_id = str(ULID())
        # Strip per-persona scores from breakdown stored on card to avoid
        # serializing huge nested rationales into yaml; keep top-level dim avgs
        # and panel_consensus.
        panel_dump: dict[str, dict[str, Any]] = {}
        for persona, b in (ps.get("persona_breakdown") or {}).items():
            panel_dump[persona] = {
                "feasibility": b.get("feasibility"),
                "novelty": b.get("novelty"),
                "impact": b.get("impact"),
                "rationale": (b.get("rationale") or "")[:500],
                "red_flags": list(b.get("red_flags") or []),
            }
        card = IdeaCard(
            id=idea_id,
            title=draft["title"],
            one_liner=draft["one_liner"],
            motivation=draft["motivation"],
            proposed_approach=draft["proposed_approach"],
            novelty_claim=draft["novelty_claim"],
            expected_contribution=draft["expected_contribution"],
            grounded_in=draft.get("grounded_in", []),
            contrasts_with=draft.get("contrasts_with", []),
            risk_factors=draft.get("risk_factors", []),
            feasibility_score=draft.get("feasibility_score", 0.0),
            novelty_score=draft.get("novelty_score", 0.0),
            impact_score=draft.get("impact_score", 0.0),
            composite_score=draft.get("composite_score", 0.0),
            status="draft",
            created_at=now,
            parent_run_id=state.get("run_id"),
            schema_version=2,
            panel_scores=panel_dump,
            panel_consensus=ps.get("panel_consensus"),
            red_flags=list(ps.get("red_flags") or []),
            feedback_log=list(feedback_chain),
            rounds_used=rounds_used,
        )
        save_yaml(deps.paths.ideas_dir / f"{idea_id}.yaml", card.model_dump(mode="json"))
        finalized.append(idea_id)
        ranking.append({
            "idea_id": idea_id,
            "title": card.title,
            "composite_score": card.composite_score,
            "panel_consensus": card.panel_consensus,
        })

    ranking.sort(key=lambda r: r["composite_score"], reverse=True)
    save_yaml(
        deps.paths.ideas_ranking,
        {"updated_at": now.isoformat(), "ranking": ranking},
    )
    return {"finalized_ids": finalized}


# --- Graph builder --------------------------------------------------------


def build_ideate_graph(deps: IdeateDeps):
    from paic.graphs.checkpointer import get_checkpointer

    g = StateGraph(IdeateState)
    g.add_node("gather_corpus", lambda s: _gather_corpus(s, deps))
    g.add_node("brainstorm", lambda s: _brainstorm(s, deps))
    g.add_node("score_panel", lambda s: _score_panel(s, deps))
    g.add_node("await_user_decision", lambda s: _await_user_decision(s, deps))
    g.add_node("finalize", lambda s: _finalize(s, deps))

    g.add_edge(START, "gather_corpus")
    g.add_edge("gather_corpus", "brainstorm")
    g.add_edge("brainstorm", "score_panel")
    g.add_edge("score_panel", "await_user_decision")
    g.add_conditional_edges(
        "await_user_decision",
        _route_after_decision,
        {"brainstorm": "brainstorm", "finalize": "finalize"},
    )
    g.add_edge("finalize", END)

    checkpointer = get_checkpointer(deps.paths)
    return g.compile(checkpointer=checkpointer)

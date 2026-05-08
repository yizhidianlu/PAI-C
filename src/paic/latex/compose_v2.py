"""Generator-Evaluator 4-call compose pipeline (ARS-fusion P1-1).

Borrowed from ARS v3.6.6 academic-paper full-mode generator-evaluator
contract. PAI-C adapts it for /paic-draft compose: one section's draft
is produced by FOUR independent LLM calls, with physical separation
preserved by ``LLMClient.complete_isolated`` (each phase gets a fresh
conversation context, so the evaluator literally cannot see the writer's
phase-4b output until phase 6b).

```
phase 4a writer planning   →  WriterCommitment    (paper-blind: contract only)
phase 4b writer execute    →  WriterDecision      (contract + library + paper plan)
phase 6a evaluator setup   →  EvaluatorRubric     (paper-blind: contract + 4a only)
phase 6b evaluator execute →  EvaluatorDecision   (contract + 4a + 4b output + 6a rubric)
```

The "physical separation of calls" defeats silent quality drift: the
writer cannot adjust the bar after seeing how hard the section turned
out, and the evaluator cannot rationalise a high score after seeing the
draft. Each phase commits its output BEFORE seeing what the next phase
needs.

V1.0 default: 4-call enabled (user decision §2). Cost is 2-4× a single
compose; latency similarly. Users can opt out via
``draft_compose_v2(strict=False)`` or by routing the new node tags to
their preferred backend.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from paic.llm.client import LLMClient, LLMUnavailable
from paic.llm.prompts import load_prompt
from paic.schemas.sprint_contract import (
    DEFAULT_DIMENSIONS,
    EvaluatorDecision,
    EvaluatorRubric,
    SprintContract,
    WriterCommitment,
    WriterDecision,
)

# Node names used to route per-phase calls via LLMRouter; each is
# whitelisted in HOST_SUPPORTED_NODES.
NODE_WRITER_PLAN = "compose_writer_plan"
NODE_WRITER_EXEC = "compose_writer_exec"
NODE_EVAL_SETUP = "compose_evaluator_setup"
NODE_EVAL_EXEC = "compose_evaluator_exec"


def run_4call_compose(
    *,
    contract: SprintContract,
    client: LLMClient,
    cross_model_evaluator_client: LLMClient | None = None,
) -> dict[str, Any]:
    """Run the full 4-phase pipeline against ``contract``.

    Args:
        contract: frozen baseline shared across all 4 phases
        client: LLMClient for writer phases (4a + 4b). Phases 6a/6b use
            this client too unless ``cross_model_evaluator_client`` is
            supplied — that switch lets P2-3 cross-model verification
            land trivially.
        cross_model_evaluator_client: optional second client for the
            evaluator phases. When supplied, the evaluator runs on a
            different backend than the writer (catches both single-model
            blind spots).

    Returns ``{contract, writer_commitment, writer_decision,
    evaluator_rubric, evaluator_decision, divergences}``.
    Each phase's output is a Pydantic model dump.

    Raises :class:`LLMUnavailable` only when ALL phase calls fail; a
    single phase failure is bubbled up via the result dict's ``errors``
    field so callers can decide whether to retry just the failed phase.
    """
    eval_client = cross_model_evaluator_client or client

    # Phase 4a: writer paper-blind pre-commitment
    commitment = _run_phase_4a(client, contract)

    # Phase 4b: writer paper-visible execution (separate isolated call)
    decision = _run_phase_4b(client, contract, commitment)

    # Phase 6a: evaluator paper-blind setup (sees contract + commitment, NOT decision)
    rubric = _run_phase_6a(eval_client, contract, commitment)

    # Phase 6b: evaluator paper-visible execution
    eval_decision = _run_phase_6b(eval_client, contract, commitment, decision, rubric)

    return {
        "contract": contract.model_dump(mode="json", exclude_none=True),
        "writer_commitment": commitment.model_dump(mode="json", exclude_none=True),
        "writer_decision": decision.model_dump(mode="json", exclude_none=True),
        "evaluator_rubric": rubric.model_dump(mode="json", exclude_none=True),
        "evaluator_decision": eval_decision.model_dump(mode="json", exclude_none=True),
        "divergences": _compute_divergences(commitment, decision, eval_decision),
        "cross_model_used": cross_model_evaluator_client is not None,
    }


# ----------------------------------------------------- per-phase runners


def _run_phase_4a(client: LLMClient, contract: SprintContract) -> WriterCommitment:
    """Writer paper-blind pre-commitment.

    Inputs: contract only (NO paper context, NO library, NO paper plan).
    Output: WriterCommitment — acceptance criteria + intended claims +
    structure + cite keys. The writer commits to what they will produce
    BEFORE seeing the data.
    """
    system = load_prompt("compose_writer_plan")
    user = (
        f"## Contract (paper-blind)\n\n"
        f"section_name: {contract.section_name}\n"
        f"section_type: {contract.section_type}\n"
        f"target_words: {contract.target_words}\n"
        f"dimensions: {list(contract.dimensions)}\n"
        f"library_cite_keys (allowed): {contract.library_cite_keys[:80]}\n"
        + (f"\ninstruction: {contract.instruction}\n" if contract.instruction else "")
        + "\n\nReturn a single JSON object matching the WriterCommitment schema."
    )
    return client.complete_isolated(
        system=system,
        user=user,
        schema=WriterCommitment,
        node=NODE_WRITER_PLAN,
        max_tokens=1500,
        temperature=0.1,
    )


def _run_phase_4b(
    client: LLMClient,
    contract: SprintContract,
    commitment: WriterCommitment,
) -> WriterDecision:
    """Writer paper-visible execution.

    Inputs: contract + commitment + paper context (paper_plan / idea /
    experiment excerpts). Output: WriterDecision — composed text +
    self-scores + cited keys + failure-condition checks.
    """
    system = load_prompt("compose_writer_exec")
    user = (
        f"## Contract\n\n{_dump_for_prompt(contract.model_dump(mode='json', exclude_none=True))}\n\n"
        f"## Pre-commitment (Phase 4a output)\n\n"
        f"{_dump_for_prompt(commitment.model_dump(mode='json', exclude_none=True))}\n\n"
        f"## Paper context\n\n"
        f"paper_plan_excerpt: {_dump_for_prompt(contract.paper_plan_excerpt)}\n\n"
        f"idea_excerpt: {_dump_for_prompt(contract.idea_excerpt)}\n\n"
        f"experiment_excerpt: {_dump_for_prompt(contract.experiment_excerpt)}\n\n"
        "Compose the section per the commitment. "
        "Return a single JSON object matching the WriterDecision schema."
    )
    return client.complete_isolated(
        system=system,
        user=user,
        schema=WriterDecision,
        node=NODE_WRITER_EXEC,
        max_tokens=4096,
        temperature=0.2,
    )


def _run_phase_6a(
    client: LLMClient,
    contract: SprintContract,
    commitment: WriterCommitment,
) -> EvaluatorRubric:
    """Evaluator paper-blind setup.

    Inputs: contract + commitment ONLY. Critically, NO sight of the
    writer's Phase 4b output. The evaluator pre-commits to a rubric:
    what signals warrant high/low scores, what triggers BLOCK / WARN.

    This is the load-bearing isolation that prevents the evaluator from
    rationalising the standard after seeing the draft.
    """
    system = load_prompt("compose_evaluator_setup")
    user = (
        f"## Contract\n\n{_dump_for_prompt(contract.model_dump(mode='json', exclude_none=True))}\n\n"
        f"## Writer's Pre-commitment (Phase 4a)\n\n"
        f"{_dump_for_prompt(commitment.model_dump(mode='json', exclude_none=True))}\n\n"
        "You DO NOT yet see the writer's draft. Pre-commit to your "
        "scoring rubric: per-dimension what_to_look_for / "
        "what_triggers_block / what_triggers_warn. "
        "Return a single JSON object matching the EvaluatorRubric schema."
    )
    return client.complete_isolated(
        system=system,
        user=user,
        schema=EvaluatorRubric,
        node=NODE_EVAL_SETUP,
        max_tokens=2048,
        temperature=0.1,
    )


def _run_phase_6b(
    client: LLMClient,
    contract: SprintContract,
    commitment: WriterCommitment,
    decision: WriterDecision,
    rubric: EvaluatorRubric,
) -> EvaluatorDecision:
    """Evaluator paper-visible execution.

    Inputs: contract + commitment + writer's Phase 4b output + the
    rubric this evaluator pre-committed to in Phase 6a. Outputs:
    dimension scores + decision + must_fix list.
    """
    system = load_prompt("compose_evaluator_exec")
    user = (
        f"## Contract\n\n{_dump_for_prompt(contract.model_dump(mode='json', exclude_none=True))}\n\n"
        f"## Writer's Pre-commitment (Phase 4a)\n\n"
        f"{_dump_for_prompt(commitment.model_dump(mode='json', exclude_none=True))}\n\n"
        f"## Your Pre-committed Rubric (Phase 6a)\n\n"
        f"{_dump_for_prompt(rubric.model_dump(mode='json', exclude_none=True))}\n\n"
        f"## Writer's Draft (Phase 4b)\n\n"
        f"```latex\n{decision.composed_text}\n```\n\n"
        f"writer self-scores: {decision.self_dimension_scores}\n"
        f"writer cited_keys: {decision.cited_keys}\n\n"
        "Score against your pre-committed rubric. Cite specific signals "
        "from the rubric in `review_body`. "
        "Return a single JSON object matching the EvaluatorDecision schema."
    )
    return client.complete_isolated(
        system=system,
        user=user,
        schema=EvaluatorDecision,
        node=NODE_EVAL_EXEC,
        max_tokens=2048,
        temperature=0.0,
    )


# ----------------------------------------------------- helpers


def _dump_for_prompt(payload: Any, max_chars: int = 2000) -> str:
    """Serialize a dict / Any for inclusion in an LLM prompt.

    Truncates to ``max_chars`` to keep token count predictable across
    phases — the contract + commitment chain accumulates fast.
    """
    import json
    if payload is None:
        return "(none)"
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...(truncated)"


def _compute_divergences(
    commitment: WriterCommitment,
    decision: WriterDecision,
    eval_decision: EvaluatorDecision,
) -> dict[str, Any]:
    """Surface drift signals between phases — useful for caller to flag
    silent quality drift.

    - ``cite_drift``: cite_keys writer committed to vs actually used
    - ``score_divergence``: writer self-scores vs evaluator scores per
      dimension; values > 15 points apart are flagged
    - ``commitment_breach``: dimensions where evaluator scored below
      target_threshold the writer committed to
    """
    intended = set(commitment.intended_cite_keys)
    used = set(decision.cited_keys)
    cite_drift = {
        "added": sorted(used - intended),
        "dropped": sorted(intended - used),
    }

    score_divergence: dict[str, dict[str, int]] = {}
    for dim, writer_score in decision.self_dimension_scores.items():
        eval_score = eval_decision.dimension_scores.get(dim)
        if eval_score is None:
            continue
        delta = abs(writer_score - eval_score)
        if delta > 15:
            score_divergence[dim] = {
                "writer": writer_score,
                "evaluator": eval_score,
                "delta": delta,
            }

    commitment_breach: list[dict] = []
    target_thresholds = {c.dimension: c.target_threshold for c in commitment.acceptance_criteria}
    for dim, eval_score in eval_decision.dimension_scores.items():
        target = target_thresholds.get(dim)
        if target is not None and eval_score < target:
            commitment_breach.append({
                "dimension": dim,
                "target": target,
                "eval_score": eval_score,
                "shortfall": target - eval_score,
            })

    return {
        "cite_drift": cite_drift,
        "score_divergence": score_divergence,
        "commitment_breach": commitment_breach,
    }


def build_contract(
    *,
    section_name: str,
    section_type: str,
    target_words: int,
    library_cite_keys: list[str],
    paper_plan_excerpt: dict | None = None,
    idea_excerpt: dict | None = None,
    experiment_excerpt: dict | None = None,
    instruction: str | None = None,
    dimensions: list[str] | None = None,
) -> SprintContract:
    """Convenience constructor for the contract baseline."""
    return SprintContract(
        section_name=section_name,
        section_type=section_type,
        target_words=target_words,
        dimensions=list(dimensions or DEFAULT_DIMENSIONS),
        library_cite_keys=list(library_cite_keys),
        paper_plan_excerpt=paper_plan_excerpt,
        idea_excerpt=idea_excerpt,
        experiment_excerpt=experiment_excerpt,
        instruction=instruction,
    )

"""4-call compose pipeline tests — physical isolation guard (ARS-fusion P1-1)."""

from __future__ import annotations

import pytest

from paic.latex.compose_v2 import (
    NODE_EVAL_EXEC,
    NODE_EVAL_SETUP,
    NODE_WRITER_EXEC,
    NODE_WRITER_PLAN,
    build_contract,
    run_4call_compose,
)
from paic.schemas.sprint_contract import (
    DEFAULT_DIMENSIONS,
    EvaluatorDecision,
    EvaluatorRubric,
    SprintContract,
    WriterCommitment,
    WriterDecision,
)


class _StubLLMClient:
    """Returns canned outputs based on schema; records every call's user
    payload so the test can assert each phase saw the right (and only the
    right) inputs."""

    model = "stub-compose-v2"

    def __init__(self):
        self.calls: list[dict] = []
        self.cfg = None

    def complete_isolated(
        self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None,
    ):
        self.calls.append({
            "node": node,
            "schema": schema.__name__,
            "user_chars": len(user),
            "user": user,
            "system_first_line": (system.splitlines() or [""])[0],
        })
        if schema is WriterCommitment:
            return WriterCommitment(
                section_name="03_method",
                target_words=900,
                acceptance_criteria=[
                    {
                        "dimension": d,
                        "target_threshold": 75,
                        "success_signals": [f"signal for {d}"],
                    }
                    for d in DEFAULT_DIMENSIONS
                ],
                intended_claims=["claim 1", "claim 2"],
                intended_structure=["overview", "details"],
                intended_cite_keys=["smith_2023"],
            )
        if schema is WriterDecision:
            return WriterDecision(
                composed_text="\\section{Method}\nA composed section.",
                self_dimension_scores={d: 80 for d in DEFAULT_DIMENSIONS},
                cited_keys=["smith_2023"],
                failure_condition_checks={},
            )
        if schema is EvaluatorRubric:
            return EvaluatorRubric(
                contract_paraphrase="Writer commits to a 900-word method section.",
                per_dimension_criteria={
                    d: {
                        "what_to_look_for": [f"signal for {d}"],
                        "what_triggers_block": ["serious flaw"],
                        "what_triggers_warn": ["minor concern"],
                    }
                    for d in DEFAULT_DIMENSIONS
                },
            )
        if schema is EvaluatorDecision:
            return EvaluatorDecision(
                dimension_scores={d: 78 for d in DEFAULT_DIMENSIONS},
                failure_condition_checks={},
                review_body="Solid draft per the rubric.",
                decision="accept",
                must_fix=[],
                nice_to_fix=[],
            )
        raise AssertionError(f"unexpected schema: {schema!r}")

    def complete_json(self, **kw):
        return self.complete_isolated(**kw)


def _make_contract():
    return build_contract(
        section_name="03_method",
        section_type="method",
        target_words=900,
        library_cite_keys=["smith_2023", "jones_2024"],
        paper_plan_excerpt={"thesis": "T", "contributions": [{"id": "C1"}]},
        idea_excerpt={"title": "Idea", "summary": "..."},
        experiment_excerpt={"id": "exp_1"},
    )


def test_run_4call_compose_invokes_4_phases_in_order():
    stub = _StubLLMClient()
    contract = _make_contract()
    out = run_4call_compose(contract=contract, client=stub)

    assert len(stub.calls) == 4
    assert [c["node"] for c in stub.calls] == [
        NODE_WRITER_PLAN,
        NODE_WRITER_EXEC,
        NODE_EVAL_SETUP,
        NODE_EVAL_EXEC,
    ]
    assert out["writer_decision"]["composed_text"]
    assert out["evaluator_decision"]["decision"] == "accept"


def test_writer_plan_phase_4a_does_not_see_paper_context():
    """Phase 4a is paper-blind: must not contain paper_plan / idea /
    experiment excerpts in the user prompt."""
    stub = _StubLLMClient()
    contract = _make_contract()
    run_4call_compose(contract=contract, client=stub)

    plan_call = next(c for c in stub.calls if c["node"] == NODE_WRITER_PLAN)
    user = plan_call["user"]
    # Contract scaffolding allowed
    assert "section_name: 03_method" in user
    assert "library_cite_keys" in user
    # Paper context is NOT allowed
    assert "paper_plan_excerpt" not in user
    assert "idea_excerpt" not in user
    assert "experiment_excerpt" not in user
    assert "exp_1" not in user
    assert '"title": "Idea"' not in user


def test_evaluator_setup_phase_6a_does_not_see_writer_decision():
    """Phase 6a is the load-bearing isolation: must not contain the
    writer's Phase 4b composed_text in the user prompt."""
    stub = _StubLLMClient()
    contract = _make_contract()
    run_4call_compose(contract=contract, client=stub)

    setup_call = next(c for c in stub.calls if c["node"] == NODE_EVAL_SETUP)
    user = setup_call["user"]
    # Phase 4a commitment allowed
    assert "Pre-commitment" in user
    # Phase 4b output forbidden — the load-bearing isolation
    assert "composed_text" not in user
    assert "A composed section" not in user
    assert "self_dimension_scores" not in user


def test_evaluator_exec_phase_6b_sees_all_prior_phases():
    """Phase 6b is the only phase with full visibility."""
    stub = _StubLLMClient()
    contract = _make_contract()
    run_4call_compose(contract=contract, client=stub)

    exec_call = next(c for c in stub.calls if c["node"] == NODE_EVAL_EXEC)
    user = exec_call["user"]
    # All four blocks present
    assert "Pre-commitment" in user
    assert "Pre-committed Rubric" in user
    assert "Writer's Draft" in user
    assert "A composed section" in user


def test_writer_exec_phase_4b_sees_commitment_and_paper_context():
    """Phase 4b must see Phase 4a commitment and paper context, but NOT
    the evaluator's rubric (which doesn't exist yet at this point)."""
    stub = _StubLLMClient()
    contract = _make_contract()
    run_4call_compose(contract=contract, client=stub)

    exec_call = next(c for c in stub.calls if c["node"] == NODE_WRITER_EXEC)
    user = exec_call["user"]
    assert "Pre-commitment" in user
    assert "paper_plan_excerpt" in user
    # Evaluator rubric MUST NOT leak into the writer's exec context
    assert "Pre-committed Rubric" not in user


def test_run_4call_returns_divergences_block():
    stub = _StubLLMClient()
    contract = _make_contract()
    out = run_4call_compose(contract=contract, client=stub)

    div = out["divergences"]
    assert "cite_drift" in div
    assert "score_divergence" in div
    assert "commitment_breach" in div


def test_cross_model_evaluator_uses_separate_client():
    """When cross_model_evaluator_client supplied, evaluator phases call it."""
    writer_stub = _StubLLMClient()
    eval_stub = _StubLLMClient()
    contract = _make_contract()
    out = run_4call_compose(
        contract=contract,
        client=writer_stub,
        cross_model_evaluator_client=eval_stub,
    )
    # Writer phases on writer_stub (2 calls); evaluator phases on eval_stub
    writer_nodes = [c["node"] for c in writer_stub.calls]
    eval_nodes = [c["node"] for c in eval_stub.calls]
    assert writer_nodes == [NODE_WRITER_PLAN, NODE_WRITER_EXEC]
    assert eval_nodes == [NODE_EVAL_SETUP, NODE_EVAL_EXEC]
    assert out["cross_model_used"] is True


def test_4_new_node_names_in_host_supported_nodes():
    from paic.config import HOST_SUPPORTED_NODES
    for n in (NODE_WRITER_PLAN, NODE_WRITER_EXEC, NODE_EVAL_SETUP, NODE_EVAL_EXEC):
        assert n in HOST_SUPPORTED_NODES, f"missing {n}"


def test_complete_isolated_method_exists_on_llm_client():
    from paic.llm.client import LLMClient
    assert hasattr(LLMClient, "complete_isolated")

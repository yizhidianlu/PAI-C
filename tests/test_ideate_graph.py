"""Ideate graph + run registry tests — Phase 5."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.mcp_server.tools.ideate import ideate_start, ideate_step
from paic.mcp_server.tools.runs import (
    runs_cancel_tool,
    runs_list_tool,
    runs_resume_tool,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.paper import PaperRef, PaperSummary


class _StubIdeateLLM:
    """Schema-dispatching stub: returns `_BrainstormOutput` for brainstorm
    calls and `_PanelScoreOutput` for panel scoring calls.

    Tracks per-node call counts so tests can assert the expected number of
    LLM invocations (e.g. brainstorm=1 + 4 panel personas per scoring round,
    minus cache hits).
    """

    model = "stub-model"

    # Predictable scores per draft for assertions: idx → (feas, nov, imp).
    # All personas return the same scores → panel_consensus=high_agreement.
    def __init__(self, n: int = 3, *, panel_jitter: bool = False):
        self.n = n
        self.panel_jitter = panel_jitter
        self.calls_by_node: dict[str | None, int] = {}

    # Lightweight router-aware adapter so tests that read `_backend_for`
    # (e.g. _looks_serial_only) don't need a full router.
    def _backend_for(self, node):
        from types import SimpleNamespace
        return SimpleNamespace(name="anthropic.api_key")

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        from paic.graphs.ideate_graph import (
            _BrainstormOutput,
            _IdeaDraft,
            _PanelScoreOutput,
            _PersonaIdeaScore,
        )

        self.calls_by_node[node] = self.calls_by_node.get(node, 0) + 1

        if schema is _BrainstormOutput:
            ideas = [
                _IdeaDraft(
                    title=f"Idea {i}",
                    one_liner=f"One liner {i}",
                    motivation="Motivation",
                    proposed_approach="Approach " * 5,
                    novelty_claim="Novelty",
                    expected_contribution="Contribution",
                    grounded_in=["2401.12345"],
                    contrasts_with=[],
                    risk_factors=["risk a", "risk b"],
                )
                for i in range(self.n)
            ]
            return _BrainstormOutput(ideas=ideas)

        if schema is _PanelScoreOutput:
            # Parse the user message to find idx values being asked about
            import re
            indices = sorted({int(m.group(1)) for m in re.finditer(r"idx=(\d+)", user)})
            offset = 0.0
            if self.panel_jitter and node:
                # Different personas → different score offsets so panel_consensus
                # tests can hit the "diverged" branch
                offset = {"idea_score_methodology": 0.0,
                          "idea_score_novelty": 0.2,
                          "idea_score_impact": -0.1,
                          "idea_score_reviewer2": -0.3}.get(node, 0.0)
            scores = [
                _PersonaIdeaScore(
                    idx=i,
                    feasibility=max(0.0, min(1.0, 0.5 + 0.1 * i + offset)),
                    novelty=max(0.0, min(1.0, 0.6 + offset * 0.5)),
                    impact=max(0.0, min(1.0, 0.5 + offset * 0.3)),
                    rationale=f"persona stub rationale for idx={i}",
                    red_flags=[f"flag from {node}"],
                )
                for i in indices
            ]
            return _PanelScoreOutput(scores=scores)

        raise AssertionError(f"unexpected schema in stub: {schema}")

    @property
    def calls(self) -> int:
        """Total LLM calls — for back-compat with old assertions."""
        return sum(self.calls_by_node.values())


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    from paic.graphs.checkpointer import reset_checkpointer_cache
    from paic.mcp_server.runs import reset_registry_cache

    reset_config_cache()
    reset_checkpointer_cache()
    reset_registry_cache()

    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    # plant a summary so gather_corpus has something to feed brainstorm
    summary = PaperSummary(
        paper=PaperRef(arxiv_id="2401.12345", title="Sample"),
        problem="P",
        method="M",
        key_results=["r"],
        limitations=["l"],
        techniques=["t"],
        summarized_at=datetime.now(UTC),
        summarizer_model="stub",
    )
    from paic.workspace.store import save_yaml

    save_yaml(
        project_dir / ".paic/library/summaries/2401.12345.yaml",
        summary.model_dump(mode="json"),
    )
    return project_dir


def test_ideate_start_pauses_at_interrupt(project):
    stub = _StubIdeateLLM(n=3)
    out = ideate_start(str(project), focus="diffusion video", n_candidates=3, llm=stub)
    assert out["status"] == "awaiting_input"
    # §26: interrupt node renamed from await_user_filter → await_user_decision
    assert out["current_node"] == "await_user_decision"
    assert len(out["preview_ideas"]) == 3
    # 1 brainstorm + 4 personas (default panel) = 5 calls
    assert stub.calls_by_node.get("ideate_brainstorm") == 1
    assert sum(v for k, v in stub.calls_by_node.items() if k and k.startswith("idea_score_")) == 4
    # No idea YAML files written yet — finalize hasn't run.
    assert list((project / ".paic/ideas").glob("*.yaml")) == []
    # round bumped to 1
    assert out["round"] == 1
    # drafts_with_scores has the new richer payload
    assert all("composite" in d for d in out["drafts_with_scores"])


def test_ideate_response_trims_persona_rationale(project):
    """Full per-persona rationale strings must NOT be in the response payload —
    they bloat the response past Claude Code's token budget. ``rationale_brief``
    (≤80 chars) is kept for tooltips; full text remains in the LangGraph
    checkpoint for paic_runs_resume.
    """
    stub = _StubIdeateLLM(n=3)
    out = ideate_start(str(project), focus="x", n_candidates=3, llm=stub)
    drafts = out["drafts_with_scores"]
    assert drafts
    for draft in drafts:
        breakdown = draft["persona_breakdown"]
        assert breakdown, "expected at least one persona scored"
        for persona, entry in breakdown.items():
            # Trimmed shape: scores + truncated red_flags + rationale_brief only
            assert "rationale" not in entry, (
                f"full rationale leaked into response for persona={persona}"
            )
            assert "rationale_brief" in entry
            assert len(entry["rationale_brief"]) <= 80
            assert len(entry["red_flags"]) <= 3
            for rf in entry["red_flags"]:
                assert len(rf) <= 200


def test_ideate_step_finalizes_kept_ideas(project):
    """Legacy compat: ideate_step(keep=[...], feedback=...) without action → finalize."""
    stub = _StubIdeateLLM(n=4)
    start = ideate_start(str(project), focus="x", n_candidates=4, llm=stub)
    run_id = start["run_id"]

    # No action= passed → §26.10 R92 legacy compat path infers "finalize"
    out = ideate_step(str(project), run_id, keep=[0, 2], feedback="keep best two", llm=stub)
    assert out["status"] == "done"
    finalized = out["finalized_ids"]
    assert len(finalized) == 2

    idea_files = [
        p for p in (project / ".paic/ideas").glob("*.yaml") if p.name != "_ranking.yaml"
    ]
    assert len(idea_files) == 2
    assert (project / ".paic/ideas/_ranking.yaml").is_file()

    # Check the saved IdeaCard has v2 fields
    from paic.workspace.store import load_yaml
    card = load_yaml(idea_files[0])
    assert card["schema_version"] == 2
    assert "panel_scores" in card
    assert isinstance(card["red_flags"], list)
    assert card["feedback_log"] == ["keep best two"]
    assert card["rounds_used"] == 1


def test_runs_list_and_cancel(project):
    stub = _StubIdeateLLM(n=2)
    start = ideate_start(str(project), n_candidates=2, llm=stub)
    listing = runs_list_tool(project_dir=str(project))
    assert listing["count"] == 1
    assert listing["runs"][0]["kind"] == "ideate"
    assert listing["runs"][0]["status"] == "awaiting_input"

    cancel = runs_cancel_tool(start["run_id"])
    assert cancel["status"] == "cancelled"
    listing2 = runs_list_tool(project_dir=str(project), status_filter="cancelled")
    assert listing2["count"] == 1


def test_runs_resume_dispatches_to_ideate(project):
    stub = _StubIdeateLLM(n=2)
    start = ideate_start(str(project), n_candidates=2, llm=stub)
    out = runs_resume_tool(start["run_id"], keep=[0])
    assert out["status"] == "done"
    assert len(out["finalized_ids"]) == 1


def test_ideate_handles_empty_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    from paic.graphs.checkpointer import reset_checkpointer_cache
    from paic.mcp_server.runs import reset_registry_cache

    reset_config_cache()
    reset_checkpointer_cache()
    reset_registry_cache()

    p = tmp_path / "empty_proj"
    workspace_init(p)
    stub = _StubIdeateLLM(n=2)
    out = ideate_start(str(p), n_candidates=2, llm=stub)
    # Empty corpus shouldn't crash; LLM still produces drafts (they may be weak).
    assert out["status"] == "awaiting_input"

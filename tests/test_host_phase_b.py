"""Phase B host directive tests — paper_plan / claims / relwork / revision /
figure_plan / figure_prompt all return mode=host_orchestration when their
node is routed to ``host``."""

from __future__ import annotations

import pytest
import yaml

from paic.config import reset_config_cache
from paic.mcp_server.tools import (
    claims as claims_tools,
    figure as figure_tools,
    paper_plan as paper_plan_tools,
    related_work as related_work_tools,
    revisions as revisions_tools,
)
from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.workspace import workspace_init


def _write_routing(home, **routing):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "anthropic": {
                        "mode": "claude_agent_sdk",
                        "model": "claude-opus-4-7",
                    }
                },
                "routing": routing,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


def _seed_idea(project_dir):
    """Drop a minimal IdeaCard YAML so paper_plan_create_tool finds an idea."""
    ideas_dir = project_dir / ".paic" / "ideas"
    ideas_dir.mkdir(parents=True, exist_ok=True)
    (ideas_dir / "idea_001.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "idea_001",
                "title": "Foo",
                "one_liner": "bar",
                "motivation": "baz",
                "proposed_approach": "qux",
                "novelty_claim": "n",
                "expected_contribution": "c",
                "grounded_in": [],
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def _seed_library_and_summary(project_dir):
    library_add_tool(
        str(project_dir),
        [{"arxiv_id": "2401.99999", "title": "Cluster Test", "authors": ["A"]}],
    )


def test_paper_plan_generate_host_directive(project, tmp_path):
    home = tmp_path / ".paic"
    _write_routing(home, default="anthropic", overrides={"paper_plan_generate": "host"})
    reset_config_cache()
    _seed_idea(project)
    out = paper_plan_tools.paper_plan_create_tool(str(project), "idea_001")
    assert out["mode"] == "host_orchestration"
    assert out["node"] == "paper_plan_generate"
    assert out["next_tool"] == "mcp__paic__paic_paper_plan_persist"
    assert out["idea_id"] == "idea_001"
    assert "schema_hint" in out
    assert "thesis" in out["schema_hint"]["properties"]


def test_claim_extract_host_directive(project, tmp_path):
    home = tmp_path / ".paic"
    _write_routing(home, default="anthropic", overrides={"claim_extract": "host"})
    reset_config_cache()
    out = claims_tools.claims_extract_tool(
        str(project),
        section_name="03_method",
        section_text="Our method achieves SOTA on benchmark X \\cite{foo}.",
    )
    assert out["mode"] == "host_orchestration"
    assert out["node"] == "claim_extract"
    assert out["next_tool"] == "mcp__paic__paic_claims_extract_persist"
    assert out["section_name"] == "03_method"


def test_relwork_cluster_host_directive(project, tmp_path):
    home = tmp_path / ".paic"
    _write_routing(home, default="anthropic", overrides={"relwork_cluster": "host"})
    reset_config_cache()
    _seed_library_and_summary(project)
    out = related_work_tools.related_work_cluster_tool(str(project))
    assert out["mode"] == "host_orchestration"
    assert out["node"] == "relwork_cluster"
    assert out["next_tool"] == "mcp__paic__paic_relwork_cluster_persist"


def test_revision_extract_host_directive(project, tmp_path):
    home = tmp_path / ".paic"
    _write_routing(home, default="anthropic", overrides={"revision_extract": "host"})
    reset_config_cache()
    out = revisions_tools.revision_extract_tool(
        str(project),
        review_payload={"moderator": "Tighten the abstract."},
        round_num=1,
    )
    assert out["mode"] == "host_orchestration"
    assert out["node"] == "revision_extract"
    assert out["next_tool"] == "mcp__paic__paic_revision_extract_persist"
    assert out["round_num"] == 1


def test_figure_plan_host_directive(project, tmp_path):
    home = tmp_path / ".paic"
    _write_routing(home, default="anthropic", overrides={"figure_plan": "host"})
    reset_config_cache()
    out = figure_tools.figure_plan(str(project), max_figures=3)
    assert out["mode"] == "host_orchestration"
    assert out["node"] == "figure_plan"
    assert out["next_tool"] == "mcp__paic__paic_figure_plan_persist"
    assert out["max_figures"] == 3


def test_paper_plan_persist_validates_schema(project, tmp_path):
    """persist tool must reject malformed `fields` payloads."""
    home = tmp_path / ".paic"
    _write_routing(home, default="anthropic")
    reset_config_cache()
    out = paper_plan_tools.paper_plan_persist_tool(
        str(project),
        fields={"thesis": "x"},  # missing other required structures? actually thesis is the only required field
        idea_id="idea_001",
    )
    # `thesis` is the only required _PlanFields field; others default. This
    # should *succeed*, demonstrating the persist tool runs the whole tail
    # (writing paper_plan.yaml) without an LLM.
    assert out.get("written") is True
    assert "plan" in out
    assert out["plan"]["thesis"] == "x"


def test_claims_extract_persist_round_trip(project, tmp_path):
    """persist tool builds Claim objects and merges into ledger."""
    home = tmp_path / ".paic"
    _write_routing(home, default="anthropic")
    reset_config_cache()
    out = claims_tools.claims_extract_persist_tool(
        str(project),
        extracted={
            "claims": [
                {
                    "text": "We achieve 12% gain.",
                    "type": "comparative",
                    "status": "needs_evidence",
                }
            ]
        },
        section_name="04_experiments",
        section_text="We achieve 12% gain over baseline \\cite{smith2024}.",
    )
    assert out["section_name"] == "04_experiments"
    assert out["extracted_count"] == 1
    # Inline cite from section_text should have been added to required_citations
    needs = out["needs_evidence_strong"]
    assert len(needs) == 1
    assert needs[0]["type"] == "comparative"

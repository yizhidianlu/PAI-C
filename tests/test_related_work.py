"""Tests for §quality phase 7 — related-work clustering."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, Field

from paic.library.clustering import (
    cluster_related_work,
    load_related_work_plan,
    save_related_work_plan,
)
from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.related_work import (
    related_work_cluster_tool,
    related_work_status_tool,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.related_work import RelatedWorkCluster, RelatedWorkPlan
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


class _ClusterStubLLM:
    """Stub LLM that returns a fixed cluster set."""

    model = "stub-cluster"

    def __init__(self, response):
        self.response = response

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        return self.response


def _stub_clusters():
    from paic.library.clustering import _ClusterFields
    return _ClusterFields(clusters=[
        RelatedWorkCluster(
            id="RW1", label="CSP family", axis="method",
            members=["arxiv_p1", "arxiv_p2"],
            contrast_to_proposed="Unlike CSP-family methods, our approach selects channels per-subject.",
        ),
        RelatedWorkCluster(
            id="RW2", label="EEGNet derivatives", axis="method",
            members=["arxiv_p3"],
            contrast_to_proposed="In contrast to compact CNN backbones, we use Fisher score.",
        ),
    ])


@pytest.fixture
def project_with_papers(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    p = tmp_path / "p"
    workspace_init(p)
    library_add_tool(str(p), [
        {"arxiv_id": "p1", "title": "CSP for MI EEG", "authors": ["A"]},
        {"arxiv_id": "p2", "title": "Riemannian CSP", "authors": ["B"]},
        {"arxiv_id": "p3", "title": "EEGNet compact CNN", "authors": ["C"]},
    ])
    return p


# ----------------------------------------------------- schema


def test_cluster_round_trip():
    c = RelatedWorkCluster(
        id="RW1", label="x", axis="method",
        members=["a", "b"], contrast_to_proposed="contrast",
    )
    reloaded = RelatedWorkCluster.model_validate(c.model_dump(mode="json"))
    assert reloaded.id == "RW1"


def test_cluster_invalid_axis_rejected():
    with pytest.raises(Exception):
        RelatedWorkCluster(
            id="RW1", label="x", axis="not_an_axis",  # type: ignore[arg-type]
            members=[], contrast_to_proposed="x",
        )


def test_plan_round_trip():
    plan = RelatedWorkPlan(clusters=[
        RelatedWorkCluster(id="RW1", label="x", axis="method",
                           members=[], contrast_to_proposed="x"),
    ])
    reloaded = RelatedWorkPlan.from_yaml_dict(plan.model_dump(mode="json"))
    assert len(reloaded.clusters) == 1


# ----------------------------------------------------- cluster function


def test_cluster_returns_empty_when_library_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    p = tmp_path / "p"
    workspace_init(p)
    paths = resolve_project(str(p))
    llm = _ClusterStubLLM(_stub_clusters())  # never invoked
    clusters = cluster_related_work(paths, paper_plan=None, llm=llm)
    assert clusters == []


def test_cluster_calls_llm_and_returns_list(project_with_papers):
    paths = resolve_project(str(project_with_papers))
    llm = _ClusterStubLLM(_stub_clusters())
    clusters = cluster_related_work(paths, paper_plan=None, llm=llm)
    assert len(clusters) == 2
    assert clusters[0].id == "RW1"


# ----------------------------------------------------- tool


def test_tool_persists_clusters(project_with_papers):
    llm = _ClusterStubLLM(_stub_clusters())
    res = related_work_cluster_tool(str(project_with_papers), llm=llm)
    assert res.get("error") is None
    assert res["cluster_count"] == 2
    assert res["total_members"] == 3
    paths = resolve_project(str(project_with_papers))
    plan = load_related_work_plan(paths)
    assert len(plan.clusters) == 2
    assert plan.last_updated_at is not None


def test_tool_empty_library_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    p = tmp_path / "p"
    workspace_init(p)
    llm = _ClusterStubLLM(_stub_clusters())
    res = related_work_cluster_tool(str(p), llm=llm)
    assert res["error"] == "library_empty"


def test_status_tool_when_missing(project_with_papers):
    res = related_work_status_tool(str(project_with_papers))
    assert res["exists"] is False


def test_status_tool_after_clustering(project_with_papers):
    related_work_cluster_tool(str(project_with_papers), llm=_ClusterStubLLM(_stub_clusters()))
    res = related_work_status_tool(str(project_with_papers))
    assert res["exists"] is True
    assert res["cluster_count"] == 2


# ----------------------------------------------------- compose integration


def test_outline_includes_related_work_clusters_for_02_related():
    """When clusters are passed and section is 02_related, the outline
    prompt mentions the cluster ids and contrast points."""
    from paic.latex.paragraph_compose import _format_outline_prompt
    clusters = [
        {"id": "RW1", "label": "CSP family", "axis": "method",
         "members": ["arxiv_p1"],
         "contrast_to_proposed": "We select channels per-subject."},
    ]
    out = _format_outline_prompt(
        section="02_related",
        paper_plan=None, idea=None, experiment=None,
        retrieval_hits=[], claims=[], target_words=800,
        instruction=None, related_work_clusters=clusters,
    )
    assert "Related-work clusters" in out
    assert "RW1" in out
    assert "CSP family" in out
    assert "We select channels per-subject" in out


def test_outline_skips_related_work_clusters_for_other_sections():
    """The cluster block must NOT appear when section != 02_related."""
    from paic.latex.paragraph_compose import _format_outline_prompt
    clusters = [
        {"id": "RW1", "label": "x", "axis": "method",
         "members": [], "contrast_to_proposed": "y"},
    ]
    out = _format_outline_prompt(
        section="01_intro",  # not related-work
        paper_plan=None, idea=None, experiment=None,
        retrieval_hits=[], claims=[], target_words=800,
        instruction=None, related_work_clusters=clusters,
    )
    assert "Related-work clusters" not in out


# ----------------------------------------------------- persistence


def test_save_load_round_trip(project_with_papers):
    paths = resolve_project(str(project_with_papers))
    plan = RelatedWorkPlan(clusters=[
        RelatedWorkCluster(id="RW1", label="x", axis="method",
                           members=["a"], contrast_to_proposed="y"),
    ])
    save_related_work_plan(paths, plan)
    reloaded = load_related_work_plan(paths)
    assert len(reloaded.clusters) == 1
    assert reloaded.clusters[0].id == "RW1"

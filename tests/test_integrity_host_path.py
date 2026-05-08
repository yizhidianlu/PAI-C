"""Test that paic_integrity_check returns a host directive when integrity_judge=host."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import yaml

from paic.mcp_server.tools.integrity import (
    INTEGRITY_NODE,
    integrity_check_tool,
    integrity_persist_tool,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


@dataclass
class _StubPaper:
    title: str
    authors: list
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None


@dataclass
class _StubResult:
    papers: list


@pytest.fixture
def project_with_host_routing(tmp_path, monkeypatch):
    home = tmp_path / ".paic_home"
    home.mkdir()
    cfg_yaml = home / "config.yaml"
    cfg_yaml.write_text(
        yaml.safe_dump({
            "providers": {"anthropic": {"mode": "api_key", "api_key_env": "X_KEY"}},
            "routing": {
                "default": "anthropic",
                "overrides": {"integrity_judge": "host"},
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("PAIC_HOME", str(home))
    from paic.config import reset_config_cache
    reset_config_cache()

    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    paths = resolve_project(str(project_dir))
    save_yaml(paths.selected_yaml, {
        "papers": [
            {"arxiv_id": "9999.0001", "title": "Made-up Paper", "authors": ["X"]},
        ],
    })
    # Stub the S2 client at the runner's import binding so the runner
    # doesn't touch the network. (paic.integrity.runner does
    # `from paic.integrity.citation_check import verify_library_via_s2`,
    # so the symbol must be patched on the runner module, not the source.)
    from paic.integrity.types import WebSearchPending

    def _fake_verify(paths, **kw):
        return (
            [],
            [WebSearchPending(
                cite_key="arxiv_9999_0001",
                title="Made-up Paper",
                authors=["X"],
            )],
            [],
            kw.get("cache") or {},
        )

    monkeypatch.setattr("paic.integrity.runner.verify_library_via_s2", _fake_verify)
    return project_dir


def test_integrity_check_returns_host_directive(project_with_host_routing):
    out = integrity_check_tool(str(project_with_host_routing))
    assert out["mode"] == "host_orchestration"
    assert out["node"] == INTEGRITY_NODE
    assert out["next_tool"] == "mcp__paic__paic_integrity_persist"
    # Metadata fields promoted to top level by HostOrchestrationDirective.to_dict()
    assert "pending_websearch" in out
    assert len(out["pending_websearch"]) == 1
    assert "pending_ai_judge" in out
    assert "structural_issues" in out
    # Schema hint references the persist tool's input shape
    assert "$defs" in out["schema_hint"] or "properties" in out["schema_hint"]


def test_integrity_persist_finalizes_with_websearch_NOT_FOUND(project_with_host_routing):
    """Round-trip: directive → user supplies NOT_FOUND verdict → persist returns blocker."""
    out = integrity_persist_tool(
        str(project_with_host_routing),
        web_search_results=[{
            "cite_key": "arxiv_9999_0001",
            "verdict": "NOT_FOUND",
            "evidence_url": [],
            "notes": "no results found",
        }],
        ai_judge_results=[
            {"mode": 5, "status": "VERIFIED"},
            {"mode": 6, "status": "VERIFIED"},
        ],
    )
    assert out["passed"] is False
    kinds = [i["kind"] for i in out["issues"]]
    assert "TF" in kinds
    # State report written to disk
    paths = resolve_project(str(project_with_host_routing))
    assert (paths.state_dir / "integrity_report.yaml").is_file()

"""Tests for paic_disclosure_generate (ARS-fusion P0-3)."""

from __future__ import annotations

import pytest

from paic.format.disclosure import (
    SUPPORTED_VENUES,
    DisclosureContext,
    generate_disclosure,
)
from paic.mcp_server.tools.disclosure import disclosure_generate_tool
from paic.mcp_server.tools.workspace import workspace_init


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


def test_supported_venues_minimum_set():
    """All 6 ARS-supported venues + generic fallback."""
    expected = {"iclr2026", "neurips2026", "nature", "science", "acl", "emnlp", "generic"}
    assert expected.issubset(set(SUPPORTED_VENUES))


def test_generate_disclosure_rejects_empty_tools():
    ctx = DisclosureContext(venue="iclr2026", tools=[])
    out = generate_disclosure(ctx)
    assert out["error"] == "tools_required"


def test_generate_disclosure_rejects_invalid_output_format():
    ctx = DisclosureContext(
        venue="iclr2026",
        tools=[{"name": "Claude Code", "stage": "drafting", "extent": "extensive", "purpose": "polish"}],
        output_format="rtf",
    )
    out = generate_disclosure(ctx)
    assert out["error"] == "invalid_output_format"


def test_generate_disclosure_iclr2026_renders_tools():
    ctx = DisclosureContext(
        venue="iclr2026",
        tools=[
            {"name": "Claude Code", "stage": "drafting", "extent": "extensive", "purpose": "draft polish"},
            {"name": "ChatGPT-4", "stage": "literature_review", "extent": "minor", "purpose": "summarize 3 papers"},
        ],
        paper_title="Sample paper",
    )
    out = generate_disclosure(ctx)
    assert "error" not in out
    assert out["venue"] == "iclr2026"
    assert "Claude Code" in out["content"]
    assert "ChatGPT-4" in out["content"]
    assert "Sample paper" in out["content"]
    assert "ICLR 2026" in out["content"]
    # ICLR-specific iron rule about not listing AI as author
    assert "No AI tool is listed as an author" in out["content"]
    assert out["placement_hint"]
    assert out["warnings"] == []


def test_generate_disclosure_unknown_venue_falls_back_to_generic():
    ctx = DisclosureContext(
        venue="some_random_workshop_2030",
        tools=[{"name": "Claude", "stage": "drafting", "extent": "minor", "purpose": "polish"}],
    )
    out = generate_disclosure(ctx)
    assert out["venue"] == "generic"
    assert any("Unknown venue" in w for w in out["warnings"])


def test_generate_disclosure_neurips_includes_human_responsibility():
    ctx = DisclosureContext(
        venue="neurips2026",
        tools=[{"name": "Claude", "stage": "analysis", "extent": "moderate", "purpose": "data exploration"}],
    )
    out = generate_disclosure(ctx)
    assert "Human responsibility statement" in out["content"]


def test_generate_disclosure_includes_raise_equity_when_provided():
    ctx = DisclosureContext(
        venue="generic",
        tools=[{"name": "Claude", "stage": "drafting", "extent": "minor", "purpose": "polish"}],
        raise_equity_note="The authors verified AI-suggested phrasing for fair representation.",
    )
    out = generate_disclosure(ctx)
    assert "fair representation" in out["content"]


# --- MCP tool layer ---------------------------------------------------------


def test_mcp_tool_writes_to_disk(project, tmp_path):
    out = disclosure_generate_tool(
        str(project),
        venue="iclr2026",
        tools=[
            {"name": "Claude Code", "stage": "drafting", "extent": "extensive", "purpose": "draft polish"},
        ],
        write_to="drafts/disclosure.md",
    )
    assert "error" not in out
    assert "written_to" in out
    target = project / ".paic/drafts/disclosure.md"
    # write_to is project-relative, lands under .paic/<...>
    # Note: workspace_init creates drafts/ dir; write_text re-creates path.
    written_path = out["written_to"]
    from pathlib import Path
    assert Path(written_path).is_file()
    assert "Claude Code" in Path(written_path).read_text(encoding="utf-8")


def test_mcp_tool_cleans_incomplete_tool_entries(project):
    """Tools with missing fields are filled with sensible defaults; tools without name dropped."""
    out = disclosure_generate_tool(
        str(project),
        venue="generic",
        tools=[
            {"name": "Claude"},  # only name → defaults applied
            {"stage": "drafting"},  # no name → dropped
            {"name": "GPT-4", "stage": "analysis", "extent": "moderate", "purpose": "data viz"},
        ],
    )
    assert "error" not in out
    assert "Claude" in out["content"]
    assert "GPT-4" in out["content"]
    # Defaults: stage=drafting, extent=moderate, purpose=(unspecified)
    assert "(unspecified)" in out["content"]


def test_mcp_tool_rejects_missing_project(tmp_path):
    out = disclosure_generate_tool(
        str(tmp_path / "no_project"),
        venue="iclr2026",
        tools=[{"name": "Claude", "stage": "drafting", "extent": "minor", "purpose": "polish"}],
    )
    assert out["error"] == "project_not_initialized"


def test_mcp_tool_emits_supported_venues_list(project):
    out = disclosure_generate_tool(
        str(project),
        venue="generic",
        tools=[{"name": "X", "stage": "drafting", "extent": "minor", "purpose": "y"}],
    )
    assert "supported_venues" in out
    assert "iclr2026" in out["supported_venues"]

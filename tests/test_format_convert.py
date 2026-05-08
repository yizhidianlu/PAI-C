"""Tests for paic_format_convert (ARS-fusion P0-3).

Pandoc is a weak dependency; tests cover both the present-pandoc path
(when available) and the missing-pandoc graceful-degrade path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paic.format.bib_convert import (
    CSL_ALIASES,
    detect_csl_path,
    list_csl_aliases,
)
from paic.format.pandoc_bridge import pandoc_available
from paic.mcp_server.tools.format_convert import (
    diagnose_csl,
    format_convert_tool,
    list_supported_citation_styles,
)
from paic.mcp_server.tools.workspace import workspace_init


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


def test_csl_aliases_have_5_canonical_styles():
    aliases = set(list_csl_aliases())
    for canonical in {"apa7", "chicago-author-date", "mla9", "ieee", "vancouver"}:
        assert canonical in aliases, f"missing {canonical}"


def test_csl_alias_mapping_is_consistent():
    """Aliases pointing at the same canonical name should resolve identically."""
    apa_filename = CSL_ALIASES["apa7"]
    apa_alias_filename = CSL_ALIASES["apa"]
    assert apa_filename == apa_alias_filename


def test_detect_csl_path_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_CSL_DIR", str(tmp_path / "no_csl"))
    out = detect_csl_path("apa7")
    assert out is None


def test_detect_csl_path_finds_user_dropped_file(tmp_path, monkeypatch):
    csl_dir = tmp_path / "csl"
    csl_dir.mkdir()
    (csl_dir / "apa.csl").write_text("<style>...</style>", encoding="utf-8")
    monkeypatch.setenv("PAIC_CSL_DIR", str(csl_dir))
    out = detect_csl_path("apa7")
    assert out is not None
    assert out.name == "apa.csl"


def test_diagnose_csl_returns_search_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_CSL_DIR", str(tmp_path / "csl"))
    out = diagnose_csl("apa7")
    assert "search_path" in out
    assert any("PAIC_CSL_DIR" in s for s in out["search_path"])


def test_format_convert_rejects_missing_project(tmp_path):
    out = format_convert_tool(
        str(tmp_path / "no_project"),
        input_path="x.md",
        output_path="x.docx",
    )
    assert out["error"] == "project_not_initialized"


@pytest.mark.skipif(pandoc_available(), reason="needs pandoc-missing path")
def test_format_convert_graceful_degrade_when_pandoc_missing(project, tmp_path):
    out = format_convert_tool(
        str(project),
        input_path="drafts/main.tex",
        output_path="drafts/main.docx",
    )
    assert out["error"] == "pandoc_unavailable"
    assert "install" in out["hint"].lower()


@pytest.mark.skipif(not pandoc_available(), reason="needs pandoc")
def test_format_convert_md_to_docx_with_pandoc(project, tmp_path):
    drafts = project / ".paic" / "drafts"
    src = drafts / "tiny.md"
    src.write_text("# Hello\n\nWorld.\n", encoding="utf-8")
    out = format_convert_tool(
        str(project),
        input_path="drafts/tiny.md",
        output_path="drafts/tiny.docx",
        target_format="docx",
    )
    assert out.get("ok") is True
    assert (drafts / "tiny.docx").is_file()


@pytest.mark.skipif(not pandoc_available(), reason="needs pandoc")
def test_format_convert_input_not_found(project):
    out = format_convert_tool(
        str(project),
        input_path="drafts/no_such_file.tex",
        output_path="drafts/out.docx",
    )
    assert out["error"] == "input_not_found"


def test_list_supported_citation_styles():
    out = list_supported_citation_styles()
    assert "aliases" in out
    assert "apa7" in out["aliases"]
    assert "alias_to_filename" in out

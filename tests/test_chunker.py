"""Tests for paic.library.chunker — heading-aware chunking + on-disk index.

P0 #1 (chunk-level grounding): the chunker is the foundation that lets
compose paragraph-mode inject actual paper passages into the LLM prompt
instead of one-line summaries. These tests cover the size targets,
heading hierarchy preservation, fallback paths (no headings / empty
input), persistence round-trip, and the section-path tracking.
"""

from __future__ import annotations

import json

import pytest

from paic.library.chunker import (
    Chunk,
    chunk_index_build,
    chunk_index_path,
    chunk_paper_markdown,
    load_all_chunks,
    load_chunk_index,
    save_chunk_index,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return resolve_project(str(project_dir))


# ----------------------------------------------------- chunking


def test_empty_markdown_returns_no_chunks():
    assert chunk_paper_markdown("", "arxiv_p1") == []
    assert chunk_paper_markdown("   \n\n  ", "arxiv_p1") == []


def test_chunker_requires_cite_key():
    with pytest.raises(ValueError):
        chunk_paper_markdown("Some text.", "")


def test_short_markdown_yields_one_chunk():
    md = "Just a few words about a thing."
    chunks = chunk_paper_markdown(md, "arxiv_p1")
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "arxiv_p1__c000"
    assert chunks[0].paper_cite_key == "arxiv_p1"
    assert chunks[0].text == md
    assert chunks[0].section_path == ""  # no headings


def test_heading_hierarchy_recorded_in_section_path():
    md = (
        "# Introduction\n\nIntro text.\n\n"
        "## Background\n\nBackground details.\n\n"
        "## Method\n\nMethod overview.\n\n"
        "### Architecture\n\nDeep into architecture.\n"
    )
    chunks = chunk_paper_markdown(md, "arxiv_p1")
    paths = [c.section_path for c in chunks]
    assert "Introduction" in paths
    # H2 nests under H1.
    assert "Introduction > Background" in paths
    # H3 nests under H1 > H2 (NOT under "Method" alone).
    assert "Introduction > Method > Architecture" in paths


def test_oversized_section_split_into_multiple_chunks():
    """A section larger than target_tokens fans out into multiple chunks
    that each respect the target."""
    big_paragraph = " ".join(["word"] * 1000)
    md = f"# Big\n\n{big_paragraph}\n"
    chunks = chunk_paper_markdown(md, "arxiv_p1", target_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 4  # 1000 words / 200 target = ~5
    # Each chunk's token_count is bounded by target + overlap budget.
    for chunk in chunks:
        assert chunk.token_count <= 250  # target + overlap headroom
    # All chunks belong to the same section.
    assert all(c.section_path == "Big" for c in chunks)


def test_paragraph_split_preserves_paragraph_boundary():
    """Multiple paragraphs in one section should be packed up to target_tokens
    without slicing across a paragraph boundary unless a single paragraph is
    itself oversized."""
    md = (
        "# Section\n\n"
        + "\n\n".join(f"Paragraph {i} with a moderate amount of content." for i in range(20))
    )
    chunks = chunk_paper_markdown(md, "arxiv_p1", target_tokens=50, overlap_tokens=10)
    # At least 2 chunks emitted from this 20-paragraph section.
    assert len(chunks) >= 2
    # Stable, contiguous chunk ids.
    ids = [c.chunk_id for c in chunks]
    assert ids == sorted(ids)


def test_pre_heading_prologue_kept_with_empty_section_path():
    md = "Some prologue text before any heading.\n\n# First Heading\n\nBody."
    chunks = chunk_paper_markdown(md, "arxiv_p1")
    assert chunks[0].section_path == ""
    assert "prologue" in chunks[0].text


def test_no_headings_yields_one_chunk_when_short():
    md = "Single block of text with no headings at all."
    chunks = chunk_paper_markdown(md, "arxiv_p1")
    assert len(chunks) == 1
    assert chunks[0].section_path == ""


def test_chunk_id_format_stable():
    md = "# A\n\np1\n\n# B\n\np2\n"
    chunks = chunk_paper_markdown(md, "my_cite")
    assert chunks[0].chunk_id == "my_cite__c000"
    assert chunks[1].chunk_id == "my_cite__c001"


def test_char_offsets_within_input():
    """Offsets should be inside the original markdown bounds — useful for
    re-extraction / debugging the chunker's slicing logic."""
    md = "# Section A\n\nFirst body content here.\n\n# Section B\n\nSecond body content here.\n"
    chunks = chunk_paper_markdown(md, "arxiv_p1")
    for c in chunks:
        assert 0 <= c.char_offset_start <= len(md)
        assert c.char_offset_end <= len(md) + len(c.text)  # generous bound


# ----------------------------------------------------- on-disk index


def test_save_and_load_chunk_index(project):
    chunks = [
        Chunk(
            chunk_id="arxiv_p1__c000",
            paper_cite_key="arxiv_p1",
            section_path="Intro",
            text="some text",
            token_count=2,
            char_offset_start=0,
            char_offset_end=9,
        ),
    ]
    path = save_chunk_index(project, "arxiv_p1", chunks)
    assert path.is_file()
    assert path == chunk_index_path(project, "arxiv_p1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cite_key"] == "arxiv_p1"
    assert len(payload["chunks"]) == 1

    reloaded = load_chunk_index(project, "arxiv_p1")
    assert len(reloaded) == 1
    assert reloaded[0].text == "some text"


def test_load_chunk_index_missing_returns_empty(project):
    assert load_chunk_index(project, "nonexistent") == []


def test_load_chunk_index_corrupt_returns_empty(project):
    """A malformed json file shouldn't crash callers — return empty so the
    retriever just falls back to paper-level."""
    path = chunk_index_path(project, "broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert load_chunk_index(project, "broken") == []


def test_load_all_chunks_aggregates_per_paper(project):
    save_chunk_index(project, "p1", [
        Chunk(chunk_id="p1__c000", paper_cite_key="p1", text="t1",
              token_count=1, char_offset_start=0, char_offset_end=2),
    ])
    save_chunk_index(project, "p2", [
        Chunk(chunk_id="p2__c000", paper_cite_key="p2", text="t2",
              token_count=1, char_offset_start=0, char_offset_end=2),
        Chunk(chunk_id="p2__c001", paper_cite_key="p2", text="t3",
              token_count=1, char_offset_start=2, char_offset_end=4),
    ])
    by_paper = load_all_chunks(project)
    assert set(by_paper.keys()) == {"p1", "p2"}
    assert len(by_paper["p1"]) == 1
    assert len(by_paper["p2"]) == 2


def test_load_all_chunks_empty_when_no_dir(project):
    """Older projects (created before P0 #1) have no chunks/ dir — should
    return {} not raise."""
    # The project fixture doesn't create chunks/, so load_all_chunks sees
    # a missing dir.
    assert load_all_chunks(project) == {}


def test_chunk_index_build_persists_chunks(project):
    md = "# Intro\n\nIntro body.\n\n# Method\n\nMethod body.\n"
    written = chunk_index_build(project, "arxiv_p1", md)
    assert len(written) >= 2
    reloaded = load_chunk_index(project, "arxiv_p1")
    assert len(reloaded) == len(written)


def test_chunk_index_build_skips_empty_input(project):
    """No file written when markdown is empty — keeps auto-chunk-after-summarize
    safe for projects without a body source."""
    written = chunk_index_build(project, "arxiv_p1", "")
    assert written == []
    assert not chunk_index_path(project, "arxiv_p1").exists()

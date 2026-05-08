"""Tests for the S2 batch verification + 5-type hallucination classifier."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from paic.integrity.citation_check import (
    classify_websearch_results,
    verify_library_via_s2,
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
def project_with_library(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    paths = resolve_project(str(project_dir))
    save_yaml(paths.selected_yaml, {
        "papers": [
            {
                "arxiv_id": "2401.12345",
                "title": "Attention Is All You Need",
                "authors": ["Vaswani", "Shazeer"],
                "year": 2017,
                "doi": "10.1234/aiayn",
            },
            {
                "arxiv_id": "9999.99999",
                "title": "A Completely Made-Up Paper About Nothing",
                "authors": ["Imaginary Author"],
                "year": 2024,
            },
        ],
    })
    return paths


def test_verify_library_returns_pending_when_disabled(project_with_library):
    verified, pending, issues, cache = verify_library_via_s2(
        project_with_library, enabled=False,
    )
    assert verified == []
    assert len(pending) == 2
    assert all(p.s2_attempted is False for p in pending)
    assert all(p.reason == "s2_disabled" for p in pending)
    assert issues == []


def test_verify_library_marks_s2_match_as_verified(project_with_library):
    def stub_search(query, limit=5):
        if "Attention" in query:
            return _StubResult(papers=[
                _StubPaper(
                    title="Attention Is All You Need",
                    authors=["A. Vaswani", "N. Shazeer"],
                    year=2017,
                    doi="10.1234/aiayn",
                ),
            ])
        return _StubResult(papers=[])

    verified, pending, issues, cache = verify_library_via_s2(
        project_with_library, s2_search_fn=stub_search,
    )
    assert "arxiv_2401_12345" in verified
    assert any(p.cite_key == "arxiv_9999_99999" for p in pending)
    # Cache populated for both verified + pending entries
    assert len(cache) == 2


def test_verify_library_emits_year_drift_as_minor_SH(project_with_library):
    def stub_search(query, limit=5):
        if "Attention" in query:
            return _StubResult(papers=[
                _StubPaper(
                    title="Attention Is All You Need",
                    authors=["A. Vaswani"],
                    year=2018,  # selected.yaml says 2017 — drift
                    doi="10.1234/aiayn",
                ),
            ])
        return _StubResult(papers=[])

    _, _, issues, _ = verify_library_via_s2(
        project_with_library, s2_search_fn=stub_search,
    )
    sh_issues = [i for i in issues if i.kind == "SH"]
    assert sh_issues, "expected SH year-drift issue"
    assert sh_issues[0].severity == "minor"
    assert sh_issues[0].suggested_correction == {"year": 2018}


def test_verify_library_uses_cache_on_repeat(project_with_library):
    """Cache hit should not re-call S2."""
    call_count = {"n": 0}

    def stub_search(query, limit=5):
        call_count["n"] += 1
        if "Attention" in query:
            return _StubResult(papers=[
                _StubPaper(title="Attention Is All You Need", authors=["A. Vaswani"], year=2017),
            ])
        return _StubResult(papers=[])

    _, _, _, cache = verify_library_via_s2(
        project_with_library, s2_search_fn=stub_search,
    )
    initial = call_count["n"]

    # Second call with prior cache → no fresh S2 calls for cache hits.
    _, _, _, _ = verify_library_via_s2(
        project_with_library, cache=cache, s2_search_fn=stub_search,
    )
    assert call_count["n"] == initial


def test_classify_websearch_NOT_FOUND_emits_TF_blocker():
    issues = classify_websearch_results([{
        "cite_key": "fake_2024",
        "verdict": "NOT_FOUND",
        "evidence_url": [],
        "notes": "no relevant results",
    }])
    assert len(issues) == 1
    assert issues[0].kind == "TF"
    assert issues[0].severity == "blocker"
    assert issues[0].target == "fake_2024"


def test_classify_websearch_VERIFIED_drops_no_issue():
    issues = classify_websearch_results([{
        "cite_key": "real_paper",
        "verdict": "VERIFIED",
        "evidence_url": ["https://doi.org/10.1234/x"],
    }])
    assert issues == []


def test_classify_websearch_MISMATCH_authors_changed_emits_PAC():
    """Authors entirely changed → PAC (Plausible Author/Conference)."""
    from paic.integrity.citation_check import _LibraryEntry
    library_index = {
        "fake_2020": _LibraryEntry(
            cite_key="fake_2020",
            title="Sample Paper",
            authors=("Alice Smith", "Bob Jones"),
            year=2020,
            doi=None,
            arxiv_id=None,
            s2_id=None,
        ),
    }
    issues = classify_websearch_results(
        [{
            "cite_key": "fake_2020",
            "verdict": "MISMATCH",
            "evidence_url": ["https://x"],
            "matched_authors": ["Charlie Brown", "Diana Prince"],
            "matched_year": 2020,
            "notes": "wrong attribution",
        }],
        library_index=library_index,
    )
    assert len(issues) == 1
    assert issues[0].kind == "PAC"
    assert issues[0].suggested_correction is not None

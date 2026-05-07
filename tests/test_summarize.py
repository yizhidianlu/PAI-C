"""Summarize tool tests — Phase 4.

We mock the LLM client and the arxiv-bridge file lookup so the test suite
never touches the network or the real arxiv MCP storage.
"""

from __future__ import annotations

import pytest

from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.summarize import _SummaryFields, summarize_run
from paic.mcp_server.tools.workspace import workspace_init


class _StubLLM:
    """Returns a fixed _SummaryFields response, ignoring the prompt."""

    model = "stub-model"

    def __init__(self, response: _SummaryFields):
        self.response = response
        self.calls: list[dict] = []

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        self.calls.append({"system": system[:80], "user": user[:80], "schema": schema.__name__})
        assert schema is _SummaryFields
        return self.response


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache

    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    library_add_tool(
        str(project_dir),
        [{"arxiv_id": "2401.12345", "title": "Sample Paper", "authors": ["A", "B"]}],
    )
    return project_dir


def test_summarize_writes_markdown_and_yaml(project, tmp_path, monkeypatch):
    # arrange: place a fake markdown body where arxiv_bridge will find it
    storage = tmp_path / "arxiv_storage"
    storage.mkdir()
    (storage / "2401.12345.md").write_text("# Sample\n\nBody of the paper.", encoding="utf-8")
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: (storage / f"{paper_id}.md").read_text(encoding="utf-8")
        if (storage / f"{paper_id}.md").exists()
        else None,
    )

    response = _SummaryFields(
        problem="P", method="M",
        key_results=["r1", "r2"],
        limitations=["L1"],
        techniques=["x", "y"],
        relevance_to_project=None,
    )
    stub = _StubLLM(response)

    out = summarize_run(str(project), "2401.12345", llm=stub)

    assert "error" not in out
    assert out["from_cache"] is False
    assert out["structured"]["problem"] == "P"
    # Filenames are now keyed by cite_key (canonical, filesystem-safe).
    assert out["cite_key"] == "arxiv_2401_12345"
    assert (project / ".paic/library/summaries/arxiv_2401_12345.md").is_file()
    assert (project / ".paic/library/summaries/arxiv_2401_12345.yaml").is_file()


def test_summarize_caches_on_disk(project, tmp_path, monkeypatch):
    storage = tmp_path / "arxiv_storage"
    storage.mkdir()
    (storage / "2401.12345.md").write_text("body", encoding="utf-8")
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: "body",
    )

    response = _SummaryFields(problem="P", method="M")
    stub1 = _StubLLM(response)
    summarize_run(str(project), "2401.12345", llm=stub1)
    assert len(stub1.calls) == 1

    stub2 = _StubLLM(response)
    out = summarize_run(str(project), "2401.12345", llm=stub2)
    assert out["from_cache"] is True
    assert stub2.calls == []  # didn't re-invoke LLM


def test_summarize_paper_not_in_library(project):
    out = summarize_run(str(project), "9999.99999", llm=_StubLLM(_SummaryFields(problem="x", method="y")))
    assert out["error"] == "paper_not_in_library"


def test_summarize_markdown_missing(project, monkeypatch):
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: None,
    )
    out = summarize_run(str(project), "2401.12345", llm=_StubLLM(_SummaryFields(problem="x", method="y")))
    assert out["error"] == "paper_markdown_not_found"
    assert "looked_under" in out
    # The hint should mention paper_text= as a remediation
    assert "paper_text" in out["hint"]


def test_summarize_paper_text_bypass(project, monkeypatch):
    """When markdown can't be found locally, caller can pass paper_text directly."""
    # Local read returns None (no fs hit), but caller-supplied text should win.
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: None,
    )
    response = _SummaryFields(problem="P", method="M")
    out = summarize_run(
        str(project),
        "2401.12345",
        paper_text="# Sample\n\nSome body of arbitrary text.",
        llm=_StubLLM(response),
    )
    assert "error" not in out
    assert out["text_source"] == "caller_supplied"
    assert out["structured"]["problem"] == "P"


def test_summarize_paper_text_overrides_local(project, monkeypatch):
    """Even when local markdown exists, paper_text takes precedence."""
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: "local body",
    )
    out = summarize_run(
        str(project),
        "2401.12345",
        paper_text="caller body",
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert out["text_source"] == "caller_supplied"


# ---------------------------------------------------------------- §25 PDF fallback


def test_summarize_falls_back_to_library_pdfs_md(project, monkeypatch):
    """When upstream markdown is missing, summarize reads library/pdfs/<cite_key>.md."""
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: None,
    )
    # Place markdown at the §24 attach location
    pdfs_dir = project / ".paic/library/pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    (pdfs_dir / "arxiv_2401_12345.md").write_text(
        "# Sample\n\nProject-local markdown body for the paper.",
        encoding="utf-8",
    )

    out = summarize_run(
        str(project),
        "2401.12345",
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert "error" not in out
    assert out["text_source"] == "library_pdfs_md"
    assert out["structured"]["problem"] == "P"


def test_summarize_falls_back_to_library_pdfs_pdf(project, monkeypatch, tmp_path):
    """library/pdfs/<cite_key>.pdf → pypdf extract → summarize."""
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: None,
    )
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.extract_pdf_text",
        lambda path, *, cache_dir=None, min_chars=200: (
            "Extracted body of the PubMed paper. " * 20,
            None,
        ),
    )

    pdfs_dir = project / ".paic/library/pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    # Place a fake PDF — extract_pdf_text is mocked, so the file just needs to exist
    (pdfs_dir / "arxiv_2401_12345.pdf").write_bytes(b"%PDF-1.4 fake")

    out = summarize_run(
        str(project),
        "2401.12345",
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert "error" not in out
    assert out["text_source"] == "library_pdfs_pdf_extracted"


def test_summarize_pdf_extraction_failure_surfaces_reason(project, monkeypatch):
    """When the PDF exists but extraction fails, the error includes the reason + hint."""
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: None,
    )
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.extract_pdf_text",
        lambda path, *, cache_dir=None, min_chars=200: (None, "empty_extraction"),
    )

    pdfs_dir = project / ".paic/library/pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    (pdfs_dir / "arxiv_2401_12345.pdf").write_bytes(b"%PDF-1.4 fake scanned")

    out = summarize_run(
        str(project),
        "2401.12345",
        llm=_StubLLM(_SummaryFields(problem="x", method="y")),
    )
    assert out["error"] == "paper_markdown_not_found"
    assert out["pdf_extraction_failed_reason"] == "empty_extraction"
    assert "tried_pdf_path" in out
    assert "ocrmypdf" in out["pdf_hint"]


def test_summarize_md_takes_precedence_over_pdf(project, monkeypatch):
    """If both .md and .pdf exist, .md wins (faster + likely cleaner text)."""
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: None,
    )
    pdfs_dir = project / ".paic/library/pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    (pdfs_dir / "arxiv_2401_12345.md").write_text("# md\n\nMarkdown body content.", encoding="utf-8")
    (pdfs_dir / "arxiv_2401_12345.pdf").write_bytes(b"%PDF-1.4 fake")

    # If we don't mock extract_pdf_text, only md path is exercised
    out = summarize_run(
        str(project),
        "2401.12345",
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert out["text_source"] == "library_pdfs_md"


# ---------------------------------------------------------------- paper resolver
@pytest.fixture
def doi_project(tmp_path, monkeypatch):
    """Project with a DOI-only paper (no arxiv_id) — typical PubMed/OpenAlex case."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache

    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    library_add_tool(
        str(project_dir),
        [
            {
                "doi": "10.3389/fnins.2023.1276067",
                "title": "Frontiers Neuroscience Sample",
                "authors": ["X", "Y"],
                "platform": "pubmed",
                "external_ids": {"PMID": "37925884", "PMCID": "PMC10620345"},
            }
        ],
    )
    return project_dir


def _stub_pdf_md(project, cite_key: str, body: str = "DOI paper body content."):
    pdfs_dir = project / ".paic/library/pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    (pdfs_dir / f"{cite_key}.md").write_text(body, encoding="utf-8")


def test_resolve_by_doi_for_non_arxiv_paper(doi_project, monkeypatch):
    """Bug #4: DOI lookup must work — pre-fix only arxiv_id and s2_id matched,
    so every PubMed/OpenAlex paper hit `paper_not_in_library`."""
    cite_key = "doi_10_3389_fnins_2023_1276067"
    _stub_pdf_md(doi_project, cite_key)
    out = summarize_run(
        str(doi_project),
        "10.3389/fnins.2023.1276067",
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert "error" not in out, out
    assert out["cite_key"] == cite_key
    assert out["text_source"] == "library_pdfs_md"
    assert (doi_project / f".paic/library/summaries/{cite_key}.md").is_file()
    assert (doi_project / f".paic/library/summaries/{cite_key}.yaml").is_file()


def test_resolve_by_pmid_via_external_ids(doi_project):
    """external_ids.PMID must resolve — users frequently have only the PMID handy."""
    cite_key = "doi_10_3389_fnins_2023_1276067"
    _stub_pdf_md(doi_project, cite_key)
    out = summarize_run(
        str(doi_project),
        "37925884",  # the PMID
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert "error" not in out, out
    assert out["cite_key"] == cite_key


def test_resolve_by_pmcid_via_external_ids(doi_project):
    cite_key = "doi_10_3389_fnins_2023_1276067"
    _stub_pdf_md(doi_project, cite_key)
    out = summarize_run(
        str(doi_project),
        "PMC10620345",
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert "error" not in out, out
    assert out["cite_key"] == cite_key


def test_resolve_by_cite_key(doi_project):
    cite_key = "doi_10_3389_fnins_2023_1276067"
    _stub_pdf_md(doi_project, cite_key)
    out = summarize_run(
        str(doi_project),
        cite_key,  # pass the cite_key directly
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert "error" not in out, out
    assert out["cite_key"] == cite_key


def test_unresolvable_id_returns_error_with_descriptive_hint(doi_project):
    out = summarize_run(
        str(doi_project),
        "totally-bogus-id",
        llm=_StubLLM(_SummaryFields(problem="x", method="y")),
    )
    assert out["error"] == "paper_not_in_library"
    # Hint should mention the new resolver coverage so users know what
    # forms are accepted.
    assert "external_ids" in out["hint"]
    assert "cite_key" in out["hint"]


def test_legacy_summary_filename_still_hits_cache(project, monkeypatch):
    """Existing libraries have summaries at <arxiv_id>.yaml (pre-fix naming).
    Cache lookup must keep finding them, otherwise upgrade re-burns LLM
    calls on every prior summary."""
    legacy_yaml = project / ".paic/library/summaries/2401.12345.yaml"
    legacy_md = project / ".paic/library/summaries/2401.12345.md"
    legacy_yaml.parent.mkdir(parents=True, exist_ok=True)
    # Write a legacy-format summary (any non-empty dict suffices for the
    # cache-hit path; structure is rendered through unchanged).
    legacy_yaml.write_text(
        "paper: {arxiv_id: '2401.12345', title: 'Sample Paper'}\n"
        "problem: P\nmethod: M\n",
        encoding="utf-8",
    )
    legacy_md.write_text("# Summary: Sample\n", encoding="utf-8")

    out = summarize_run(
        str(project),
        "2401.12345",
        llm=_StubLLM(_SummaryFields(problem="X", method="Y")),  # never invoked
    )
    assert out["from_cache"] is True
    # Cache hit should keep returning the legacy path (no migration).
    assert "2401.12345.md" in out["summary_path"]


def test_summarize_reads_pdf_local_path_from_selected_yaml(doi_project, monkeypatch):
    """When selected.yaml entry has pdf_local_path, summarize reads from
    that filename, not from <cite_key>.<ext>. Covers ingest's new
    NNN_title naming scheme."""
    from paic.workspace.store import load_yaml, save_yaml

    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: None,
    )

    # Write the file under the new human-readable name
    pdfs_dir = doi_project / ".paic/library/pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    (pdfs_dir / "001_frontiers_neuroscience_sample.md").write_text(
        "# Sample\n\nProject body via NNN_title naming.",
        encoding="utf-8",
    )
    # Note: deliberately DON'T create <cite_key>.md — proves the resolver
    # actually used pdf_local_path.

    selected_path = doi_project / ".paic/library/selected.yaml"
    selected = load_yaml(selected_path)
    selected["papers"][0]["pdf_local_path"] = "001_frontiers_neuroscience_sample.md"
    save_yaml(selected_path, selected)

    out = summarize_run(
        str(doi_project),
        "10.3389/fnins.2023.1276067",
        llm=_StubLLM(_SummaryFields(problem="P", method="M")),
    )
    assert "error" not in out, out
    assert out["text_source"] == "library_pdfs_md"


def test_persist_uses_cite_key_filename(doi_project):
    """summarize_persist (host-orchestration write path) also keys on cite_key."""
    from paic.mcp_server.tools.summarize import summarize_persist

    cite_key = "doi_10_3389_fnins_2023_1276067"
    out = summarize_persist(
        str(doi_project),
        "37925884",  # PMID — exercises resolver too
        structured={
            "problem": "P", "method": "M",
            "key_results": ["k1"], "limitations": ["l1"], "techniques": ["t1"],
        },
    )
    assert out["persisted"] is True
    assert out["cite_key"] == cite_key
    assert (doi_project / f".paic/library/summaries/{cite_key}.yaml").is_file()


# ----------------------------------------------------- §quality phase 3


def test_phase3_legacy_summary_yaml_loads(project, tmp_path, monkeypatch):
    """Pre-phase-3 summaries (no datasets / baselines / etc fields) must
    still load and round-trip without ValidationError."""
    from paic.schemas.paper import PaperSummary
    legacy = {
        "paper": {"arxiv_id": "2401.12345", "title": "Sample", "authors": ["A"]},
        "problem": "P", "method": "M",
        "key_results": ["r1"], "limitations": ["l1"], "techniques": ["t1"],
        "summarized_at": "2024-01-01T00:00:00+00:00",
        "summarizer_model": "old-model",
    }
    summary = PaperSummary.model_validate(legacy)
    # New fields default to empty / None.
    assert summary.datasets == []
    assert summary.baselines == []
    assert summary.contribution_type is None
    # Round-trip without losing legacy content.
    reloaded = PaperSummary.model_validate(summary.model_dump(mode="json"))
    assert reloaded.problem == "P"


def test_phase3_full_summary_round_trip():
    from paic.schemas.paper import PaperSummary
    full = {
        "paper": {"arxiv_id": "p1", "title": "x", "authors": ["A"]},
        "problem": "P", "method": "M",
        "key_results": [], "limitations": [], "techniques": [],
        "contribution_type": "method",
        "datasets": ["BCI-IV-2a", "PhysioNet"],
        "baselines": ["CSP", "EEGNet"],
        "metrics": ["balanced accuracy", "Cohen's kappa"],
        "numeric_results": ["+3.2% balanced accuracy on BCI-IV-2a"],
        "assumptions": ["Stationary subject"],
        "failure_modes": ["High inter-session variance"],
        "open_questions": ["Cross-subject zero-shot?"],
        "citation_claims": ["Fisher score top-k matches CSP."],
        "quote_spans": ["Channel pruning is data-efficient."],
        "summarized_at": "2024-01-01T00:00:00+00:00",
        "summarizer_model": "stub",
    }
    summary = PaperSummary.model_validate(full)
    assert summary.contribution_type == "method"
    assert "BCI-IV-2a" in summary.datasets
    assert len(summary.numeric_results) == 1


def test_phase3_summarize_run_passes_through_new_fields(project, tmp_path, monkeypatch):
    storage = tmp_path / "arxiv_storage"
    storage.mkdir()
    (storage / "2401.12345.md").write_text("# Sample\n\nBody.", encoding="utf-8")

    from paic.config import reset_config_cache
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_alt"))
    reset_config_cache()
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: f"# Sample\n\n{(storage / '2401.12345.md').read_text(encoding='utf-8')}",
    )

    fields = _SummaryFields(
        problem="P", method="M",
        key_results=["k1"], limitations=["l1"], techniques=["t1"],
        contribution_type="method",
        datasets=["BCI-IV-2a"],
        baselines=["CSP"],
        metrics=["balanced accuracy"],
        numeric_results=["+3.2% on BCI-IV-2a"],
        citation_claims=["Fisher score is competitive."],
    )
    out = summarize_run(str(project), "2401.12345", llm=_StubLLM(fields))
    assert "error" not in out, out
    structured = out["structured"]
    assert structured["contribution_type"] == "method"
    assert structured["datasets"] == ["BCI-IV-2a"]
    assert structured["baselines"] == ["CSP"]
    # Render markdown should contain the new sections.
    md_text = (project / ".paic/library/summaries/arxiv_2401_12345.md").read_text(
        encoding="utf-8"
    )
    assert "## Datasets" in md_text
    assert "BCI-IV-2a" in md_text
    assert "## Numeric Results" in md_text
    assert "## Baselines" in md_text


def test_phase3_render_omits_empty_sections(project, tmp_path, monkeypatch):
    """When new fields are empty, their headings must NOT appear in the
    rendered markdown — so old-style summaries don't sprout empty sections."""
    storage = tmp_path / "arxiv_storage"
    storage.mkdir()
    (storage / "2401.12345.md").write_text("# Sample\n\nBody.", encoding="utf-8")

    from paic.config import reset_config_cache
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_alt2"))
    reset_config_cache()
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: "# Sample\n\nbody",
    )

    fields = _SummaryFields(problem="P", method="M")  # all phase-3 fields default-empty
    summarize_run(str(project), "2401.12345", llm=_StubLLM(fields))
    md_text = (project / ".paic/library/summaries/arxiv_2401_12345.md").read_text(
        encoding="utf-8"
    )
    assert "## Datasets" not in md_text
    assert "## Baselines" not in md_text
    assert "## Numeric Results" not in md_text
    assert "Contribution type:" not in md_text


def test_phase3_persist_accepts_new_fields(doi_project):
    from paic.mcp_server.tools.summarize import summarize_persist
    out = summarize_persist(
        str(doi_project),
        "37925884",  # PMID
        structured={
            "problem": "P", "method": "M",
            "key_results": [], "limitations": [], "techniques": [],
            "contribution_type": "system",
            "datasets": ["MIMIC-III"],
            "baselines": ["LSTM"],
            "metrics": ["AUC"],
            "numeric_results": ["AUC=0.84"],
        },
    )
    assert out["persisted"] is True
    structured = out["structured"]
    assert structured["contribution_type"] == "system"
    assert structured["datasets"] == ["MIMIC-III"]


def test_phase3_persist_legacy_payload_still_works(doi_project):
    """Pre-phase-3 host-orchestrated callers (only 5 keys) must still work."""
    from paic.mcp_server.tools.summarize import summarize_persist
    out = summarize_persist(
        str(doi_project),
        "37925884",
        structured={
            "problem": "P", "method": "M",
            "key_results": [], "limitations": [], "techniques": [],
        },
    )
    assert out["persisted"] is True
    structured = out["structured"]
    # New fields default to empty / None — not absent.
    assert structured["datasets"] == []
    assert structured["contribution_type"] is None

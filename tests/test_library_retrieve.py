"""Tests for paic.library.retrieval — BM25 + MMR retrieval over the project library.

Covers tokenization, build_query section templates, BM25 ranking, MMR
diversity, snippet extraction, edge cases (empty library, empty query, k=0,
unknown tokens), and the MCP wrapper's error paths.
"""

from __future__ import annotations

import pytest

from paic.library.retrieval import (
    LibraryRetriever,
    _tokenize,
    build_query,
)
from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.library_retrieve import library_retrieve_tool
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


def _add_papers(project_dir, papers):
    library_add_tool(str(project_dir), papers)


def _write_summary(project_dir, cite_key: str, fields: dict):
    paths = resolve_project(str(project_dir))
    paths.summaries_dir.mkdir(parents=True, exist_ok=True)
    save_yaml(paths.summaries_dir / f"{cite_key}.yaml", fields)


# ----------------------------------------------------- tokenize


def test_tokenize_lowercases_and_splits():
    assert _tokenize("Self-Attention is ALL you NEED") == ["self-attention", "all", "you", "need"]


def test_tokenize_drops_stopwords():
    out = _tokenize("the model is a method based on attention")
    assert "the" not in out
    assert "is" not in out
    assert "based" not in out
    assert "method" not in out
    assert "attention" in out


def test_tokenize_keeps_hyphens_in_compound_words():
    assert "self-attention" in _tokenize("self-attention layer")


def test_tokenize_drops_single_chars():
    out = _tokenize("a b cd ef")
    assert "a" not in out
    assert "cd" in out


def test_tokenize_handles_empty_input():
    assert _tokenize("") == []


# ----------------------------------------------------- build_query


def test_build_query_includes_section_hint():
    q = build_query("01_intro")
    assert "motivation" in q
    assert "introduction" in q


def test_build_query_combines_idea_and_plan():
    plan = {
        "thesis": "Channel pruning improves cross-subject EEG decoding.",
        "section_plan": [
            {"name": "01_intro", "intent": "Motivate channel selection problem.",
             "supports_contributions": ["C1"]},
        ],
        "terminology": {"channel pruning": "subset of EEG electrodes"},
    }
    idea = {"title": "Cross-subject channel pruning", "one_liner": "Top-k Fisher selection."}
    q = build_query("01_intro", paper_plan=plan, idea=idea)
    assert "channel" in q.lower()
    assert "fisher" in q.lower()


def test_build_query_pulls_experiment_for_method_section():
    exp = {
        "proposed_method": "Top-k Fisher channel selection.",
        "datasets": [{"name": "BCI-IV-2a"}],
        "baselines": [{"name": "CSP"}],
        "metrics": [{"name": "accuracy"}],
    }
    q = build_query("04_experiments", experiment=exp)
    assert "fisher" in q.lower()
    assert "bci-iv-2a" in q.lower()
    assert "csp" in q.lower()
    assert "accuracy" in q.lower()


def test_build_query_skips_experiment_for_non_method_section():
    exp = {
        "proposed_method": "Top-k Fisher channel selection.",
        "datasets": [{"name": "ImageNet"}],
    }
    q = build_query("01_intro", experiment=exp)
    assert "fisher" not in q.lower()  # only pulled for 03_method / 04_experiments
    assert "imagenet" not in q.lower()


# ----------------------------------------------------- LibraryRetriever


def test_retriever_empty_library(project):
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    assert len(retriever) == 0
    assert retriever.retrieve("anything") == []


def test_retriever_ranks_relevant_paper_higher(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Channel pruning for EEG decoding under cross-subject shift",
         "abstract": "We propose Fisher score channel selection.", "authors": ["A"]},
        {"arxiv_id": "p2", "title": "Efficient transformer for image classification",
         "abstract": "Vision transformer on ImageNet.", "authors": ["B"]},
        {"arxiv_id": "p3", "title": "Self-supervised pretraining for vision-language tasks",
         "abstract": "Contrastive learning over web data.", "authors": ["C"]},
    ])
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    hits = retriever.retrieve("channel pruning EEG cross-subject", k=3)
    assert hits, "expected at least one hit"
    # Paper 1 is the relevant one — must rank first.
    assert hits[0].cite_key == "arxiv_p1"


def test_retriever_returns_match_reason_and_snippet(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Sparse self-attention for long context",
         "abstract": "We propose a sparse pattern that retains accuracy with O(n) cost.",
         "authors": ["A"]},
    ])
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    hits = retriever.retrieve("self-attention long context sparse", k=1)
    assert hits
    assert "self-attention" in hits[0].match_reason or "sparse" in hits[0].match_reason
    assert hits[0].snippet  # non-empty


def test_retriever_uses_summary_yaml_when_present(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "An obscure paper", "abstract": "Vague.", "authors": ["A"]},
    ])
    _write_summary(str(project), "arxiv_p1", {
        "problem": "Cross-subject motor imagery decoding fails because of channel mismatch.",
        "method": "We use Fisher discriminant scores to rank EEG channels.",
        "key_results": ["Beats CSP by 3% balanced accuracy."],
        "limitations": [],
        "techniques": ["Fisher score"],
    })
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    hits = retriever.retrieve("Fisher motor imagery channel", k=1)
    assert hits
    assert hits[0].cite_key == "arxiv_p1"
    # The match should come from the summary yaml since the paper's title/abstract are vague.
    assert "fisher" in hits[0].match_reason or "channel" in hits[0].match_reason


def test_retriever_mmr_picks_diverse_paper_at_low_lambda(project):
    # 3 highly duplicate papers + 1 diverse paper that still matches the
    # query token "neural". With ``mmr_lambda=1.0`` (pure BM25) all three
    # duplicates should fill the top slots; with ``mmr_lambda=0.0`` (max
    # diversity) the diverse paper must land in top-2 because picking a
    # second near-duplicate would carry near-zero marginal value.
    # NB: BM25 is unstable on tiny corpora (every term's IDF is negative),
    # so we keep the assertion to "MMR brings in the diverse paper" rather
    # than comparing two MMR settings head-to-head.
    _add_papers(str(project), [
        {"arxiv_id": "d1", "title": "Sparse self-attention for long sequences",
         "abstract": "Sparse pattern over neural transformer attention.",
         "authors": ["Smith"]},
        {"arxiv_id": "d2", "title": "Sparse self-attention for long context",
         "abstract": "Sparse pattern over neural transformer attention.",
         "authors": ["Jones"]},
        {"arxiv_id": "d3", "title": "Sparse self-attention efficient training",
         "abstract": "Sparse pattern over neural transformer attention.",
         "authors": ["Brown"]},
        {"arxiv_id": "diverse",
         "title": "Hardware accelerator for neural network inference",
         "abstract": "FPGA-based neural inference accelerator.",
         "authors": ["Wang"]},
    ])
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    hits = retriever.retrieve(
        "sparse self-attention neural transformer attention",
        k=2,
        mmr_lambda=0.0,
    )
    keys = {h.cite_key for h in hits}
    assert "arxiv_diverse" in keys, (
        f"low-lambda MMR should diversify away from duplicates; got {keys}"
    )


def test_retriever_empty_query_returns_empty(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Some real paper title",
         "abstract": "Substantial content here.", "authors": ["Smith"]},
    ])
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    assert retriever.retrieve("") == []
    assert retriever.retrieve("   ") == []


def test_retriever_zero_k_returns_empty(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Some real paper title",
         "abstract": "Substantial content here.", "authors": ["Smith"]},
    ])
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    assert retriever.retrieve("anything", k=0) == []


def test_retriever_query_with_no_matches_returns_empty(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Apple banana cherry", "abstract": "fruit",
         "authors": ["A"]},
    ])
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    # Tokens that don't appear at all in any paper
    hits = retriever.retrieve("zebra unicorn", k=5)
    assert hits == []


# ----------------------------------------------------- MCP wrapper


def test_tool_explicit_query(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Channel pruning for EEG decoding",
         "abstract": "Subset selection of electrodes.", "authors": ["Smith"]},
    ])
    res = library_retrieve_tool(str(project), query="channel pruning EEG", k=5)
    assert res.get("error") is None
    assert res["library_size"] == 1
    assert res["query"] == "channel pruning EEG"
    assert res["hits"][0]["cite_key"] == "arxiv_p1"


def test_tool_section_targeted_builds_query(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Channel pruning for EEG decoding",
         "abstract": "Subset selection of electrodes.", "authors": ["Smith"]},
    ])
    # No paper_plan, no idea, no experiment — section hint alone.
    res = library_retrieve_tool(str(project), section="01_intro", k=5)
    assert res.get("error") is None
    assert "introduction" in res["query"] or "motivation" in res["query"]


def test_tool_empty_library_errors(project):
    res = library_retrieve_tool(str(project), query="anything")
    assert res["error"] == "library_empty"


def test_tool_no_query_no_section_errors(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Some real title",
         "abstract": "Substantial content.", "authors": ["Smith"]},
    ])
    res = library_retrieve_tool(str(project))
    assert res["error"] == "no_query"


def test_tool_unknown_project_errors(tmp_path):
    res = library_retrieve_tool(str(tmp_path / "nope"), query="x")
    assert res["error"] == "project_not_initialized"


# ----------------------------------------------------- chunk-level grounding (P0 #1)


def _seed_chunks(project_dir, cite_key: str, md_text: str):
    """Build & persist a chunk index for ``cite_key`` from ``md_text``."""
    from paic.library.chunker import chunk_index_build
    chunk_index_build(resolve_project(str(project_dir)), cite_key, md_text)


def test_retrieve_chunks_per_paper_attaches_top_passages(project):
    """When chunks_per_paper > 0 and chunks exist, hits include the top
    chunk passages for each paper — not just a snippet from selected.yaml."""
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Channel pruning for EEG decoding",
         "abstract": "Subset selection of electrodes.", "authors": ["A"]},
    ])
    _seed_chunks(str(project), "arxiv_p1", (
        "# Method\n\n"
        "We rank EEG channels by Fisher discriminant scores. The top-k channels "
        "are kept and the rest masked.\n\n"
        "# Results\n\n"
        "On BCI-IV-2a the pruned model retains 97% accuracy with 30% of channels.\n"
    ))
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    assert retriever.chunk_count > 0
    hits = retriever.retrieve("Fisher channel ranking", k=5, chunks_per_paper=2)
    assert hits
    # The paper's chunks should be attached.
    assert hits[0].chunks, "expected chunks attached to hit"
    chunk_texts = " ".join(c.text for c in hits[0].chunks)
    assert "Fisher" in chunk_texts


def test_retrieve_default_no_chunks_attached(project):
    """Default behavior unchanged — chunks_per_paper=0 keeps the empty list."""
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Channel pruning",
         "abstract": "Some content.", "authors": ["A"]},
    ])
    _seed_chunks(str(project), "arxiv_p1", "# X\n\nFoo bar baz.\n")
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    hits = retriever.retrieve("channel pruning", k=5)
    assert hits
    assert hits[0].chunks == []


def test_retrieve_chunks_empty_when_no_chunk_index(project):
    """Older projects without chunks/ — chunks_per_paper=N still returns
    empty chunks per hit, retrieval doesn't crash."""
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "Channel pruning",
         "abstract": "Some content.", "authors": ["A"]},
    ])
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    hits = retriever.retrieve("channel pruning", k=5, chunks_per_paper=3)
    assert hits
    assert hits[0].chunks == []


def test_retrieve_chunks_ranks_relevant_chunk_first(project):
    """Within a paper, the chunk most relevant to the query should rank first."""
    _add_papers(str(project), [
        {"arxiv_id": "p1",
         "title": "Fisher-score channel ranking for EEG decoding",
         "abstract": "Channel ranking via Fisher discriminant scores.",
         "authors": ["A"]},
    ])
    _seed_chunks(str(project), "arxiv_p1", (
        "# Background\n\n"
        "Generic background that mentions baselines and prior pruning approaches.\n\n"
        "# Method\n\n"
        "Our specific Fisher-score channel ranking algorithm operates per-trial.\n"
    ))
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    hits = retriever.retrieve("Fisher channel ranking", k=1, chunks_per_paper=2)
    assert hits
    assert hits[0].chunks
    # The Method chunk (mentioning Fisher / channel / ranking) should rank above Background.
    top = hits[0].chunks[0]
    assert "Fisher" in top.text


def test_chunk_count_aggregates_across_papers(project):
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "A", "abstract": "x", "authors": ["A"]},
        {"arxiv_id": "p2", "title": "B", "abstract": "y", "authors": ["B"]},
    ])
    _seed_chunks(str(project), "arxiv_p1", "# A\n\nfoo bar.\n")
    _seed_chunks(str(project), "arxiv_p2", "# B\n\nbaz qux.\n")
    retriever = LibraryRetriever.build(resolve_project(str(project)))
    assert retriever.chunk_count == 2


def test_reindex_chunks_tool_walks_library(project):
    """The MCP wrapper builds chunk indices for every paper with a markdown body."""
    from paic.mcp_server.tools.library import library_reindex_chunks_tool
    paths = resolve_project(str(project))
    _add_papers(str(project), [
        {"arxiv_id": "p1", "title": "A", "abstract": "x", "authors": ["A"]},
        {"arxiv_id": "p2", "title": "B", "abstract": "y", "authors": ["B"]},
    ])
    # Stage one markdown body in pdfs/.
    paths.pdfs_dir.mkdir(parents=True, exist_ok=True)
    (paths.pdfs_dir / "arxiv_p1.md").write_text(
        "# Intro\n\nSome paper body content.\n", encoding="utf-8",
    )
    res = library_reindex_chunks_tool(str(project))
    assert res.get("error") is None
    assert res["library_count"] == 2
    assert len(res["indexed"]) == 1
    assert res["indexed"][0]["cite_key"] == "arxiv_p1"
    assert any(s["cite_key"] == "arxiv_p2" and s["reason"] == "no_markdown_body"
               for s in res["skipped"])


def test_reindex_chunks_tool_empty_library_errors(project):
    from paic.mcp_server.tools.library import library_reindex_chunks_tool
    res = library_reindex_chunks_tool(str(project))
    assert res["error"] == "library_empty"

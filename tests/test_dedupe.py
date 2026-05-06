"""Dedupe tests — Phase 3."""

from paic.schemas.paper import PaperRef
from paic.sources.dedupe import dedupe


def _ref(**kwargs) -> PaperRef:
    kwargs.setdefault("title", "x")
    return PaperRef(**kwargs)


def test_exact_arxiv_id_match_collapses():
    a = _ref(arxiv_id="2401.12345", title="Foo (v1)")
    b = _ref(arxiv_id="2401.12345", title="Foo (v2)")
    res = dedupe([a, b])
    assert len(res.unique) == 1
    assert res.duplicate_groups == [[0, 1]]


def test_exact_doi_match_collapses_across_sources():
    a = _ref(doi="10.1234/x", title="Title A", source="s2")
    b = _ref(doi="10.1234/X", title="Title B", source="manual")  # case-insensitive
    res = dedupe([a, b])
    assert len(res.unique) == 1


def test_fuzzy_title_match_collapses():
    a = _ref(title="A Survey on Diffusion Models for Video Generation")
    b = _ref(title="A Survey on Diffusion Models for Video Generation.")
    res = dedupe([a, b])
    assert len(res.unique) == 1


def test_distinct_papers_preserved_in_order():
    refs = [
        _ref(arxiv_id="1", title="Paper One"),
        _ref(arxiv_id="2", title="Paper Two"),
        _ref(arxiv_id="3", title="Paper Three"),
    ]
    res = dedupe(refs)
    assert [p.arxiv_id for p in res.unique] == ["1", "2", "3"]
    assert res.duplicate_groups == []


def test_transitive_clustering_through_id_and_title():
    # a == b by arxiv id; b ~~ c by fuzzy title -> all three collapse.
    a = _ref(arxiv_id="2401.0001", title="Attention Is All You Need")
    b = _ref(arxiv_id="2401.0001", title="Attention Is All You Need (Extended)")
    c = _ref(arxiv_id="2401.9999", title="Attention Is All You Need!!!")
    res = dedupe([a, b, c])
    assert len(res.unique) == 1
    assert res.duplicate_groups == [[0, 1, 2]]

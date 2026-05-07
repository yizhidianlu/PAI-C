"""Tests for paic.mcp_server.tools.search_recall — title-hit tiering.

Pure logic, no I/O. Validates four-tier classification, strict vs loose mode,
tier1_min floor promotion, word-boundary correctness, multi-word phrase
matching, and edge cases (empty inputs, Unicode, duplicate terms).
"""

from __future__ import annotations

import pytest

from paic.mcp_server.tools.search_recall import (
    search_recall_check,
    search_recall_check_tool,
)
from paic.schemas.paper import PaperRef


def _ref(title: str, idx: int = 0) -> PaperRef:
    return PaperRef(title=title, arxiv_id=f"2401.{idx:05d}", source="manual")


def _idx_set(bucket: list[dict]) -> set[int]:
    return {p["idx"] for p in bucket}


def _hits_for(bucket: list[dict], idx: int) -> list[str]:
    for p in bucket:
        if p["idx"] == idx:
            return list(p["hits"])
    raise AssertionError(f"idx {idx} not in bucket")


# ---------------------------------------------------------- core tiering


def test_full_hit_lands_in_tier1_strict():
    papers = [_ref("Motor Imagery EEG Channel Selection via Attention", 0)]
    res = search_recall_check(papers, ["motor imagery", "EEG", "channel selection"])

    assert _idx_set(res["tier1_strict"]) == {0}
    assert sorted(_hits_for(res["tier1_strict"], 0)) == sorted(
        ["channel selection", "eeg", "motor imagery"]
    )
    assert res["stats"]["t1_strict"] == 1


def test_n_minus_one_hit_lands_in_tier1_loose_when_n_geq_3():
    # Title hits 2 of 3 terms — loose should promote to T1_loose.
    papers = [_ref("Motor Imagery EEG Decoding", 0)]
    res = search_recall_check(
        papers,
        ["motor imagery", "EEG", "channel selection"],
        tier1_min=0,  # disable floor so we can isolate loose promotion
        match_mode="loose",
    )

    assert _idx_set(res["tier1_loose"]) == {0}
    assert _idx_set(res["tier1_strict"]) == set()
    assert _idx_set(res["tier2_partial"]) == set()


def test_strict_mode_does_not_relax_to_loose():
    papers = [_ref("Motor Imagery EEG Decoding", 0)]
    res = search_recall_check(
        papers,
        ["motor imagery", "EEG", "channel selection"],
        tier1_min=0,
        match_mode="strict",
    )

    assert _idx_set(res["tier1_strict"]) == set()
    assert _idx_set(res["tier1_loose"]) == set()
    assert _idx_set(res["tier2_partial"]) == {0}


def test_two_term_query_loose_equals_strict():
    # N=2 < 3 → loose disabled, falls through to T2_partial.
    papers = [_ref("Diffusion Models Survey", 0)]
    res = search_recall_check(
        papers, ["diffusion", "video"], tier1_min=0, match_mode="loose"
    )

    assert _idx_set(res["tier1_loose"]) == set()
    assert _idx_set(res["tier2_partial"]) == {0}


# ---------------------------------------------------------- multi-word


def test_multiword_phrase_requires_contiguous_match():
    p_hit = _ref("Motor Imagery Decoding from EEG", 0)
    p_miss = _ref("Motor Performance and Visual Imagery in BCI", 1)
    res = search_recall_check([p_hit, p_miss], ["motor imagery"], tier1_min=0)

    assert _idx_set(res["tier1_strict"]) == {0}
    assert _idx_set(res["tier3_others"]) == {1}


# ---------------------------------------------------------- word boundary


def test_word_boundary_blocks_substring_inside_other_word():
    p_pos = _ref("EEG-Based Classification", 0)
    p_neg = _ref("Studies on PEEGO Synthesis", 1)
    res = search_recall_check([p_pos, p_neg], ["EEG"], tier1_min=0)

    assert _idx_set(res["tier1_strict"]) == {0}
    assert _idx_set(res["tier3_others"]) == {1}


def test_punctuation_around_term_still_matches():
    # "Diffusion-Based" should still match the term "diffusion".
    papers = [_ref("Diffusion-Based Video Generation", 0)]
    res = search_recall_check(papers, ["diffusion"], tier1_min=0)
    assert _idx_set(res["tier1_strict"]) == {0}


def test_case_insensitive():
    papers = [_ref("DIFFUSION Models for VIDEO", 0)]
    res = search_recall_check(papers, ["diffusion", "video"], tier1_min=0)
    assert _idx_set(res["tier1_strict"]) == {0}


# ---------------------------------------------------------- floor promotion


def test_tier1_min_floor_promotes_top_t2():
    # 0 strict hits, 0 loose-eligible (N=2). T2 has 2 papers; tier1_min=2
    # should promote both up.
    papers = [
        _ref("Diffusion Models for Image Generation", 0),
        _ref("Video Tokenization Survey", 1),
        _ref("Unrelated Quantum Computing Paper", 2),
    ]
    res = search_recall_check(
        papers, ["diffusion", "video"], tier1_min=2, match_mode="loose"
    )

    assert len(res["tier1_loose"]) == 2
    assert _idx_set(res["tier1_loose"]) == {0, 1}
    assert _idx_set(res["tier3_others"]) == {2}


def test_tier1_min_floor_does_not_promote_zero_hits():
    # Paper 0 hits all 3 → strict. Paper 1 hits 2 of 3 → loose. Paper 2 hits 0
    # → others. Floor is 5 but no T2 hits exist to promote — T1 stays at 2.
    papers = [
        _ref("Motor Imagery EEG Channel Selection", 0),
        _ref("EEG Motor Imagery Channel Pruning", 1),
        _ref("Diffusion Survey", 2),
    ]
    res = search_recall_check(
        papers,
        ["motor imagery", "EEG", "channel selection"],
        tier1_min=5,
    )
    assert _idx_set(res["tier1_strict"]) == {0}
    assert _idx_set(res["tier1_loose"]) == {1}
    assert _idx_set(res["tier2_partial"]) == set()
    assert _idx_set(res["tier3_others"]) == {2}


# ---------------------------------------------------------- edge cases


def test_empty_core_terms_routes_all_to_t3():
    papers = [_ref("Anything Goes Here", 0), _ref("Another Title", 1)]
    res = search_recall_check(papers, [], tier1_min=5)

    assert _idx_set(res["tier3_others"]) == {0, 1}
    assert res["stats"]["core_term_count"] == 0


def test_empty_papers_yields_empty_buckets():
    res = search_recall_check([], ["motor imagery"], tier1_min=5)
    assert res["stats"]["total"] == 0
    assert res["tier1_strict"] == []
    assert res["tier1_loose"] == []
    assert res["tier2_partial"] == []
    assert res["tier3_others"] == []


def test_duplicate_core_terms_deduplicated():
    papers = [_ref("Motor Imagery EEG", 0)]
    res = search_recall_check(
        papers,
        ["motor imagery", "Motor Imagery", "MOTOR IMAGERY"],
    )
    assert res["stats"]["core_term_count"] == 1
    assert _idx_set(res["tier1_strict"]) == {0}


def test_unicode_title_does_not_crash():
    papers = [_ref("脑电信号的 Motor Imagery 分析 🧠", 0)]
    res = search_recall_check(papers, ["motor imagery"], tier1_min=0)
    assert _idx_set(res["tier1_strict"]) == {0}


# ---------------------------------------------------------- tool wrapper


def test_tool_wrapper_accepts_raw_dicts():
    raw = [
        {
            "title": "Motor Imagery EEG Channel Selection",
            "arxiv_id": "2401.00001",
            "source": "manual",
        }
    ]
    res = search_recall_check_tool(
        raw,
        ["motor imagery", "EEG", "channel selection"],
    )
    assert _idx_set(res["tier1_strict"]) == {0}


def test_tool_wrapper_rejects_invalid_match_mode():
    with pytest.raises(ValueError, match="match_mode"):
        search_recall_check_tool([], ["x"], match_mode="fuzzy")

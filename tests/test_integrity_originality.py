"""Tests for the originality / plagiarism scan (ARS-fusion P3-1)."""

from __future__ import annotations

import pytest

from paic.integrity.originality import (
    DEFAULT_SHINGLE_K,
    KIND_CLOSE_MATCH,
    KIND_PARAPHRASE,
    KIND_VERBATIM,
    ORIGINALITY_KINDS,
    THRESHOLD_CLOSE_MATCH,
    THRESHOLD_PARAPHRASE,
    THRESHOLD_VERBATIM,
    _jaccard,
    _shingle_set,
    run_originality_check,
)
from paic.integrity.runner import run_integrity_check
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    paths = resolve_project(str(project_dir))
    return paths


def _write_section(paths, name, body):
    sections_dir = paths.drafts_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    (sections_dir / f"{name}.tex").write_text(body, encoding="utf-8")


def _write_summary(paths, cite_key, body):
    paths.summaries_dir.mkdir(parents=True, exist_ok=True)
    (paths.summaries_dir / f"{cite_key}.md").write_text(body, encoding="utf-8")


# ---------------------------------------------------- shingle / jaccard


def test_shingle_set_empty_for_short_text():
    assert _shingle_set("only a few words", k=DEFAULT_SHINGLE_K) == frozenset()


def test_shingle_set_deterministic():
    text = "the quick brown fox jumps over the lazy dog repeatedly today"
    a = _shingle_set(text, k=4)
    b = _shingle_set(text, k=4)
    assert a == b
    # Re-pasted with extra whitespace / case differences should hash to
    # the same shingles after normalisation.
    text2 = "  THE  QUICK  Brown FOX jumps  over the lazy dog repeatedly today "
    c = _shingle_set(text2, k=4)
    assert a == c


def test_shingle_set_strips_latex_commands():
    """LaTeX command tokens (\\section, \\cite) shouldn't bias shingle similarity.

    Brace contents stay (\\section{Method} legitimately carries the word
    'Method' as part of the prose), so the only difference between the
    two shingle sets is the noise from the brace argument 'X'. We assert
    near-equivalence (≥ 80% jaccard) rather than strict equality.
    """
    a = _shingle_set("we propose a method that uses attention layers in a network", k=4)
    b = _shingle_set("\\section{X}we propose a method that uses attention layers in a network", k=4)
    assert _jaccard(a, b) >= 0.80, f"jaccard={_jaccard(a, b)}"


def test_jaccard_self_is_one():
    s = _shingle_set("a b c d e f g h i j k l m n", k=4)
    assert _jaccard(s, s) == 1.0


def test_jaccard_disjoint_is_zero():
    a = _shingle_set("a b c d e f g h i j", k=4)
    b = _shingle_set("z y x w v u t s r q", k=4)
    assert _jaccard(a, b) == 0.0


# ---------------------------------------------------- end-to-end


def test_originality_flags_verbatim_paragraph(project):
    verbatim_text = (
        "Multi-head attention layers learn to attend to different parts of "
        "the sequence in parallel and project the resulting representations "
        "into a unified output space producing the final attended vector."
    )
    _write_summary(project, "vaswani_2017", verbatim_text + "\nThis paper introduces transformers.")
    _write_section(
        project,
        "01_intro",
        "\\section{Intro}\n\n"
        + verbatim_text
        + "\n\nWe extend this idea by adding cross-modal alignment."
    )
    issues = run_originality_check(project, mode="originality")
    verbatim = [i for i in issues if i.kind == KIND_VERBATIM]
    assert verbatim, f"expected VERBATIM, got {[i.kind for i in issues]}"
    assert verbatim[0].severity == "blocker"


def test_originality_flags_close_match(project):
    """Synonym swaps but identical structure → CLOSE_MATCH (or VERBATIM
    if many overlapping shingles remain).

    Threshold tuning is heuristic — this test asserts that some
    significant signal is emitted, not the exact verdict bin.
    """
    base = (
        "neural networks trained with stochastic gradient descent converge to "
        "local minima depending on the initialisation and the learning-rate "
        "schedule selected by the practitioner during training"
    )
    swap = base.replace("neural", "deep").replace("trained", "optimised")
    _write_summary(project, "src_2020", base)
    _write_section(project, "02_related", swap + "\n\nConclusion paragraph here adds a different observation.")
    issues = run_originality_check(project, mode="originality")
    assert any(i.kind in {KIND_VERBATIM, KIND_CLOSE_MATCH} for i in issues), [i.kind for i in issues]


def test_originality_no_issue_for_truly_original_text(project):
    _write_summary(project, "src_2020", "The literature reports a stark divergence in baseline accuracy.")
    _write_section(
        project,
        "01_intro",
        "Our contribution is a fresh framing of long-context attention as a "
        "graph-pruning problem under bounded memory budgets.\n\n"
        "We empirically verify the framing on three benchmarks."
    )
    issues = run_originality_check(project, mode="originality")
    # May get info-level "no source corpus" if summaries empty etc., but
    # no VERBATIM / CLOSE_MATCH for genuinely independent prose.
    assert not any(i.kind in {KIND_VERBATIM, KIND_CLOSE_MATCH} for i in issues)


def test_originality_skips_short_paragraphs(project):
    """Paragraphs under MIN_PARAGRAPH_CHARS (80 chars) are ignored."""
    _write_summary(project, "src_2020", "This is a sample summary of an arbitrary paper that explains something useful in detail with enough content to compare against drafts later.")
    _write_section(project, "01_intro", "Short paragraph.\n\nAnother short one.")
    issues = run_originality_check(project, mode="originality")
    assert not any(i.kind in {KIND_VERBATIM, KIND_CLOSE_MATCH, KIND_PARAPHRASE} for i in issues)


def test_originality_emits_info_when_no_corpus(project):
    """No summaries / chunks → single info-level note, not a hard error."""
    _write_section(
        project,
        "01_intro",
        "Some content here that is at least eighty characters long to get past "
        "the MIN_PARAGRAPH_CHARS threshold for being considered."
    )
    issues = run_originality_check(project, mode="originality")
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert "skipped" in issues[0].detail.lower()


def test_originality_sample_rate_caps_paragraph_count(project):
    """Pre-review sample rate (default 30%) checks fewer paragraphs than
    full mode. We verify by counting issues on a draft full of duplicates."""
    src_text = (
        "the network is trained for one hundred epochs on the held-out validation "
        "split with early stopping on plateau detection over consecutive evaluation rounds"
    )
    _write_summary(project, "src_2020", src_text)
    body = "\n\n".join([src_text + f" iteration {i}." for i in range(30)])
    _write_section(project, "04_experiments", body)

    full = run_originality_check(project, mode="originality")
    sampled = run_originality_check(project, mode="pre_review")
    assert len(sampled) <= len(full)
    assert len(sampled) >= 1  # some signal still surfaces


# ---------------------------------------------------- runner integration


def test_runner_originality_only_mode(project):
    """run_integrity_check(mode='originality') bypasses S2 + AI judge."""
    _write_summary(project, "src_2020", "A summary of the prior work used in this paper for grounding.")
    _write_section(
        project,
        "01_intro",
        "Independent intro paragraph that does not match the summary in any way to keep the test deterministic."
    )
    result = run_integrity_check(project, mode="originality")
    assert result.mode == "originality"
    assert result.s2_verified_count == 0
    assert result.s2_failed_count == 0
    assert result.pending_websearch == []
    assert result.pending_ai_judge == []


def test_runner_originality_disabled_in_pre_review_by_default(project):
    """pre_review mode does NOT run originality unless opt-in."""
    src = (
        "the network is trained on imagenet using sgd with momentum and a "
        "cosine learning-rate schedule for one hundred epochs total"
    )
    _write_summary(project, "src_2020", src)
    _write_section(project, "04_experiments", src + "\n\nWe report top-1 accuracy on the test split.")

    # Default pre_review — no originality
    out = run_integrity_check(
        project,
        mode="pre_review",
        s2_search_fn=lambda q, limit=5: type("R", (), {"papers": []})(),
    )
    assert not any(i.kind in ORIGINALITY_KINDS for i in out.issues)

    # Opt-in — originality issues appear
    out2 = run_integrity_check(
        project,
        mode="pre_review",
        s2_search_fn=lambda q, limit=5: type("R", (), {"papers": []})(),
        originality_enabled=True,
        originality_sample_rate=1.0,
    )
    # When verbatim copy present, expect ≥ 1 originality finding
    originality_findings = [i for i in out2.issues if i.kind in ORIGINALITY_KINDS]
    assert originality_findings, [i.kind for i in out2.issues]


def test_originality_kinds_disjoint_from_other_namespaces():
    """Originality kinds must not collide with citation / AI-failure kinds."""
    from paic.integrity.types import (
        AI_FAILURE_MODE_KINDS,
        HALLUCINATION_KINDS,
        ORIGINALITY_KINDS,
    )
    overlap = (
        set(ORIGINALITY_KINDS) & set(HALLUCINATION_KINDS)
        | set(ORIGINALITY_KINDS) & set(AI_FAILURE_MODE_KINDS)
    )
    assert overlap == set()


def test_thresholds_monotonically_decreasing():
    assert THRESHOLD_VERBATIM > THRESHOLD_CLOSE_MATCH > THRESHOLD_PARAPHRASE

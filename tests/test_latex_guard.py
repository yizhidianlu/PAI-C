"""LaTeX guard tests — §20."""

from __future__ import annotations

from paic.latex.guard import (
    begin_end_balanced,
    brace_balanced,
    cite_keys_in_library,
    cite_keys_preserved,
    extract_cite_keys,
    strip_markdown_fence,
    validate_composed,
    validate_polished,
)


# ---------------------------------------------------------------- cite keys
def test_extract_cite_keys_basic():
    text = r"As shown by \cite{smith2024}, this matters."
    assert extract_cite_keys(text)["smith2024"] == 1


def test_extract_cite_keys_variants():
    text = (
        r"\citet{a} and \citep{b} and \citep[p.~5]{c} and \citeauthor*{d} "
        r"and \citeyear{e}."
    )
    keys = extract_cite_keys(text)
    assert keys["a"] == 1
    assert keys["b"] == 1
    assert keys["c"] == 1
    assert keys["d"] == 1
    assert keys["e"] == 1


def test_extract_cite_keys_comma_list():
    r"""\cite{a,b,c} -> 3 separate keys, each counted once."""
    keys = extract_cite_keys(r"see \cite{a, b, c} for details")
    assert keys["a"] == 1
    assert keys["b"] == 1
    assert keys["c"] == 1


def test_extract_cite_keys_repeated():
    keys = extract_cite_keys(r"\cite{a} and later again \cite{a}")
    assert keys["a"] == 2


def test_cite_keys_preserved_subset_passes():
    """Polished may drop a cite (consolidation) but not add one."""
    original = r"\cite{a} foo \cite{b} bar \cite{c}"
    polished = r"\cite{a} foo bar \cite{c}"  # dropped b, kept a/c
    assert cite_keys_preserved(original, polished) is True


def test_cite_keys_preserved_added_fails():
    original = r"\cite{a}"
    polished = r"\cite{a} and \cite{newauthor2024}"
    assert cite_keys_preserved(original, polished) is False


def test_cite_keys_preserved_empty_original_no_new_cites():
    """If original had no cites, polished may not introduce any."""
    assert cite_keys_preserved("Plain text.", r"now \cite{novel}") is False


def test_cite_keys_preserved_empty_both():
    assert cite_keys_preserved("Plain.", "Cleaner.") is True


# ---------------------------------------------------------------- begin/end
def test_begin_end_balanced_basic():
    text = r"\begin{equation} x = 1 \end{equation}"
    assert begin_end_balanced(text) is True


def test_begin_end_balanced_nested():
    text = r"\begin{figure} \begin{center} foo \end{center} \end{figure}"
    assert begin_end_balanced(text) is True


def test_begin_end_unmatched_fails():
    text = r"\begin{equation} x = 1"
    assert begin_end_balanced(text) is False


def test_begin_end_wrong_close_fails():
    text = r"\begin{equation} x \end{align}"
    assert begin_end_balanced(text) is False


def test_begin_end_empty_passes():
    assert begin_end_balanced("just plain prose") is True


# ---------------------------------------------------------------- braces
def test_brace_balanced_basic():
    assert brace_balanced("{a} {b} {c}") is True


def test_brace_unbalanced_fails():
    assert brace_balanced("{a} {b") is False


def test_brace_escaped_literal_ignored():
    """\\{ and \\} are escaped literals, shouldn't count."""
    assert brace_balanced(r"text with \{ literal brace") is True


def test_brace_inside_verb_inline_ignored():
    text = r"use \verb|{| then a \section{Real}"
    assert brace_balanced(text) is True


def test_brace_inside_verbatim_block_ignored():
    text = (
        "Real {}\n"
        r"\begin{verbatim}"
        "\n{ unbalanced inside verbatim }\n"
        r"\end{verbatim}"
        "\n"
    )
    assert brace_balanced(text) is True


def test_brace_in_comment_ignored():
    text = "%% this { is a comment with no close\n\\section{Real}\n"
    assert brace_balanced(text) is True


# ---------------------------------------------------------------- validate_polished
def test_validate_polished_ok():
    original = r"Old phrasing \cite{smith}."
    polished = r"Better phrasing \cite{smith}."
    report = validate_polished(original, polished)
    assert report.ok is True
    assert report.cite_keys_preserved is True
    assert report.begin_end_balanced is True
    assert report.brace_balanced is True


def test_validate_polished_bad_cite():
    report = validate_polished(r"\cite{a}", r"\cite{a} \cite{b}")
    assert report.ok is False
    assert report.cite_keys_preserved is False


def test_validate_polished_bad_structure():
    report = validate_polished(
        r"\begin{equation} x \end{equation}",
        r"\begin{equation} x",
    )
    assert report.ok is False
    assert report.begin_end_balanced is False


def test_validate_polished_warns_on_huge_shrinkage():
    original = "X " * 200  # 400 chars
    polished = "tiny."
    report = validate_polished(original, polished)
    assert any("less than" in w or "<30%" in w for w in report.warnings)


# ---------------------------------------------------------------- markdown fence
def test_strip_fence_leading_and_trailing():
    text = "```latex\n\\section{Foo} body\n```\n"
    assert strip_markdown_fence(text).strip() == "\\section{Foo} body"


def test_strip_fence_only_leading():
    text = "```\n\\section{X}"
    assert "```" not in strip_markdown_fence(text)


def test_strip_fence_no_fence_unchanged():
    text = "\\section{X}\nbody\n"
    assert strip_markdown_fence(text) == text


def test_strip_fence_inner_fence_left_alone():
    """A fence in the middle of the content is content, not framing."""
    text = "intro\n```python\nx = 1\n```\nmore"
    assert strip_markdown_fence(text) == text


# ---------------------------------------------------------------- §21 compose guard
def test_cite_keys_in_library_subset_passes():
    library = {"arxiv_a", "doi_b", "s2_c"}
    text = r"As shown by \cite{arxiv_a} and \cite{doi_b}."
    valid, missing = cite_keys_in_library(text, library)
    assert valid is True
    assert missing == []


def test_cite_keys_in_library_unknown_fails_lists_missing():
    library = {"arxiv_a", "doi_b"}
    text = r"\cite{arxiv_a} \cite{novel2099} \cite{another_fake}"
    valid, missing = cite_keys_in_library(text, library)
    assert valid is False
    assert missing == ["another_fake", "novel2099"]


def test_cite_keys_in_library_no_cites_passes():
    """A section with no cites passes regardless of library content."""
    valid, missing = cite_keys_in_library("Plain prose.", {"arxiv_a"})
    assert valid is True
    assert missing == []


def test_cite_keys_in_library_empty_library_with_no_cites_passes():
    """Empty library is fine when the text has no cites either."""
    valid, missing = cite_keys_in_library("No cites here.", set())
    assert valid is True


def test_cite_keys_in_library_empty_library_with_cite_fails():
    """Empty library + any cite → fail."""
    valid, missing = cite_keys_in_library(r"\cite{anything}", set())
    assert valid is False
    assert "anything" in missing


def test_validate_composed_ok():
    library = {"arxiv_a", "doi_b"}
    text = (
        r"\section{Related Work}" "\n"
        r"Foo \cite{arxiv_a}. \begin{equation} x = 1 \end{equation} "
        r"Bar \cite{doi_b}." + "\n" * 10 + "More content here. " * 30
    )
    report = validate_composed(text, library)
    assert report.ok is True


def test_validate_composed_unknown_cite_fails():
    library = {"arxiv_a"}
    text = (
        r"\section{X}" + " padding " * 50 + "\n" + r"\cite{unknown_paper}"
    )
    report = validate_composed(text, library)
    assert report.ok is False
    assert "unknown_paper" in report.cite_keys_missing


def test_validate_composed_short_warns():
    """Composed sections under 200 chars get a length warning."""
    report = validate_composed("tiny", set())
    assert any("short" in w.lower() for w in report.warnings)

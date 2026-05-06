"""LaTeX structural validation for ``paic_draft_polish`` — §20.

LLM-rewritten LaTeX can subtly break structure: silently swapping a cite
key, dropping a closing brace, leaving an unbalanced ``\\begin{equation}``.
The polish pipeline runs every candidate through these checks before
overwriting the original — failure → return ``latex_validation_failed``,
keep the user's file intact, surface the issue.

Scope intentionally narrow: cite-key preservation, ``\\begin{}/\\end{}``
balance, brace balance outside verbatim. Nothing fancy like AST parsing —
we just want to catch the obvious sins.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Matches \cite{KEY}, \citet{KEY}, \citep[opts]{KEY}, \citeauthor*{KEY}, …
# Captures the key list (may be comma-separated: \cite{a,b,c}).
_CITE_PATTERN = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")

# Matches \begin{NAME} and \end{NAME} (NAME = letters + optional star).
_BEGIN_PATTERN = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
_END_PATTERN = re.compile(r"\\end\{([a-zA-Z*]+)\}")

# Comment lines (LaTeX comments start with % unless preceded by \).
_COMMENT_PATTERN = re.compile(r"(?<!\\)%[^\n]*")
# verbatim / lstlisting blocks where braces don't count.
_VERBATIM_BLOCK_PATTERN = re.compile(
    r"\\begin\{(verbatim|lstlisting|minted)\*?\}.*?\\end\{\1\*?\}",
    re.DOTALL,
)
# \verb|...|  — single-char delimiter, content opaque.
_VERB_INLINE_PATTERN = re.compile(r"\\verb\*?(.)(.*?)\1", re.DOTALL)


@dataclass(frozen=True)
class ValidationReport:
    cite_keys_preserved: bool
    begin_end_balanced: bool
    brace_balanced: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.cite_keys_preserved
            and self.begin_end_balanced
            and self.brace_balanced
        )

    def to_dict(self) -> dict:
        return {
            "cite_keys_preserved": self.cite_keys_preserved,
            "begin_end_balanced": self.begin_end_balanced,
            "brace_balanced": self.brace_balanced,
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


def extract_cite_keys(text: str) -> Counter:
    """Return a ``Counter`` of every cite key referenced in ``text``.

    ``\\cite{a,b,c}`` counts as three references (one per key). Whitespace
    inside the brace is trimmed. Empty keys are dropped.
    """
    keys: list[str] = []
    for match in _CITE_PATTERN.finditer(text):
        for key in match.group(1).split(","):
            key = key.strip()
            if key:
                keys.append(key)
    return Counter(keys)


def cite_keys_preserved(original: str, polished: str) -> bool:
    """Polished may use the same keys (any count >=1) but **must not** add new ones.

    Dropping a cite is allowed — polish often consolidates redundant cites.
    Adding a new key is rejected — the LLM must not invent references.
    """
    orig = extract_cite_keys(original)
    new = extract_cite_keys(polished)
    return set(new.keys()).issubset(set(orig.keys()))


def begin_end_balanced(text: str) -> bool:
    """Stack-based check that every ``\\begin{X}`` has a matching ``\\end{X}``."""
    stack: list[str] = []
    # Walk in document order: interleave begin/end matches by position.
    events: list[tuple[int, str, str]] = []
    for m in _BEGIN_PATTERN.finditer(text):
        events.append((m.start(), "begin", m.group(1)))
    for m in _END_PATTERN.finditer(text):
        events.append((m.start(), "end", m.group(1)))
    events.sort()
    for _pos, kind, name in events:
        if kind == "begin":
            stack.append(name)
        else:  # end
            if not stack or stack[-1] != name:
                return False
            stack.pop()
    return not stack


def _strip_uncountable_regions(text: str) -> str:
    """Remove regions where braces are content, not structure."""
    # Order matters: comments first (% can appear inside verbatim sources but
    # those are stripped en bloc anyway).
    text = _VERBATIM_BLOCK_PATTERN.sub("", text)
    text = _VERB_INLINE_PATTERN.sub("", text)
    text = _COMMENT_PATTERN.sub("", text)
    return text


def brace_balanced(text: str) -> bool:
    """Count `{` vs `}` in non-verbatim, non-comment text.

    Treats ``\\{`` and ``\\}`` (escaped literals) as non-grouping. Doesn't
    track nesting context — just makes sure totals match.
    """
    stripped = _strip_uncountable_regions(text)
    # Drop escaped braces before counting.
    stripped = re.sub(r"\\[{}]", "", stripped)
    open_count = stripped.count("{")
    close_count = stripped.count("}")
    return open_count == close_count


def validate_polished(original: str, polished: str) -> ValidationReport:
    """Run every guard against ``polished`` (with ``original`` for context)."""
    warnings: list[str] = []

    if len(polished) < len(original) * 0.3 and len(original) > 200:
        warnings.append(
            f"polished length ({len(polished)} chars) is <30% of original "
            f"({len(original)} chars) — verify nothing important was dropped"
        )
    elif len(polished) > len(original) * 3 and len(original) > 50:
        warnings.append(
            f"polished length ({len(polished)} chars) is >3x original "
            f"({len(original)} chars) — verify the polish stayed on task"
        )

    return ValidationReport(
        cite_keys_preserved=cite_keys_preserved(original, polished),
        begin_end_balanced=begin_end_balanced(polished),
        brace_balanced=brace_balanced(polished),
        warnings=warnings,
    )


def cite_keys_in_library(
    text: str, library_keys: set[str]
) -> tuple[bool, list[str]]:
    """Check that every ``\\cite{KEY}`` in ``text`` references a key from
    ``library_keys`` (the whitelist built from ``library/selected.yaml``).

    Returns ``(all_valid, missing_keys)``. ``missing_keys`` is sorted for
    stable display. Empty input text → valid (no cites to check).

    This is the §21 compose guard — the LLM must only ``\\cite`` papers that
    actually live in the project library; anything else would become an
    "undefined citation" warning at compile time.
    """
    used = set(extract_cite_keys(text))
    missing = sorted(used - library_keys)
    return (not missing, missing)


@dataclass(frozen=True)
class ComposedReport:
    cite_keys_in_library: bool
    cite_keys_missing: list[str]
    begin_end_balanced: bool
    brace_balanced: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.cite_keys_in_library
            and self.begin_end_balanced
            and self.brace_balanced
        )

    def to_dict(self) -> dict:
        return {
            "cite_keys_in_library": self.cite_keys_in_library,
            "cite_keys_missing": list(self.cite_keys_missing),
            "begin_end_balanced": self.begin_end_balanced,
            "brace_balanced": self.brace_balanced,
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


def validate_composed(composed: str, library_keys: set[str]) -> ComposedReport:
    """Run §21 compose guards: cite-in-library + structure + length."""
    valid, missing = cite_keys_in_library(composed, library_keys)
    warnings: list[str] = []
    if len(composed) < 200:
        warnings.append(
            f"composed length is suspiciously short ({len(composed)} chars) "
            "— LLM may have refused or produced a stub"
        )
    return ComposedReport(
        cite_keys_in_library=valid,
        cite_keys_missing=missing,
        begin_end_balanced=begin_end_balanced(composed),
        brace_balanced=brace_balanced(composed),
        warnings=warnings,
    )


def strip_markdown_fence(text: str) -> str:
    """Strip a leading/trailing `````latex`` (or ```````) fence the LLM may have added.

    Only strips if the fence is at the very beginning / end. Inner usage is
    left alone — that's content, not framing.
    """
    fence_open = re.compile(r"\A\s*```(?:latex|tex)?\s*\n")
    fence_close = re.compile(r"\n```\s*\Z")
    text = fence_open.sub("", text)
    text = fence_close.sub("\n", text)
    return text

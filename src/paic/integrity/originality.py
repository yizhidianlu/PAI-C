"""Originality / plagiarism detection (ARS-fusion P3-1).

Compares each paragraph of the user's draft against:

1. **Library summaries** — text the user did NOT write (paper summaries
   under ``library/summaries/<cite_key>.md``). High overlap = the user
   may have copied phrasing from the cited paper.
2. **Library chunks** — when present (``library/chunks/<cite_key>.json``),
   gives finer-grained overlap detection than summaries alone.
3. **Other sections of the same paper** — high overlap across own
   sections is "self-plagiarism" or unintended duplication that the
   structural ``check_duplicate_paragraphs`` quality gate already flags;
   the originality check goes deeper by comparing tokens, not full
   paragraphs.

Algorithm: **k-shingle Jaccard** (default k=8 word shingles). No
external library; pure Python. Verdict thresholds:

| Score | Verdict | Severity |
|---|---|---|
| ≥ 0.85 | VERBATIM | blocker (≥ 20 consecutive identical words without citation) |
| 0.65–0.85 | CLOSE_MATCH | major |
| 0.40–0.65 | PARAPHRASE | minor (advisory; cite the source) |
| < 0.40 | ORIGINAL | (no issue emitted) |

Sampling:
- mode=pre_review: 30% paragraph sample (min 5)
- mode=final_check: 50% paragraph sample
- mode=originality (standalone): 100% paragraph coverage

Per ARS spec, this is a heuristic screen — not a substitute for
Turnitin / iThenticate. The integrity report includes a
``Tool Limitation Disclaimer`` line.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from paic.integrity.types import IntegrityIssue
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml

# Shingle size — number of consecutive words per shingle. k=8 is a
# practical compromise: small enough to catch sentence-level paraphrase,
# large enough to keep accidental matches on common phrases low.
DEFAULT_SHINGLE_K = 8

# Verdict thresholds — tuned conservatively. Bumping VERBATIM lower
# triggers false-positive blockers on common scientific phrasing
# ("we propose a method that"); bumping it higher loses real verbatim
# copies of dense scientific paragraphs.
THRESHOLD_VERBATIM = 0.85
THRESHOLD_CLOSE_MATCH = 0.65
THRESHOLD_PARAPHRASE = 0.40

# Minimum paragraph length to bother checking. Short paragraphs (< 80
# chars) generate too many false positives — abstract / conclusion
# sentences naturally rhyme with the literature.
MIN_PARAGRAPH_CHARS = 80

# Sample rates per mode (fraction of total paragraphs to check).
SAMPLE_RATES: dict[str, float] = {
    "pre_review": 0.30,
    "final_check": 0.50,
    "originality": 1.00,
}


KIND_VERBATIM = "ORIGINALITY_VERBATIM"
KIND_CLOSE_MATCH = "ORIGINALITY_CLOSE_MATCH"
KIND_PARAPHRASE = "ORIGINALITY_PARAPHRASE"

ORIGINALITY_KINDS: tuple[str, ...] = (KIND_VERBATIM, KIND_CLOSE_MATCH, KIND_PARAPHRASE)


@dataclass(frozen=True)
class _ParagraphRef:
    """Lightweight handle to one paragraph candidate for checking."""

    section_name: str
    para_idx: int
    text: str


@dataclass(frozen=True)
class _SourceFragment:
    """One fragment from the corpus we're comparing against."""

    source_kind: str  # "summary" | "chunk" | "other_section"
    source_id: str    # cite_key for library, section_name for own sections
    text: str


# ---------------------------------------------------- public API


def run_originality_check(
    paths: ProjectPaths,
    *,
    mode: str = "pre_review",
    sample_rate: float | None = None,
    shingle_k: int = DEFAULT_SHINGLE_K,
) -> list[IntegrityIssue]:
    """Run the originality scan and return :class:`IntegrityIssue` entries.

    Args:
        mode: ``"pre_review"`` / ``"final_check"`` / ``"originality"``.
            Drives the default sample rate. Standalone ``"originality"``
            mode covers 100% of paragraphs.
        sample_rate: explicit override for the sampling fraction
            (0.0–1.0). When None, falls back to ``SAMPLE_RATES[mode]``.
        shingle_k: word-shingle size. Default 8.

    Returns the list of originality issues; empty when nothing flagged.
    """
    rate = (
        max(0.01, min(1.0, sample_rate))
        if sample_rate is not None
        else SAMPLE_RATES.get(mode, 0.30)
    )

    paragraphs = _collect_draft_paragraphs(paths)
    if not paragraphs:
        return []

    sources = _collect_source_fragments(paths)
    if not sources:
        # Without a corpus there's nothing to compare against — surface
        # a single advisory note rather than silent zero results.
        return [
            IntegrityIssue(
                kind=KIND_PARAPHRASE,
                severity="info",
                target=None,
                detail=(
                    "Originality check skipped: no library summaries / chunks "
                    "found under .paic/library/. Run /paic-summarize to populate."
                ),
                actionable_fix=(
                    "Run /paic-summarize all so the originality scan has a "
                    "reference corpus to compare against."
                ),
            )
        ]

    sampled = _sample_paragraphs(paragraphs, rate)

    # Pre-compute source shingle sets once; reused per paragraph.
    source_shingles: list[tuple[_SourceFragment, frozenset[int]]] = [
        (src, _shingle_set(src.text, k=shingle_k)) for src in sources
    ]

    issues: list[IntegrityIssue] = []
    for para in sampled:
        para_shingles = _shingle_set(para.text, k=shingle_k)
        if not para_shingles:
            continue

        best_score = 0.0
        best_source: _SourceFragment | None = None
        for src, src_shingles in source_shingles:
            if not src_shingles:
                continue
            score = _jaccard(para_shingles, src_shingles)
            if score > best_score:
                best_score = score
                best_source = src
            if best_score >= THRESHOLD_VERBATIM:
                # Once we hit verbatim, no need to keep scanning sources
                # for this paragraph — the verdict won't get worse.
                break

        if best_source is None:
            continue

        verdict = _classify(best_score)
        if verdict is None:
            continue

        issues.append(_build_issue(para, best_source, best_score, verdict))

    return issues


def list_originality_kinds() -> tuple[str, ...]:
    """Public accessor for downstream code that needs the kind enum."""
    return ORIGINALITY_KINDS


# ---------------------------------------------------- helpers


_WORD_RE = re.compile(r"\w+", re.UNICODE)
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
# Strip LaTeX commands but keep the content inside braces (so \cite{key}
# becomes "key", \section{Method} becomes "Method"). Heuristic — the
# point is to compare prose, not LaTeX syntax.
_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?")


def _normalize_text(text: str) -> list[str]:
    """LaTeX-strip → lowercase → word tokens. Pure for hash stability."""
    text = _LATEX_CMD_RE.sub(" ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = text.lower()
    return [m.group(0) for m in _WORD_RE.finditer(text)]


def _shingle_set(text: str, *, k: int) -> frozenset[int]:
    """k-shingle the tokens; return hashed set for cheap jaccard.

    Hash collisions are theoretically possible but with SHA-1 truncated
    to 8 bytes (53 bits used, 11 free) the collision probability over
    ~5000 shingles is below 10^-8. Acceptable for an originality
    heuristic, not for a cryptographic test.
    """
    tokens = _normalize_text(text)
    if len(tokens) < k:
        return frozenset()
    out: set[int] = set()
    for i in range(len(tokens) - k + 1):
        shingle = " ".join(tokens[i : i + k])
        h = hashlib.sha1(shingle.encode("utf-8")).digest()
        # First 8 bytes → 64-bit signed int → fits in Python int trivially.
        out.add(int.from_bytes(h[:8], "big", signed=False))
    return frozenset(out)


def _jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    union = len(a | b)
    return inter / union if union else 0.0


def _classify(score: float) -> str | None:
    if score >= THRESHOLD_VERBATIM:
        return KIND_VERBATIM
    if score >= THRESHOLD_CLOSE_MATCH:
        return KIND_CLOSE_MATCH
    if score >= THRESHOLD_PARAPHRASE:
        return KIND_PARAPHRASE
    return None


def _collect_draft_paragraphs(paths: ProjectPaths) -> list[_ParagraphRef]:
    sections_dir = paths.drafts_dir / "sections"
    if not sections_dir.is_dir():
        return []
    out: list[_ParagraphRef] = []
    for section_path in sorted(sections_dir.iterdir()):
        if not (section_path.is_file() and section_path.suffix == ".tex"):
            continue
        try:
            text = section_path.read_text(encoding="utf-8")
        except OSError:
            continue
        section_name = section_path.stem
        for idx, paragraph in enumerate(_PARAGRAPH_SPLIT.split(text)):
            stripped = paragraph.strip()
            if len(stripped) < MIN_PARAGRAPH_CHARS:
                continue
            out.append(_ParagraphRef(
                section_name=section_name,
                para_idx=idx,
                text=stripped,
            ))
    return out


def _collect_source_fragments(paths: ProjectPaths) -> list[_SourceFragment]:
    sources: list[_SourceFragment] = []

    summaries_dir = paths.summaries_dir
    if summaries_dir.is_dir():
        for path in sorted(summaries_dir.iterdir()):
            if not (path.is_file() and path.suffix == ".md"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if len(text.strip()) < MIN_PARAGRAPH_CHARS:
                continue
            sources.append(_SourceFragment(
                source_kind="summary",
                source_id=path.stem,
                text=text,
            ))

    chunks_dir = paths.library_dir / "chunks"
    if chunks_dir.is_dir():
        for path in sorted(chunks_dir.iterdir()):
            if not (path.is_file() and path.suffix == ".json"):
                continue
            data = load_yaml(path)  # JSON parses fine via PyYAML safe_load
            if not isinstance(data, dict):
                continue
            cite_key = path.stem
            for chunk in data.get("chunks") or []:
                if not isinstance(chunk, dict):
                    continue
                chunk_text = (chunk.get("text") or "").strip()
                if len(chunk_text) < MIN_PARAGRAPH_CHARS:
                    continue
                sources.append(_SourceFragment(
                    source_kind="chunk",
                    source_id=cite_key,
                    text=chunk_text,
                ))

    return sources


def _sample_paragraphs(
    paragraphs: list[_ParagraphRef],
    rate: float,
) -> list[_ParagraphRef]:
    """Deterministic sampling: order paragraphs, keep every Nth.

    Deterministic because random sampling would make two runs of
    /paic-integrity (e.g. user fixes a paragraph and re-runs)
    inconsistent — they'd see different issues even when the draft
    didn't change in the unchecked paragraphs. With deterministic
    sampling, fixing a flagged paragraph doesn't shuffle the others
    out of the sample.
    """
    if rate >= 1.0:
        return list(paragraphs)
    target = max(5, int(round(len(paragraphs) * rate)))
    if target >= len(paragraphs):
        return list(paragraphs)
    step = len(paragraphs) / target
    return [paragraphs[int(i * step)] for i in range(target)]


def _build_issue(
    para: _ParagraphRef,
    source: _SourceFragment,
    score: float,
    kind: str,
) -> IntegrityIssue:
    severity_map = {
        KIND_VERBATIM: "blocker",
        KIND_CLOSE_MATCH: "major",
        KIND_PARAPHRASE: "minor",
    }
    severity = severity_map[kind]
    target = f"{para.section_name}:para{para.para_idx}"
    if source.source_kind == "summary":
        source_label = f"library/summaries/{source.source_id}"
    elif source.source_kind == "chunk":
        source_label = f"library/chunks/{source.source_id}"
    else:
        source_label = f"section:{source.source_id}"
    return IntegrityIssue(
        kind=kind,
        severity=severity,
        target=target,
        detail=(
            f"Paragraph {para.para_idx} in {para.section_name} matches "
            f"{source_label} at {kind.removeprefix('ORIGINALITY_').lower()} "
            f"level (jaccard={score:.3f}, k={DEFAULT_SHINGLE_K} word shingles)."
        ),
        actionable_fix=_actionable_fix_for(kind, source_label),
        suggested_correction={
            "matched_source": source_label,
            "jaccard_score": round(score, 3),
            "verdict": kind.removeprefix("ORIGINALITY_"),
        },
    )


def _actionable_fix_for(kind: str, source_label: str) -> str:
    table = {
        KIND_VERBATIM: (
            f"This paragraph appears verbatim in {source_label}. "
            "Either rewrite in your own words AND add the citation, or "
            "convert to a properly-formatted block quote with the citation. "
            "Verbatim copy without quotation marks + citation is plagiarism "
            "regardless of intent."
        ),
        KIND_CLOSE_MATCH: (
            f"Paragraph closely paraphrases {source_label}. Add the "
            "citation if missing, then rewrite to genuinely use your own "
            "phrasing — close paraphrase + citation is acceptable in some "
            "venues but NeurIPS / ICLR-style reviewers will flag it."
        ),
        KIND_PARAPHRASE: (
            f"Paragraph paraphrases {source_label} at moderate similarity. "
            "Verify the citation is present; rewrite is optional but "
            "recommended for stronger original framing."
        ),
    }
    return table.get(kind, "Investigate the match before submission.")

"""Final quality gate (§quality phase 10).

Runs paper-level preflight checks before the user declares a draft
ready. Surfaces issues the syntax-only ``guard.py`` cannot — e.g. an
abstract / intro / conclusion whose contribution lists disagree, or a
numeric claim with no experiment artifact backing it.

Eight checks:

1. ``undefined_cites_refs`` — ``\\cite{}`` keys not in the project
   library; ``\\ref{}`` to undeclared labels.
2. ``unresolved_todos`` — ``\\todo{}`` and ``% TODO`` markers in
   sections.
3. ``duplicate_paragraphs`` — paragraphs with rapidfuzz
   ``token_set_ratio >= 85`` to another in the same draft.
4. ``contribution_consistency`` — contribution counts in abstract /
   intro / conclusion sections drift from each other or the paper plan.
5. ``section_length_balance`` — sections wildly off their target_words
   (>2x or <0.3x).
6. ``unsupported_claims`` — claim ledger entries marked
   ``needs_evidence`` whose type is novelty / comparative / numeric /
   result.
7. ``numeric_provenance`` — numeric claims that don't reference any
   experiment_id and have no inline ``\\cite{}``.
8. ``latex_compile_warnings`` — opt-in. When
   ``quality_gate.compile=true`` the gate runs ``latexmk -pdf`` (or
   equivalent) and parses the log. Phase 10 stubs this — returns an
   empty list — so the dependency graph stays buildable; users wire in
   their own compiler later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from paic.latex.guard import extract_cite_keys
from paic.workspace.paths import ProjectPaths
from paic.workspace.store import load_yaml

# ----------------------------------------------------- types


@dataclass
class GateIssue:
    kind: str
    severity: str  # info / minor / major / blocker
    target: str | None
    detail: str
    actionable_fix: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "target": self.target,
            "detail": self.detail,
            "actionable_fix": self.actionable_fix,
        }


@dataclass
class GateResult:
    passed: bool
    issues: list[GateIssue]
    overrides: list[str] = field(default_factory=list)
    overrides_rejected: list[dict[str, str]] = field(default_factory=list)
    """List of ``{kind, severity}`` entries the caller asked to override but
    that PAI-C kept anyway because the issue was a ``blocker``. Surfaced so
    the user can see their override didn't take effect — see ``run_quality_gate``."""
    strict: bool = False
    """Echoes the strict-mode flag. When True, ``overrides`` is ignored and
    every issue counts toward ``passed``."""

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "issue_count": len(self.issues),
            "issues": [i.to_dict() for i in self.issues],
            "overrides": list(self.overrides),
            "overrides_rejected": list(self.overrides_rejected),
            "strict": self.strict,
        }


# ----------------------------------------------------- helpers


_TODO_RE = re.compile(r"\\todo\{[^}]*\}|%\s*TODO[: ]?", re.IGNORECASE)
_REF_RE = re.compile(r"\\ref\{([^}]+)\}")
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?\s*%?")

# Tighter regex for `check_numeric_provenance` — captures sign, integer /
# decimal, and an optional percent / pp / x suffix so we can normalize
# across "92.3%", "0.923", "+5", "3.2 percent", "1.5x" before tolerance
# matching. The leading lookbehind avoids matching "section 4" / "step 2".
_PROVENANCE_NUM_RE = re.compile(
    r"(?<![A-Za-z_])([+-]?\d+(?:\.\d+)?)\s*(%|percent|percentage points|pp|x|×)?",
    re.IGNORECASE,
)
_NUMERIC_TOLERANCE_REL = 0.005  # 0.5% relative tolerance


def _extract_numbers_from_claim(text: str) -> list[tuple[float, str]]:
    """Return ``(value, normalized_unit)`` pairs from a claim's free text.

    ``normalized_unit`` is ``"%"`` for any percent variant or empty
    otherwise. Skips obvious section / step references via the
    lookbehind in ``_PROVENANCE_NUM_RE``.
    """
    out: list[tuple[float, str]] = []
    for m in _PROVENANCE_NUM_RE.finditer(text or ""):
        try:
            val = float(m.group(1))
        except (TypeError, ValueError):
            continue
        suffix = (m.group(2) or "").lower()
        if suffix in {"%", "percent", "percentage points", "pp"}:
            unit = "%"
        elif suffix in {"x", "×"}:
            unit = "x"
        else:
            unit = ""
        out.append((val, unit))
    return out


def _result_value_matches(claim_num: tuple[float, str], result: dict) -> bool:
    """Return True if ``result.value`` matches the claim's number within
    tolerance, accounting for percent/decimal scaling.
    """
    raw_val = result.get("value")
    if raw_val is None:
        return False
    try:
        rv = float(raw_val)
    except (TypeError, ValueError):
        return False

    target, _unit = claim_num
    candidates = [rv, rv * 100.0, rv / 100.0]
    for cand in candidates:
        denom = max(abs(cand), 1e-9)
        if abs(cand - target) / denom <= _NUMERIC_TOLERANCE_REL:
            return True
    return False


def _experiment_results(paths: ProjectPaths, exp_id: str) -> list[dict]:
    """Load ``results[]`` from one experiment yaml; tolerant of malformed entries."""
    exp_path = paths.experiments_dir / f"{exp_id}.yaml"
    if not exp_path.is_file():
        return []
    raw = load_yaml(exp_path) or {}
    if not isinstance(raw, dict):
        return []
    return [r for r in (raw.get("results") or []) if isinstance(r, dict)]


def _read_section(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _all_sections(paths: ProjectPaths) -> dict[str, str]:
    """Map ``section_name -> latex source`` for everything in
    ``drafts/sections``."""
    sections_dir = paths.drafts_dir / "sections"
    out: dict[str, str] = {}
    if not sections_dir.is_dir():
        return out
    for path in sorted(sections_dir.iterdir()):
        if path.is_file() and path.suffix == ".tex":
            out[path.stem] = _read_section(path)
    return out


def _library_cite_keys(paths: ProjectPaths) -> set[str]:
    selected = load_yaml(paths.selected_yaml) or {}
    if not isinstance(selected, dict):
        return set()
    from paic.latex.filler import _cite_key
    return {_cite_key(p) for p in (selected.get("papers") or [])}


def _experiment_ids(paths: ProjectPaths) -> set[str]:
    if not paths.experiments_dir.is_dir():
        return set()
    return {
        p.stem for p in paths.experiments_dir.iterdir()
        if p.is_file() and p.suffix == ".yaml"
    }


# ----------------------------------------------------- checks


def check_undefined_cites_refs(
    sections: dict[str, str],
    library_cite_keys: set[str],
) -> list[GateIssue]:
    issues: list[GateIssue] = []
    # Collect labels across sections so cross-section refs resolve.
    all_labels: set[str] = set()
    for src in sections.values():
        all_labels.update(_LABEL_RE.findall(src))

    for name, src in sections.items():
        # \cite — keys must exist in selected.yaml
        used = extract_cite_keys(src)
        for key in used:
            if key not in library_cite_keys:
                issues.append(GateIssue(
                    kind="undefined_cites_refs",
                    severity="blocker",
                    target=name,
                    detail=f"Section '{name}' \\cite{{{key}}} is not in selected.yaml.",
                    actionable_fix=(
                        f"Run /paic-search + /paic-ingest to add '{key}' to the "
                        f"library, or remove the citation."
                    ),
                ))
        # \ref must resolve to a \label somewhere.
        for ref in _REF_RE.findall(src):
            if ref not in all_labels:
                issues.append(GateIssue(
                    kind="undefined_cites_refs",
                    severity="major",
                    target=name,
                    detail=f"Section '{name}' \\ref{{{ref}}} has no matching \\label.",
                    actionable_fix=(
                        f"Add \\label{{{ref}}} to the figure / table / equation "
                        f"this references, or remove the \\ref."
                    ),
                ))
    return issues


def check_unresolved_todos(sections: dict[str, str]) -> list[GateIssue]:
    issues: list[GateIssue] = []
    for name, src in sections.items():
        matches = _TODO_RE.findall(src)
        if matches:
            issues.append(GateIssue(
                kind="unresolved_todos",
                severity="major",
                target=name,
                detail=f"Section '{name}' has {len(matches)} TODO marker(s).",
                actionable_fix="Resolve or accept each TODO and remove the marker.",
            ))
    return issues


def check_duplicate_paragraphs(
    sections: dict[str, str],
    threshold: int = 85,
) -> list[GateIssue]:
    issues: list[GateIssue] = []
    paragraphs: list[tuple[str, str]] = []  # (section_name, paragraph_text)
    for name, src in sections.items():
        for para in _PARAGRAPH_SPLIT_RE.split(src):
            normalized = " ".join(para.split())
            if len(normalized) >= 80:  # Ignore very short paragraphs.
                paragraphs.append((name, normalized))
    seen_pairs: set[tuple[int, int]] = set()
    for i in range(len(paragraphs)):
        for j in range(i + 1, len(paragraphs)):
            if (i, j) in seen_pairs:
                continue
            score = fuzz.token_set_ratio(paragraphs[i][1], paragraphs[j][1])
            if score >= threshold:
                seen_pairs.add((i, j))
                a_section, _ = paragraphs[i]
                b_section, _ = paragraphs[j]
                issues.append(GateIssue(
                    kind="duplicate_paragraphs",
                    severity="minor",
                    target=f"{a_section} ↔ {b_section}",
                    detail=(
                        f"Paragraphs in '{a_section}' and '{b_section}' overlap "
                        f"at fuzz token_set_ratio={score}."
                    ),
                    actionable_fix=(
                        "Rewrite one of the paragraphs or replace with a "
                        "back-reference (e.g. 'as discussed in Section X')."
                    ),
                ))
    return issues


def _count_contribution_mentions(text: str) -> int:
    """Heuristic count: enumerate-like list items + bullet markers."""
    if not text:
        return 0
    # Count itemize / enumerate items.
    items = len(re.findall(r"\\item\b", text))
    # Count "we" / "our" + verb that introduces a contribution-like sentence.
    enumerated = len(re.findall(
        r"\b(?:first|second|third|fourth|fifth|finally|lastly|moreover)\s*,",
        text, flags=re.IGNORECASE,
    ))
    return items + enumerated


def check_contribution_consistency(
    sections: dict[str, str],
    paper_plan: dict[str, object] | None,
) -> list[GateIssue]:
    issues: list[GateIssue] = []
    abstract = sections.get("00_abstract", "")
    intro = sections.get("01_intro", "")
    conclusion = sections.get("06_conclusion", "") or sections.get("05_conclusion", "")
    counts: dict[str, int] = {
        "00_abstract": _count_contribution_mentions(abstract),
        "01_intro": _count_contribution_mentions(intro),
        "06_conclusion": _count_contribution_mentions(conclusion),
    }
    plan_count = 0
    if paper_plan and isinstance(paper_plan, dict):
        contribs = paper_plan.get("contributions") or []
        if isinstance(contribs, list):
            plan_count = len(contribs)
    counted = {k: v for k, v in counts.items() if v > 0}
    if not counted and plan_count == 0:
        return issues
    distinct_values = set(counted.values())
    if plan_count:
        distinct_values.add(plan_count)
    if len(distinct_values) > 1:
        issues.append(GateIssue(
            kind="contribution_consistency",
            severity="major",
            target="abstract/intro/conclusion vs paper_plan",
            detail=(
                f"Contribution counts disagree — paper_plan: {plan_count}, "
                + ", ".join(f"{k}: {v}" for k, v in counted.items())
            ),
            actionable_fix=(
                "Pick one canonical count and update the others. paper_plan.yaml "
                "is the source of truth — sections should match it."
            ),
        ))
    return issues


def check_section_length_balance(
    sections: dict[str, str],
    paper_plan: dict[str, object] | None,
    min_ratio: float = 0.3,
    max_ratio: float = 2.0,
) -> list[GateIssue]:
    issues: list[GateIssue] = []
    if not paper_plan or not isinstance(paper_plan, dict):
        return issues
    section_plan = paper_plan.get("section_plan") or []
    targets: dict[str, int] = {}
    for entry in section_plan:
        if isinstance(entry, dict):
            tw = entry.get("target_words")
            if entry.get("name") and isinstance(tw, int) and tw > 0:
                targets[entry["name"]] = tw
    for name, src in sections.items():
        target = targets.get(name)
        if not target:
            continue
        word_count = len([w for w in src.split() if w])
        if word_count == 0:
            continue
        ratio = word_count / target
        if ratio < min_ratio or ratio > max_ratio:
            issues.append(GateIssue(
                kind="section_length_balance",
                severity="minor",
                target=name,
                detail=(
                    f"Section '{name}' is {word_count} words; plan target {target} "
                    f"(ratio {ratio:.1f}x; allowed [{min_ratio}, {max_ratio}])."
                ),
                actionable_fix=(
                    "Rebalance: tighten or expand to land within "
                    f"[{int(target * min_ratio)}, {int(target * max_ratio)}] words."
                ),
            ))
    return issues


_STRONG_TYPES = frozenset({"novelty", "comparative", "numeric", "result"})


def check_unsupported_claims(paths: ProjectPaths) -> list[GateIssue]:
    if not paths.claims_yaml.is_file():
        return []
    raw = load_yaml(paths.claims_yaml) or {}
    if not isinstance(raw, dict):
        return []
    issues: list[GateIssue] = []
    for c in (raw.get("claims") or []):
        if not isinstance(c, dict):
            continue
        if c.get("status") != "needs_evidence":
            continue
        if c.get("type") not in _STRONG_TYPES:
            continue
        issues.append(GateIssue(
            kind="unsupported_claims",
            severity="major",
            target=c.get("id"),
            detail=(
                f"Claim {c.get('id')} ({c.get('type')}, {c.get('status')}): "
                f"{(c.get('text') or '')[:160]}"
            ),
            actionable_fix=(
                "Add supporting_papers / supporting_experiments to the claim, "
                "convert to status=todo with an explicit fix plan, or mark "
                "status=rejected."
            ),
        ))
    return issues


def check_numeric_provenance(
    paths: ProjectPaths,
    experiment_ids: set[str],
) -> list[GateIssue]:
    """Verify every numeric claim's value actually matches a recorded
    ``ExperimentResult``. Three failure modes:

    - ``numeric_provenance`` (major) — no supporting_experiments, no
      required_citations; the claim is unmoored.
    - ``numeric_no_results_recorded`` (major) — supporting experiment(s)
      exist but have empty ``results[]``; user hasn't logged real outcomes
      via ``paic_experiment_record_result`` yet.
    - ``numeric_unmatched`` (**blocker**) — supporting experiment(s) have
      results, but no result's ``value`` matches a number in the claim
      text within ``_NUMERIC_TOLERANCE_REL`` (0.5% relative). This is the
      "abstract says 4.2% but results.yaml says 4.0%" case.

    External numeric claims (e.g. citing prior work's number) are exempt
    via ``required_citations``.
    """
    if not paths.claims_yaml.is_file():
        return []
    raw = load_yaml(paths.claims_yaml) or {}
    if not isinstance(raw, dict):
        return []
    issues: list[GateIssue] = []
    for c in (raw.get("claims") or []):
        if not isinstance(c, dict):
            continue
        if c.get("type") != "numeric":
            continue
        text = c.get("text") or ""
        numbers = _extract_numbers_from_claim(text)
        if not numbers:
            continue  # numeric type but no digits — likely mis-classified

        supporting_exps = c.get("supporting_experiments") or []
        valid_exps = [eid for eid in supporting_exps if eid in experiment_ids]

        if not valid_exps:
            if c.get("required_citations"):
                # External numeric claim — citation is acceptable provenance.
                continue
            issues.append(GateIssue(
                kind="numeric_provenance",
                severity="major",
                target=c.get("id"),
                detail=(
                    f"Numeric claim {c.get('id')} has no supporting_experiments "
                    f"and no required_citations: '{text[:160]}'."
                ),
                actionable_fix=(
                    "Either (a) attach an experiment_id whose results produce "
                    "this number, or (b) cite the source paper for an external "
                    "number."
                ),
            ))
            continue

        # Has at least one valid supporting experiment. Look up its results.
        all_results: list[dict] = []
        for eid in valid_exps:
            all_results.extend(_experiment_results(paths, eid))

        if not all_results:
            issues.append(GateIssue(
                kind="numeric_no_results_recorded",
                severity="major",
                target=c.get("id"),
                detail=(
                    f"Numeric claim {c.get('id')} references experiment(s) "
                    f"{valid_exps} but they have no ``results[]`` recorded yet."
                ),
                actionable_fix=(
                    "Run paic_experiment_record_result(experiment_id, "
                    "metric_name, value, run_id, ...) to log each real "
                    "outcome from your runs, then re-run /paic-finalize."
                ),
            ))
            continue

        # Try to match each number in the claim against any recorded result.
        unmatched: list[float] = []
        for num in numbers:
            if not any(_result_value_matches(num, r) for r in all_results):
                unmatched.append(num[0])

        if unmatched:
            issues.append(GateIssue(
                kind="numeric_unmatched",
                severity="blocker",
                target=c.get("id"),
                detail=(
                    f"Numeric claim {c.get('id')}: number(s) {unmatched} "
                    f"do not match any recorded ExperimentResult in "
                    f"{valid_exps} (within "
                    f"{_NUMERIC_TOLERANCE_REL * 100:.1f}% relative tolerance). "
                    f"Claim text: '{text[:160]}'."
                ),
                actionable_fix=(
                    "Either (a) the claim is wrong — fix the number; (b) the "
                    "result wasn't recorded — call "
                    "paic_experiment_record_result with the actual value; "
                    "(c) the number is from an external source — set "
                    "required_citations on the claim and remove this "
                    "experiment binding."
                ),
            ))
    return issues


def check_latex_compile_warnings(paths: ProjectPaths, *, enabled: bool = False) -> list[GateIssue]:
    """Stub: phase-10 deliberately doesn't run an external compiler.

    When ``enabled=True`` the user's config requested compile-time
    diagnostics, but actually invoking ``latexmk`` is left to a follow-up
    issue (network / tool availability is brittle in CI). For now the
    stub always returns []; it's a hook for future wiring.
    """
    return []


# ----------------------------------------------------- top-level


def run_quality_gate(
    paths: ProjectPaths,
    *,
    compile_check: bool = False,
    overrides: list[str] | None = None,
    strict: bool = False,
) -> GateResult:
    """Run all eight checks and return a GateResult.

    ``overrides`` is a list of issue ``kind`` values the caller wants to drop
    from the output. PAI-C honors the request **only for issues whose
    severity is below blocker**; ``blocker`` issues are kept regardless and
    recorded in ``overrides_rejected`` so the caller can see their request
    was refused. This guarantees ``passed=true`` never coexists with a live
    blocker.

    ``strict=True`` ignores ``overrides`` entirely — every issue counts
    toward ``passed``. Intended for CI / pre-submission final pass.
    """
    overrides = list(overrides or [])
    sections = _all_sections(paths)
    library_cite_keys = _library_cite_keys(paths)
    experiment_ids = _experiment_ids(paths)
    paper_plan: dict[str, object] | None = None
    if paths.paper_plan_yaml.is_file():
        loaded = load_yaml(paths.paper_plan_yaml)
        if isinstance(loaded, dict):
            paper_plan = loaded

    raw_issues: list[GateIssue] = []
    raw_issues.extend(check_undefined_cites_refs(sections, library_cite_keys))
    raw_issues.extend(check_unresolved_todos(sections))
    raw_issues.extend(check_duplicate_paragraphs(sections))
    raw_issues.extend(check_contribution_consistency(sections, paper_plan))
    raw_issues.extend(check_section_length_balance(sections, paper_plan))
    raw_issues.extend(check_unsupported_claims(paths))
    raw_issues.extend(check_numeric_provenance(paths, experiment_ids))
    raw_issues.extend(check_latex_compile_warnings(paths, enabled=compile_check))

    # Apply override filter:
    # - strict=True: keep every issue, ignore overrides
    # - strict=False: drop issues whose kind is in overrides AND severity != blocker;
    #   blockers stay and are recorded in overrides_rejected.
    issues: list[GateIssue] = []
    overrides_rejected: list[dict[str, str]] = []
    for issue in raw_issues:
        if strict or issue.kind not in overrides:
            issues.append(issue)
            continue
        if issue.severity == "blocker":
            issues.append(issue)
            overrides_rejected.append({"kind": issue.kind, "severity": issue.severity})
        # else: silently dropped per user override request.

    passed = not any(i.severity in {"major", "blocker"} for i in issues)
    return GateResult(
        passed=passed,
        issues=issues,
        overrides=overrides,
        overrides_rejected=overrides_rejected,
        strict=strict,
    )

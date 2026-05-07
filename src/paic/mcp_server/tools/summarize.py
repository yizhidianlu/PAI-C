"""Summarize tool: read paper markdown -> structured ``PaperSummary`` -> disk.

Two execution modes (§16):

- **API/SDK mode** (default): :func:`summarize_run` reads the markdown, calls
  the routed LLM backend, validates the response against ``_SummaryFields``,
  and writes the summary to disk.
- **Host orchestration mode** (``routing.overrides.summarize: host``):
  :func:`summarize_run` returns a ``mode: "host_orchestration"`` directive
  containing the markdown + JSON schema hint; the Skill layer asks Claude
  Code's main conversation to generate the structured fields and persists
  them via :func:`summarize_persist`. PAI-C never calls an LLM in this mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from paic.config import load_config
from paic.latex.filler import _cite_key
from paic.llm.backends import HostOrchestrationRequired
from paic.llm.client import LLMClient, LLMUnavailable, get_default_client
from paic.llm.host import build_host_directive
from paic.llm.prompts import load_prompt
from paic.llm.router import LLMRouter
from paic.schemas.paper import PaperRef, PaperSummary
from paic.sources.arxiv_bridge import read_local_markdown
from paic.sources.pdf_extract import extract_pdf_text
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml, save_yaml, write_text


# Schema the LLM is asked to return — narrower than PaperSummary because the
# caller already knows paper metadata. We compose the full summary client-side.
#
# ``extra='forbid'`` is deliberate: when a host-orchestrated summary contains
# fields outside this schema (typo, mismatched server version, hallucinated
# extra key), we want a loud schema_validation_failed instead of silently
# dropping data. The pre-existing failure mode — server running stale code,
# new schema fields invisibly stripped — left phase-3 / phase-10 quality_gate
# checks 0-hit without any error surface.
class _SummaryFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: str
    method: str
    key_results: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    relevance_to_project: str | None = None
    # §quality phase 3 — Optional structured evidence fields. Legacy LLM
    # responses that don't include them validate fine; new prompts encourage
    # the model to populate them.
    contribution_type: str | None = None
    datasets: list[str] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    numeric_results: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    citation_claims: list[str] = Field(default_factory=list)
    quote_spans: list[str] = Field(default_factory=list)


MAX_PAPER_CHARS = 120_000  # ~30k tokens, well under the model context window


def _resolve_paper(
    project_paths, paper_id: str
) -> tuple[PaperRef, str] | None:
    """Resolve any id form to (PaperRef, cite_key).

    Match order (first hit wins):
      1. ``arxiv_id`` exact match
      2. ``doi`` exact match
      3. ``s2_id`` exact match
      4. any value in ``external_ids`` (PMID / PMCID / OpenAlex W-id / …)
      5. ``cite_key`` exact match (the BibTeX-aligned slug)

    The earlier resolver only matched arxiv_id and s2_id, which broke every
    non-arxiv paper (PubMed / OpenAlex / Crossref / bioRxiv) since callers
    typically have a DOI or PMID, not an s2_id.
    """
    selected = load_yaml(project_paths.selected_yaml) or {}
    if not isinstance(selected, dict):
        return None
    for record in selected.get("papers") or []:
        if not isinstance(record, dict):
            continue
        cite_key = _cite_key(record)
        external_values = (record.get("external_ids") or {}).values()
        if (
            record.get("arxiv_id") == paper_id
            or record.get("doi") == paper_id
            or record.get("s2_id") == paper_id
            or paper_id in external_values
            or cite_key == paper_id
        ):
            return PaperRef.model_validate(record), cite_key
    return None


# Kept for backward-compat with code paths / tests that don't need cite_key.
def _find_paper_in_library(project_paths, paper_id: str) -> PaperRef | None:
    resolved = _resolve_paper(project_paths, paper_id)
    return resolved[0] if resolved else None


def _format_for_llm(ref: PaperRef, body: str) -> str:
    title = ref.title
    authors = ", ".join(ref.authors[:6]) + (" et al." if len(ref.authors) > 6 else "")
    header = f"# {title}\n\n**Authors:** {authors or 'unknown'}\n"
    if ref.year:
        header += f"**Year:** {ref.year}\n"
    if ref.venue:
        header += f"**Venue:** {ref.venue}\n"
    if ref.abstract:
        header += f"\n## Abstract\n\n{ref.abstract.strip()}\n"
    body = body[:MAX_PAPER_CHARS]
    return f"{header}\n## Body\n\n{body}"


def _render_summary_markdown(summary: PaperSummary) -> str:
    p = summary.paper
    lines = [
        f"# Summary: {p.title}",
        "",
        f"- **arXiv:** {p.arxiv_id or '—'}",
        f"- **DOI:** {p.doi or '—'}",
        f"- **Year / Venue:** {p.year or '—'} / {p.venue or '—'}",
        f"- **Summarized at:** {summary.summarized_at.isoformat()}",
        f"- **Model:** {summary.summarizer_model}",
        "",
        "## Problem",
        "",
        summary.problem,
        "",
        "## Method",
        "",
        summary.method,
        "",
        "## Key Results",
        "",
    ]
    lines.extend(f"- {r}" for r in summary.key_results)
    lines += ["", "## Limitations", ""]
    lines.extend(f"- {l}" for l in summary.limitations)
    lines += ["", "## Techniques", "", ", ".join(summary.techniques) or "—"]
    # §quality phase 3 — render new evidence fields when populated.
    # Each section is omitted when its list is empty so old summaries
    # don't sprout empty headers.
    if summary.contribution_type:
        lines += ["", f"**Contribution type:** {summary.contribution_type}"]
    if summary.datasets:
        lines += ["", "## Datasets", "", ", ".join(summary.datasets)]
    if summary.baselines:
        lines += ["", "## Baselines", "", ", ".join(summary.baselines)]
    if summary.metrics:
        lines += ["", "## Metrics", "", ", ".join(summary.metrics)]
    if summary.numeric_results:
        lines += ["", "## Numeric Results", ""]
        lines.extend(f"- {r}" for r in summary.numeric_results)
    if summary.assumptions:
        lines += ["", "## Assumptions", ""]
        lines.extend(f"- {a}" for a in summary.assumptions)
    if summary.failure_modes:
        lines += ["", "## Failure Modes", ""]
        lines.extend(f"- {f}" for f in summary.failure_modes)
    if summary.open_questions:
        lines += ["", "## Open Questions", ""]
        lines.extend(f"- {q}" for q in summary.open_questions)
    if summary.citation_claims:
        lines += ["", "## Citation Claims", ""]
        lines.extend(f"- {c}" for c in summary.citation_claims)
    if summary.quote_spans:
        lines += ["", "## Quote Spans", ""]
        lines.extend(f'> "{q}"' for q in summary.quote_spans)
    if summary.relevance_to_project:
        lines += ["", "## Relevance to Project", "", summary.relevance_to_project]
    return "\n".join(lines) + "\n"


HOST_INSTRUCTIONS = (
    "PAI-C is configured to host-orchestrate the `summarize` node. Generate a "
    "JSON object matching `schema_hint` directly from the `markdown` body "
    "below. Required keys: problem / method / key_results / limitations / "
    "techniques / relevance_to_project. §quality phase 3 added optional keys: "
    "contribution_type, datasets, baselines, metrics, numeric_results, "
    "assumptions, failure_modes, open_questions, citation_claims, quote_spans "
    "— populate them when the paper provides the info; leave [] / null "
    "otherwise. Then call `mcp__paic__paic_summarize_persist(project_dir=<cwd>, "
    "paper_id=<id>, structured={...your json...})` to validate and write it "
    "to disk. Do NOT call `paic_summarize_run` again for this paper."
)


def summarize_run(
    project_dir: str,
    paper_id: str,
    force: bool = False,
    paper_text: str | None = None,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Generate a structured summary for ``paper_id``.

    If ``paper_text`` is provided (e.g. fetched via ``mcp__arxiv__read_paper``
    by the skill layer when the local markdown can't be found), it's used
    directly and no filesystem probe is attempted. This is the canonical way
    to bridge between PAI-C and arxiv MCP without coupling to arxiv MCP's
    storage layout.

    When ``routing.overrides.summarize == "host"`` (or ``routing.default``),
    no LLM call is made. The function returns a ``mode: "host_orchestration"``
    directive instead — the Skill layer is responsible for running the LLM
    work in the main Claude Code conversation and calling
    :func:`summarize_persist` with the result.

    Returns the summary on success or an ``error`` dict on failure (paper not
    in library, markdown unavailable, LLM unavailable, etc.).
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    resolved = _resolve_paper(paths, paper_id)
    if resolved is None:
        # Surface what we tried and what's actually in the library so users
        # can spot a slug typo (e.g. dashes-vs-underscores in DOI cite_keys).
        library_keys: list[str] = []
        try:
            selected = load_yaml(paths.selected_yaml) or {}
            if isinstance(selected, dict):
                for record in (selected.get("papers") or [])[:50]:
                    if isinstance(record, dict):
                        library_keys.append(_cite_key(record))
        except Exception:
            pass
        return {
            "error": "paper_not_in_library",
            "hint": (
                "Add the paper via /paic-ingest first. The resolver matches "
                "arxiv_id / doi / s2_id / any external_ids value (PMID, PMCID, …) "
                "/ cite_key — make sure the id you're passing exists on the paper "
                "record in .paic/library/selected.yaml."
            ),
            "paper_id": paper_id,
            "tried_match_fields": ["arxiv_id", "doi", "s2_id", "external_ids.values()", "cite_key"],
            "library_cite_keys": library_keys,
        }
    ref, cite_key = resolved

    # Canonical on-disk filenames are keyed by cite_key (filesystem-safe;
    # consistent with library/pdfs/<cite_key>.{md,pdf}). Legacy summaries from
    # before this fix were keyed by the user-passed paper_id (typically the
    # arxiv_id) — keep reading them on cache lookup so existing libraries
    # don't lose their cache.
    summary_md = paths.summaries_dir / f"{cite_key}.md"
    summary_yaml = paths.summaries_dir / f"{cite_key}.yaml"
    legacy_md = paths.summaries_dir / f"{paper_id}.md"
    legacy_yaml = paths.summaries_dir / f"{paper_id}.yaml"

    if not force:
        for cache_yaml, cache_md in (
            (summary_yaml, summary_md),
            (legacy_yaml, legacy_md),
        ):
            if cache_yaml.exists():
                existing = load_yaml(cache_yaml)
                if existing:
                    return {
                        "paper_id": paper_id,
                        "cite_key": cite_key,
                        "summary_path": str(cache_md),
                        "structured": existing,
                        "from_cache": True,
                    }

    cfg = load_config()
    pdf_extraction_failed_reason: str | None = None
    tried_pdf_path: str | None = None

    if paper_text is not None and paper_text.strip():
        body: str | None = paper_text
        text_source = "caller_supplied"
    elif ref.arxiv_id:
        # Only probe arxiv MCP storage when the paper actually has an arxiv_id.
        # Passing a DOI / PMID / cite_key would never hit arxiv storage layout
        # (`<arxiv_id>.md`) — skip the call to keep the trace clean.
        body = read_local_markdown(ref.arxiv_id, cfg=cfg)
        text_source = "local_markdown"
    else:
        body = None
        text_source = "local_markdown"

    # §25: fall back to project-local archive (library/pdfs/<filename>) for
    # non-arxiv papers (PubMed / bioRxiv / OpenAlex / …) that ingest downloaded
    # via paper-search-mcp.
    #
    # Filename resolution: prefer ``ref.pdf_local_path`` (set by ingest when it
    # uses the human-readable ``NNN_title`` naming scheme); fall back to the
    # legacy ``<cite_key>.{md,pdf}`` for libraries from before that change.
    if body is None:
        pdfs_dir = paths.pdfs_dir

        explicit_name = ref.pdf_local_path
        if explicit_name:
            primary = pdfs_dir / explicit_name
            if primary.suffix.lower() == ".md":
                md_path = primary
                pdf_path = pdfs_dir / f"{cite_key}.pdf"
            elif primary.suffix.lower() == ".pdf":
                md_path = pdfs_dir / f"{cite_key}.md"
                pdf_path = primary
            else:
                md_path = pdfs_dir / f"{cite_key}.md"
                pdf_path = pdfs_dir / f"{cite_key}.pdf"
        else:
            md_path = pdfs_dir / f"{cite_key}.md"
            pdf_path = pdfs_dir / f"{cite_key}.pdf"

        if md_path.is_file():
            try:
                body = md_path.read_text(encoding="utf-8")
                text_source = "library_pdfs_md"
            except OSError:
                body = None

        if body is None and pdf_path.is_file():
            tried_pdf_path = str(pdf_path)
            text, err = extract_pdf_text(
                pdf_path, cache_dir=cfg.pdf_text_cache_dir
            )
            if text:
                body = text
                text_source = "library_pdfs_pdf_extracted"
            else:
                pdf_extraction_failed_reason = err

    if body is None:
        error_payload: dict[str, Any] = {
            "error": "paper_markdown_not_found",
            "hint": (
                "Markdown / PDF for this paper was not found. Either: "
                "(1) call mcp__arxiv__read_paper(paper_id) and pass the "
                "result back via paper_text=...; (2) run "
                "mcp__arxiv__download_paper(paper_id) and retry; or "
                "(3) for non-arxiv papers, ingest with paper-search-mcp "
                "so the PDF lands at .paic/library/pdfs/<cite_key>.pdf."
            ),
            "paper_id": paper_id,
            "looked_under": [str(p) for p in cfg.arxiv_mcp_storage_paths],
        }
        if tried_pdf_path:
            error_payload["tried_pdf_path"] = tried_pdf_path
        if pdf_extraction_failed_reason:
            error_payload["pdf_extraction_failed_reason"] = pdf_extraction_failed_reason
            # Add an actionable hint for the most common failure modes.
            if pdf_extraction_failed_reason == "encrypted":
                error_payload["pdf_hint"] = (
                    "PDF is encrypted. Try `qpdf --decrypt <in> <out>` and "
                    "replace the file in .paic/library/pdfs/, then retry."
                )
            elif pdf_extraction_failed_reason == "empty_extraction":
                error_payload["pdf_hint"] = (
                    "PDF text extraction returned (almost) nothing — likely a "
                    "scanned-image PDF. Run `ocrmypdf <in> <out>` to add a "
                    "text layer, replace the file, and retry. PAI-C does not "
                    "OCR automatically."
                )
            elif pdf_extraction_failed_reason == "corrupt":
                error_payload["pdf_hint"] = (
                    "PDF appears corrupt. Re-download via /paic-ingest "
                    "(delete the existing copy first to bypass the idempotent "
                    "skip)."
                )
        return error_payload

    def _summarize_directive() -> dict[str, Any]:
        return build_host_directive(
            node="summarize",
            instructions=HOST_INSTRUCTIONS,
            schema_hint=_SummaryFields.model_json_schema(),
            next_tool="mcp__paic__paic_summarize_persist",
            metadata={
                "paper_id": paper_id,
                "cite_key": cite_key,
                "text_source": text_source,
                "paper_ref": ref.model_dump(mode="json"),
                "markdown": body,
            },
        ).to_dict()

    # Route check: if the summarize node is host-orchestrated, return a
    # directive instead of calling an LLM. We've already loaded the markdown
    # so the Skill can hand it to the main conversation directly.
    router = LLMRouter(cfg)
    if router.is_host_orchestrated("summarize"):
        return _summarize_directive()

    prompt_system = load_prompt("summarize")
    prompt_user = _format_for_llm(ref, body)

    llm_client = llm or get_default_client()
    try:
        fields = llm_client.complete_json(
            system=prompt_system,
            user=prompt_user,
            schema=_SummaryFields,
            max_tokens=2048,
            temperature=0.1,
            node="summarize",
        )
    except LLMUnavailable as exc:
        return {"error": "llm_unavailable", "detail": str(exc)}
    except HostOrchestrationRequired:
        # Defensive: should be caught by is_host_orchestrated above. Keeps
        # the contract crisp if anyone reorders or skips the precheck.
        return _summarize_directive()

    summary = PaperSummary(
        paper=ref,
        problem=fields.problem,
        method=fields.method,
        key_results=fields.key_results,
        limitations=fields.limitations,
        techniques=fields.techniques,
        relevance_to_project=fields.relevance_to_project,
        contribution_type=fields.contribution_type,
        datasets=fields.datasets,
        baselines=fields.baselines,
        metrics=fields.metrics,
        numeric_results=fields.numeric_results,
        assumptions=fields.assumptions,
        failure_modes=fields.failure_modes,
        open_questions=fields.open_questions,
        citation_claims=fields.citation_claims,
        quote_spans=fields.quote_spans,
        summarized_at=datetime.now(UTC),
        summarizer_model=llm_client.model,
    )

    save_yaml(summary_yaml, summary.model_dump(mode="json"))
    write_text(summary_md, _render_summary_markdown(summary))

    return {
        "paper_id": paper_id,
        "cite_key": cite_key,
        "text_source": text_source,
        "summary_path": str(summary_md),
        "structured": summary.model_dump(mode="json"),
        "from_cache": False,
    }


def summarize_persist(
    project_dir: str,
    paper_id: str,
    structured: dict[str, Any],
    *,
    summarizer_model: str | None = None,
) -> dict[str, Any]:
    """Persist a host-orchestrated summary (LLM-free).

    Validates ``structured`` against :class:`_SummaryFields`, composes the
    full :class:`PaperSummary` (paper metadata + structured fields +
    timestamp + ``summarizer_model``), and writes the same YAML + Markdown
    pair as :func:`summarize_run`.

    Args:
        project_dir: PAI-C project root (must already be initialized).
        paper_id: must already exist in ``library/selected.yaml``.
        structured: dict with keys ``problem``, ``method``, ``key_results``,
            ``limitations``, ``techniques`` and optional ``relevance_to_project``.
        summarizer_model: free-form label for ``summarizer_model`` field.
            Defaults to ``"host:claude-code-main"`` to mark host-orchestrated
            summaries (lets users grep their library to see provenance).

    Returns:
        Same shape as :func:`summarize_run`'s success path, plus
        ``persisted: true`` and ``mode: "host_orchestration"``. Validation
        failures return ``error: "schema_validation_failed"`` so the Skill
        can ask Claude to retry.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}

    resolved = _resolve_paper(paths, paper_id)
    if resolved is None:
        # Surface what we tried and what's actually in the library so users
        # can spot a slug typo (e.g. dashes-vs-underscores in DOI cite_keys).
        library_keys: list[str] = []
        try:
            selected = load_yaml(paths.selected_yaml) or {}
            if isinstance(selected, dict):
                for record in (selected.get("papers") or [])[:50]:
                    if isinstance(record, dict):
                        library_keys.append(_cite_key(record))
        except Exception:
            pass
        return {
            "error": "paper_not_in_library",
            "hint": (
                "Add the paper via /paic-ingest first. The resolver matches "
                "arxiv_id / doi / s2_id / any external_ids value (PMID, PMCID, …) "
                "/ cite_key — make sure the id you're passing exists on the paper "
                "record in .paic/library/selected.yaml."
            ),
            "paper_id": paper_id,
            "tried_match_fields": ["arxiv_id", "doi", "s2_id", "external_ids.values()", "cite_key"],
            "library_cite_keys": library_keys,
        }
    ref, cite_key = resolved

    try:
        fields = _SummaryFields.model_validate(structured)
    except ValidationError as exc:
        return {
            "error": "schema_validation_failed",
            "paper_id": paper_id,
            "detail": exc.errors(),
            "hint": (
                "Fix the JSON to match schema_hint and retry. Required keys: "
                "problem (str), method (str), key_results (list[str]), "
                "limitations (list[str]), techniques (list[str]). Optional: "
                "relevance_to_project (str), contribution_type (str), "
                "datasets / baselines / metrics / numeric_results / "
                "assumptions / failure_modes / open_questions / "
                "citation_claims / quote_spans (list[str])."
            ),
        }

    summary = PaperSummary(
        paper=ref,
        problem=fields.problem,
        method=fields.method,
        key_results=fields.key_results,
        limitations=fields.limitations,
        techniques=fields.techniques,
        relevance_to_project=fields.relevance_to_project,
        contribution_type=fields.contribution_type,
        datasets=fields.datasets,
        baselines=fields.baselines,
        metrics=fields.metrics,
        numeric_results=fields.numeric_results,
        assumptions=fields.assumptions,
        failure_modes=fields.failure_modes,
        open_questions=fields.open_questions,
        citation_claims=fields.citation_claims,
        quote_spans=fields.quote_spans,
        summarized_at=datetime.now(UTC),
        summarizer_model=summarizer_model or "host:claude-code-main",
    )

    summary_md = paths.summaries_dir / f"{cite_key}.md"
    summary_yaml = paths.summaries_dir / f"{cite_key}.yaml"
    save_yaml(summary_yaml, summary.model_dump(mode="json"))
    write_text(summary_md, _render_summary_markdown(summary))

    return {
        "mode": "host_orchestration",
        "paper_id": paper_id,
        "cite_key": cite_key,
        "summary_path": str(summary_md),
        "structured": summary.model_dump(mode="json"),
        "persisted": True,
    }

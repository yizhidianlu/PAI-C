"""PAI-C MCP server — FastMCP entry point.

Tool registrations live here; the actual logic is in ``paic.mcp_server.tools.*``.
This module is the single source of truth for the server's stdio transport;
``paic-mcp`` and ``paic serve`` both call ``main()``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from paic import __version__
from paic.mcp_server.tools import attach as attach_tools
from paic.mcp_server.tools import draft as draft_tools
from paic.mcp_server.tools import experiment as experiment_tools
from paic.mcp_server.tools import figure as figure_tools
from paic.mcp_server.tools import ideate as ideate_tools
from paic.mcp_server.tools import library as library_tools
from paic.mcp_server.tools import pacing as pacing_tools
from paic.mcp_server.tools import review as review_tools
from paic.mcp_server.tools import runs as runs_tools
from paic.mcp_server.tools import strategy as strategy_tools
from paic.mcp_server.tools import summarize as summarize_tools
from paic.mcp_server.tools import workspace as workspace_tools

mcp = FastMCP(
    "paic",
    instructions=(
        f"PAI-C v{__version__} — Paper All-in-Claude. Provides workspace, library, "
        "ideation, experiment design, multi-agent review, and LaTeX writing "
        "tools for STEM paper workflows."
    ),
)


# --- Workspace tools -------------------------------------------------------

@mcp.tool()
def paic_workspace_init(
    project_dir: str,
    title: str | None = None,
    venue: str | None = None,
    deadline: str | None = None,
    domain_preset: str | None = None,
    platforms_override: list[str] | None = None,
) -> dict[str, Any]:
    """Initialize a PAI-C project at ``project_dir``.

    Creates ``.paic/`` with the standard subdirectories (library/, ideas/,
    experiments/, reviews/, drafts/, state/, logs/) and writes
    ``.paic/project.yaml``. Idempotent: re-running on an existing project
    only updates the metadata fields you pass.

    Multi-platform search (§18, opt-in):

    ``domain_preset`` selects one of the built-in research-area presets that
    seeds the project's paper-search platform list. Accepted values:
    ``cs_ml`` / ``biomed`` / ``physics_math`` / ``econ_social`` /
    ``engineering`` / ``interdisciplinary``. Pass ``"custom"`` together with
    ``platforms_override`` to use a hand-picked list.

    ``platforms_override`` is a list of platform names (e.g.
    ``["pubmed", "biorxiv", "openalex"]``) that overrides the preset.
    ``arxiv`` and ``semantic_scholar`` are always prepended automatically —
    they go through the existing ``mcp__arxiv__*`` / ``paic_s2_search`` path
    regardless of whether paper-search-mcp is installed.

    Both fields take effect only when ``providers.external_search.enabled``
    is ``true`` in ``~/.paic/config.yaml``. When disabled, ``/paic-search``
    falls back to the legacy two-source flow.
    """
    return workspace_tools.workspace_init(
        project_dir,
        title=title,
        venue=venue,
        deadline=deadline,
        domain_preset=domain_preset,
        platforms_override=platforms_override,
    )


@mcp.tool()
def paic_workspace_status(project_dir: str | None = None) -> dict[str, Any]:
    """Snapshot of the PAI-C project at ``project_dir`` (or auto-detect from cwd).

    Returns counts of library/ideas/experiments/reviews and a list of
    in-flight LangGraph runs.
    """
    return workspace_tools.workspace_status(project_dir)


# --- Library tools ---------------------------------------------------------

@mcp.tool()
def paic_s2_search(
    query: str,
    limit: int = 20,
    year_from: int | None = None,
    fields_of_study: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Search Semantic Scholar by free-text query.

    Use this to broaden coverage beyond arXiv (e.g. conference / journal venues).
    For arXiv-specific search use ``mcp__arxiv__search_papers`` instead.

    Args:
        query: free-text query
        limit: max results (1-100)
        year_from: only papers from this year onward
        fields_of_study: e.g. ["Computer Science", "Medicine"]
        force: bypass disk cache
    """
    return library_tools.s2_search_tool(
        query,
        limit=limit,
        year_from=year_from,
        fields_of_study=fields_of_study,
        force=force,
    )


@mcp.tool()
def paic_dedupe(papers: list[dict[str, Any]]) -> dict[str, Any]:
    """Deduplicate a list of paper records by id (arxiv/doi/s2) and fuzzy title.

    Returns ``{unique: [...], duplicate_groups: [[idx, idx, ...], ...]}``
    where each group lists indices in the input that collapsed together.
    """
    return library_tools.dedupe_tool(papers)


@mcp.tool()
def paic_library_add(
    project_dir: str,
    papers: list[dict[str, Any]],
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Add papers to the project's library/selected.yaml.

    Skips entries that already exist (matched by any of arxiv_id / doi / s2_id).
    Returns ``{added, skipped_duplicates, library_count}``.
    """
    return library_tools.library_add_tool(project_dir, papers, tags=tags)


@mcp.tool()
def paic_library_attach_paper(
    project_dir: str,
    paper: dict[str, Any],
    source_path: str | None = None,
) -> dict[str, Any]:
    """Copy a downloaded paper into ``<project>/.paic/library/pdfs/<cite_key>.<ext>`` (§24).

    Two modes:

    - **arxiv auto-locate** (when ``source_path`` is None and ``paper`` has
      ``arxiv_id``): finds the markdown via the configured arxiv MCP storage
      paths and copies it to ``library/pdfs/<cite_key>.md``.
    - **explicit source** (when ``source_path`` is given): copies that file
      to ``library/pdfs/<cite_key>.<ext>``, where ``<ext>`` comes from the
      source's suffix.

    Idempotent — skips silently if the destination already exists. The SKILL
    layer normally calls this only for arxiv ingest (paper-search-mcp's
    download tools accept ``save_path`` directly, so they write to the
    project-local path without needing a separate attach step).

    Returns ``{cite_key, dest_path, copied, ext, source_path}`` on success
    or an ``error`` dict (``project_not_initialized`` / ``cannot_derive_cite_key`` /
    ``source_not_found`` / ``copy_failed``).
    """
    return attach_tools.library_attach_paper_tool(
        project_dir, paper, source_path=source_path
    )


# --- arxiv pacing helper ---------------------------------------------------

@mcp.tool()
def paic_arxiv_pace(seconds: float | None = None) -> dict[str, Any]:
    """Sleep to throttle successive arxiv MCP calls (download/read/search).

    Voluntary coordination tool — call this *between* successive arxiv MCP
    invocations to stay under arxiv.org's recommended 1 req / 3 seconds.

    PAI-C cannot intercept ``mcp__arxiv__*`` calls (they go from Claude Code
    directly to the arxiv MCP, bypassing this Python process), so the Skill
    prompt is responsible for invoking this tool. If you skip it, no gating
    happens and you may trigger 429 / soft-block from arxiv.org.

    Args:
        seconds: how long to sleep. If omitted, defaults to
            ``providers.arxiv.inter_batch_delay_sec`` from
            ``~/.paic/config.yaml`` (default 3.0). Capped at 30s.

    Returns:
        ``{"slept_sec": float, "default_used": bool}``.
    """
    return pacing_tools.arxiv_pace_run(seconds=seconds)


# --- Multi-platform search helpers (§18) -----------------------------------

@mcp.tool()
def paic_search_pace(
    platform: str,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Sleep to throttle successive paper-search-mcp calls to ``platform``.

    Voluntary coordination tool — call this *between* successive
    ``mcp__paper_search__search_<platform>`` invocations to stay under each
    upstream's rate limit. PAI-C cannot intercept those calls (they go from
    Claude Code directly to paper-search-mcp), so the Skill prompt must
    invoke this tool between platform fan-out steps. If skipped, you may
    trigger 429 / soft-block from the upstream platform.

    Args:
        platform: upstream name (``pubmed`` / ``biorxiv`` / ``openalex`` /
            ``crossref`` / ``ssrn`` / ``google_scholar`` / …). Free-form
            string; unknown platforms use a 1.0s fallback.
        seconds: explicit sleep duration. If omitted, defaults to
            ``providers.external_search.inter_call_delay_sec[platform]``
            from ``~/.paic/config.yaml``. Capped at 30s.

    Returns:
        ``{"slept_sec": float, "platform": str, "default_used": bool}``.
    """
    return pacing_tools.search_pace_run(platform=platform, seconds=seconds)


@mcp.tool()
def paic_search_strategy(project_dir: str | None = None) -> dict[str, Any]:
    """Resolve the active multi-platform search strategy for the project.

    Reads ``providers.external_search.*`` from ``~/.paic/config.yaml`` and
    the project's ``.paic/project.yaml`` to figure out:

    * whether external search is enabled at all
    * which platforms to fan out to (project override > preset > global default)
    * per-platform pacing values
    * any warnings (missing optional API keys, etc.)

    The Skill layer should call this once at the top of ``/paic-search``,
    iterate over the returned ``platforms`` list calling each platform's
    search tool followed by ``paic_search_pace(platform=...)``, then dedupe.

    When external search is disabled, returns
    ``{"enabled": false, "platforms": ["arxiv", "semantic_scholar"], ...}``
    so the Skill can short-circuit to the legacy two-source flow.
    """
    target = project_dir if project_dir is not None else "."
    return strategy_tools.search_strategy_run(project_dir=target)


# --- Summarize tool --------------------------------------------------------

@mcp.tool()
def paic_summarize_run(
    project_dir: str,
    paper_id: str,
    force: bool = False,
    paper_text: str | None = None,
) -> dict[str, Any]:
    """Generate a structured ``PaperSummary`` for a paper in the project library.

    Reads the markdown that the arXiv MCP downloaded (under any configured
    storage path) and asks Claude to fill the structured summary schema.
    Writes ``library/summaries/<paper_id>.{md,yaml}``.

    If you already have the markdown (e.g. from ``mcp__arxiv__read_paper``),
    pass it via ``paper_text`` to skip the filesystem probe — useful when
    arxiv MCP's storage layout doesn't match any of PAI-C's configured roots.

    Returns ``{paper_id, summary_path, structured, from_cache, text_source}``
    on success or an ``error`` dict (``paper_markdown_not_found``,
    ``paper_not_in_library``, ``llm_unavailable``).

    **Host orchestration mode** (when ``routing.overrides.summarize: host``
    in ``~/.paic/config.yaml``): no LLM call is made. Instead returns
    ``{mode: "host_orchestration", markdown, schema_hint, instructions,
    next_tool}`` — the Skill layer must generate the structured fields in
    the main Claude Code conversation and call ``paic_summarize_persist``.
    """
    return summarize_tools.summarize_run(
        project_dir, paper_id, force=force, paper_text=paper_text
    )


@mcp.tool()
def paic_summarize_persist(
    project_dir: str,
    paper_id: str,
    structured: dict[str, Any],
    summarizer_model: str | None = None,
) -> dict[str, Any]:
    """Persist a host-generated PaperSummary to disk (LLM-free).

    Validates ``structured`` against the summary schema (problem / method /
    key_results / limitations / techniques + optional relevance_to_project)
    and writes the same ``library/summaries/<paper_id>.{md,yaml}`` pair as
    ``paic_summarize_run``.

    Companion to ``paic_summarize_run`` in host orchestration mode: when the
    main Claude Code conversation has finished generating the structured
    JSON, call this tool to validate and persist it. Schema validation
    failures are returned as ``error: "schema_validation_failed"`` with a
    ``detail`` list — fix the JSON and retry.
    """
    return summarize_tools.summarize_persist(
        project_dir, paper_id, structured, summarizer_model=summarizer_model
    )


# --- Ideate tools ----------------------------------------------------------

@mcp.tool()
def paic_ideate_start(
    project_dir: str,
    focus: str | None = None,
    n_candidates: int = 8,
    paper_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Start an ideation run.

    Reads the project's ``library/summaries/*.yaml``, asks Claude to brainstorm
    ``n_candidates`` ideas grounded in those summaries, scores them, and pauses
    for the user to filter (the graph hits an interrupt at ``await_user_filter``).

    Returns ``{run_id, thread_id, status, current_node, preview_ideas, ...}``.
    Resume with ``paic_ideate_step``.
    """
    return ideate_tools.ideate_start(
        project_dir, focus=focus, n_candidates=n_candidates, paper_ids=paper_ids
    )


@mcp.tool()
def paic_ideate_step(
    project_dir: str,
    run_id: str,
    keep: list[int] | None = None,
    feedback: str | None = None,
) -> dict[str, Any]:
    """Resume an ideate run after the user has filtered drafts.

    ``keep`` is a list of indices into the ``preview_ideas`` from the start
    response. If omitted, all drafts are finalized. ``feedback`` is an
    optional Chinese/English comment recorded with the run.
    """
    return ideate_tools.ideate_step(project_dir, run_id, keep=keep, feedback=feedback)


# --- Experiment & review tools --------------------------------------------

@mcp.tool()
def paic_experiment_start(
    project_dir: str,
    idea_id: str,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Design an ExperimentPlan for an existing IdeaCard.

    Reads ``ideas/<idea_id>.yaml``, asks Claude to fill the experiment schema
    (research questions / hypotheses / datasets / baselines / proposed_method /
    metrics / ablations / compute_budget / success_criteria), and writes
    ``experiments/<experiment_id>.yaml``.

    ``constraints`` is an optional free-form dict (compute, deadline, allowed
    datasets) the LLM should respect when designing the plan.
    """
    return experiment_tools.experiment_start(project_dir, idea_id, constraints=constraints)


@mcp.tool()
def paic_review_start(
    project_dir: str,
    experiment_id: str,
    personas: list[str] | None = None,
    rounds: int = 2,
) -> dict[str, Any]:
    """Start a multi-agent review of an experiment.

    Runs through ``rounds`` rounds; each round, the configured ``personas``
    (default: all 4 — methodology / statistics / domain / reviewer2) each
    produce critiques, the moderator synthesizes them, and the graph pauses
    for an author rebuttal at ``await_user``.

    Resume the run with ``paic_review_step``. Inspect mid-run via
    ``paic_review_status``.
    """
    return review_tools.review_start(
        project_dir, experiment_id, personas=personas, rounds=rounds
    )


@mcp.tool()
def paic_review_step(
    project_dir: str,
    run_id: str,
    rebuttal: str | None = None,
    plan_diff: str | None = None,
    skip_to_verdict: bool = False,
) -> dict[str, Any]:
    """Continue a paused review run.

    ``rebuttal``: the author's response to this round's panel synthesis.
    ``plan_diff``: optional fully-patched experiment YAML (string) to replace
    the current plan before the next round.
    ``skip_to_verdict``: if True, terminate after the current round and emit
    the final verdict immediately.
    """
    return review_tools.review_step(
        project_dir,
        run_id,
        rebuttal=rebuttal,
        plan_diff=plan_diff,
        skip_to_verdict=skip_to_verdict,
    )


@mcp.tool()
def paic_review_status(project_dir: str, run_id: str) -> dict[str, Any]:
    """Read-only snapshot of a review run (without advancing it)."""
    return review_tools.review_status(project_dir, run_id)


# --- LaTeX draft tools (v0.1) ----------------------------------------------

@mcp.tool()
def paic_draft_fill(
    project_dir: str,
    template: str,
    idea_id: str,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Fill a LaTeX template (built-in or project-local) from an idea + experiment.

    Built-in templates (always available): ``cvpr`` / ``neurips`` / ``ieee``.
    Project-local templates under ``<project>/.paic/templates/<name>/`` are
    discovered automatically and override built-ins on name collision.

    Writes ``drafts/main.tex``, ``drafts/sections/*.tex``, and
    ``drafts/refs.bib`` derived from ``library/selected.yaml``. When the
    chosen template ships static assets (``.sty`` / ``.cls`` / ``.bst`` /
    images), those are auto-copied to ``drafts/`` so the user no longer
    needs to drop them in by hand. Does NOT compile —
    pdflatex / tectonic is still the user's responsibility.

    On unknown ``template``, returns ``{error: "unknown_template",
    available: [...], hint: ...}`` listing every discovered template.
    Use ``paic_draft_list_templates`` for the same listing without
    triggering an error.

    v0.2 will add ``paic_draft_polish``; v0.3 will add ``paic_draft_compose``.
    """
    return draft_tools.draft_fill_tool(
        project_dir, template, idea_id, experiment_id=experiment_id
    )


@mcp.tool()
def paic_draft_list_templates(project_dir: str) -> dict[str, Any]:
    """List every LaTeX template available to the project — built-in + project-local.

    Project-local templates live under ``<project>/.paic/templates/<name>/``;
    each must contain a ``main.tex.j2`` plus optional ``template.yaml`` metadata
    and any static assets the venue ships (``.sty`` / ``.cls`` / ``.bst`` /
    images). Project-local templates win on name collision with built-ins
    (the response flags those with ``overrides_builtin: true``).

    Use this at the top of ``/paic-draft fill`` so the user can see what's
    available before picking. Returns ``{templates: [...], templates_dir, project_dir}``.
    """
    return draft_tools.draft_list_templates_tool(project_dir)


@mcp.tool()
def paic_draft_scaffold(
    project_dir: str,
    name: str,
    base: str = "neurips",
) -> dict[str, Any]:
    """Scaffold a new project-local LaTeX template from a built-in starting point.

    Copies ``<base>/main.tex.j2`` to ``<project>/.paic/templates/<name>/main.tex.j2``
    and writes a starter ``template.yaml`` with placeholder metadata. The user
    then edits ``main.tex.j2`` (typically swapping the venue style file in
    ``\\usepackage{...}`` and the title/author block) and drops the venue's
    static files (``.sty`` / ``.cls`` / ``.bst``) into the same directory
    before running ``paic_draft_fill(template='<name>', ...)``.

    ``base`` must be one of the built-in template names (``cvpr`` / ``neurips``
    / ``ieee``). Returns ``{name, root, base, files_created, next_steps}``
    on success. Errors: ``invalid_name``, ``unknown_base``,
    ``template_already_exists``.
    """
    return draft_tools.draft_scaffold_tool(project_dir, name, base=base)


@mcp.tool()
def paic_draft_polish(
    project_dir: str,
    section: str,
    mode: str = "clarify",
    instruction: str | None = None,
    idea_id: str | None = None,
    experiment_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Polish one section file under ``drafts/sections/`` with an LLM rewrite.

    Reads the section, asks the LLM (routed via the ``draft_polish`` node
    label) to rewrite per ``mode``, validates the output (cite keys
    preserved, ``\\begin/\\end`` balanced, braces balanced), then overwrites
    the file leaving a timestamped ``.bak.<UTC>`` alongside.

    Args:
        section: section identifier — canonical (``01_intro``), alias
            (``intro``), basename (``01_intro.tex``), or path
            (``drafts/sections/01_intro.tex``).
        mode: one of ``tighten`` / ``clarify`` / ``formalize`` / ``expand`` /
            ``proofread``.
        instruction: optional freeform extra guidance (overrides / refines mode).
        idea_id / experiment_id: only used for ``mode='expand'`` — the
            polish call loads ``ideas/<idea_id>.yaml`` and
            ``experiments/<experiment_id>.yaml`` and threads their content
            into the prompt so the LLM can flesh out ``TODO`` placeholders
            with real claims.
        dry_run: if True, return the diff without writing or creating a backup.

    Returns ``{section, mode, original, original_hash, polished, diff,
    validation, wrote, backup_path}`` on success. Errors:
    ``project_not_initialized`` / ``section_not_found`` / ``section_empty`` /
    ``invalid_mode`` / ``llm_unavailable`` / ``latex_validation_failed``.

    **Host orchestration mode**: when ``routing.overrides.draft_polish: host``
    is set in ``~/.paic/config.yaml``, no LLM call is made. Returns
    ``{mode: "host_orchestration", original, original_hash, user_prompt,
    next_tool, instructions}`` — the SKILL hands the prompt to the main
    Claude Code conversation, then calls ``paic_draft_polish_persist`` with
    the result.
    """
    return draft_tools.draft_polish_tool(
        project_dir,
        section,
        mode=mode,
        instruction=instruction,
        idea_id=idea_id,
        experiment_id=experiment_id,
        dry_run=dry_run,
    )


@mcp.tool()
def paic_draft_polish_persist(
    project_dir: str,
    section: str,
    polished: str,
    original_hash: str,
) -> dict[str, Any]:
    """Persist a host-orchestration-generated polished section to disk (LLM-free).

    Companion to ``paic_draft_polish`` in host-orchestration mode. Re-reads
    the section file, verifies its sha256 still matches ``original_hash``
    (rejecting if the user edited the file mid-polish), runs the same
    structural validation as ``paic_draft_polish``, then overwrites with a
    timestamped ``.bak.<UTC>`` alongside.

    Errors: ``project_not_initialized`` / ``section_not_found`` /
    ``original_hash_mismatch`` / ``latex_validation_failed``.
    """
    return draft_tools.draft_polish_persist_tool(
        project_dir, section, polished, original_hash
    )


@mcp.tool()
def paic_draft_compose(
    project_dir: str,
    section: str,
    mode: str = "from_stub",
    idea_id: str | None = None,
    experiment_id: str | None = None,
    target_words: int | None = None,
    instruction: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compose a full LaTeX section from idea + experiment + library (v0.3, §21).

    Where ``paic_draft_polish`` rewrites an existing section, ``compose``
    generates the section from scratch (or from a stub) using:

    - the idea card at ``ideas/<idea_id>.yaml``
    - the experiment plan at ``experiments/<experiment_id>.yaml``
    - **the library**: every paper in ``library/selected.yaml`` is offered to
      the LLM as a citable source. The LLM must use ONLY these cite keys —
      anything else is rejected by the structural guard.

    Args:
        section: section identifier (canonical / alias / path; same as polish).
        mode: ``from_stub`` (default — flesh out the current file's TODOs) or
            ``from_scratch`` (ignore current file, generate fresh).
        idea_id: highly recommended; the idea card aligns the section's claims.
        experiment_id: recommended for ``method`` / ``experiments`` sections.
        target_words: soft length target passed to the LLM. None → use the
            built-in default for the section type.
        instruction: freeform extra guidance.
        dry_run: return diff + composed text without writing.

    Returns ``{section, section_name, mode, original, original_hash, composed,
    diff, cite_keys_used, validation, library_size, wrote, backup_path}`` on
    success. Errors: ``invalid_mode`` / ``section_not_found`` /
    ``empty_library`` / ``llm_unavailable`` / ``latex_validation_failed``
    (which includes ``cite_keys_missing_from_library`` so callers can ingest
    the missing papers and retry).

    **Host orchestration mode**: when ``routing.overrides.draft_compose: host``
    is set, returns ``{mode: "host_orchestration", original, original_hash,
    library_cite_keys, user_prompt, next_tool, instructions}`` — the SKILL
    hands the prompt to the main Claude Code conversation, then calls
    ``paic_draft_compose_persist`` with the result.
    """
    return draft_tools.draft_compose_tool(
        project_dir,
        section,
        mode=mode,
        idea_id=idea_id,
        experiment_id=experiment_id,
        target_words=target_words,
        instruction=instruction,
        dry_run=dry_run,
    )


@mcp.tool()
def paic_draft_compose_persist(
    project_dir: str,
    section: str,
    composed: str,
    original_hash: str,
) -> dict[str, Any]:
    """Persist a host-orchestration-generated composed section to disk (LLM-free).

    Companion to ``paic_draft_compose`` in host-orchestration mode. Validates
    that every ``\\cite{}`` in ``composed`` references a key from the project
    library, that ``\\begin/\\end`` and braces balance, and that the on-disk
    section's sha256 still matches ``original_hash`` (rejects if the user
    edited mid-flight). Then overwrites with a timestamped ``.bak.<UTC>``.

    Errors: ``project_not_initialized`` / ``section_not_found`` /
    ``original_hash_mismatch`` / ``latex_validation_failed`` (includes
    ``cite_keys_missing_from_library``).
    """
    return draft_tools.draft_compose_persist_tool(
        project_dir, section, composed, original_hash
    )


# --- Run management --------------------------------------------------------

@mcp.tool()
def paic_runs_list(
    project_dir: str | None = None,
    status_filter: str | None = None,
) -> dict[str, Any]:
    """List LangGraph runs across PAI-C projects.

    Filter by ``project_dir`` or by ``status_filter`` (one of
    ``running``, ``awaiting_input``, ``done``, ``error``, ``cancelled``).
    """
    return runs_tools.runs_list_tool(project_dir, status_filter)


@mcp.tool()
def paic_runs_resume(
    run_id: str,
    keep: list[int] | None = None,
    feedback: str | None = None,
    rebuttal: str | None = None,
    plan_diff: str | None = None,
    skip_to_verdict: bool = False,
) -> dict[str, Any]:
    """Resume any paused run by ``run_id`` (dispatches by kind).

    For ideate runs use ``keep`` + ``feedback``; for review runs use
    ``rebuttal`` / ``plan_diff`` / ``skip_to_verdict``.
    """
    return runs_tools.runs_resume_tool(
        run_id,
        keep=keep,
        feedback=feedback,
        rebuttal=rebuttal,
        plan_diff=plan_diff,
        skip_to_verdict=skip_to_verdict,
    )


@mcp.tool()
def paic_runs_cancel(run_id: str) -> dict[str, Any]:
    """Mark a run as cancelled (does not delete its checkpoint)."""
    return runs_tools.runs_cancel_tool(run_id)


# --- Figure pipeline (Phase 1: AI-generated raster figures) ---------------

@mcp.tool()
def paic_figure_plan(
    project_dir: str,
    draft_path: str | None = None,
    max_figures: int = 4,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Analyze paper context and propose ≤ ``max_figures`` figure slots.

    Reads a polished LaTeX draft when ``draft_path`` is given; otherwise
    falls back to the project's idea + experiment YAMLs. The proposed plan
    (slot, kind, section_hint, position_hint, scene_description,
    caption_hint, rationale) is written to ``.paic/figures/_plan.yaml``.

    AI image generators are bad at architecture diagrams, plots, and text
    rendering, so the planner only proposes raster-friendly figures
    (teaser / concept / domain). Returns the plan body so the caller can
    render it without a separate file read.

    Refuses to clobber an existing plan unless ``overwrite=True``.
    """
    return figure_tools.figure_plan(
        project_dir,
        draft_path=draft_path,
        max_figures=max_figures,
        overwrite=overwrite,
    )


@mcp.tool()
def paic_figure_generate(
    project_dir: str,
    slot: str,
    description: str | None = None,
    free_slot: bool = False,
    n: int = 1,
) -> dict[str, Any]:
    """Render an image for one slot via the configured image backend.

    By default ``slot`` must exist in ``_plan.yaml``. Pass ``free_slot=True``
    plus a ``description`` to generate a one-off slot without planning.
    Returns the saved version label, png path, the synthesized image
    prompt, and a ready-to-paste LaTeX ``\\begin{figure}...\\end{figure}``
    snippet referencing ``figures/<slot>/<version>.png``.
    """
    return figure_tools.figure_generate(
        project_dir,
        slot,
        description=description,
        free_slot=free_slot,
        n=n,
    )


@mcp.tool()
def paic_figure_edit(
    project_dir: str,
    slot: str,
    instruction: str,
    parent_version: str | None = None,
) -> dict[str, Any]:
    """Edit an existing slot version with a natural-language instruction.

    Default parent is the latest version on disk. Bumps the version with
    a ``_edit`` suffix (e.g. ``v2_edit.png``) and records the parent in
    meta.yaml. Requires a model that supports image edits
    (``gpt-image-1`` / ``dall-e-2``); ``dall-e-3`` returns
    ``edit_not_supported``.
    """
    return figure_tools.figure_edit(
        project_dir,
        slot,
        instruction,
        parent_version=parent_version,
    )


@mcp.tool()
def paic_figure_variant(
    project_dir: str,
    slot: str,
    n: int = 2,
    parent_version: str | None = None,
) -> dict[str, Any]:
    """Produce ``n`` variations of a slot's latest (or specified) version.

    For ``dall-e-2`` uses the native ``/variations`` endpoint. For
    ``gpt-image-1`` falls back to ``/edits`` with an "alternative
    variation" instruction (the API has no native variations endpoint
    for that model).
    """
    return figure_tools.figure_variant(
        project_dir,
        slot,
        n=n,
        parent_version=parent_version,
    )


@mcp.tool()
def paic_figure_list(project_dir: str) -> dict[str, Any]:
    """Return the figure plan + per-slot version counts.

    Includes "free slots" (directories with figures but no plan entry,
    typically created via ``free_slot=True`` on generate) for
    completeness.
    """
    return figure_tools.figure_list(project_dir)


def main() -> None:
    """Entry point for the ``paic-mcp`` console script."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()

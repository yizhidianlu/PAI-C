"""Tests for §quality phase 6 — paragraph-level compose pipeline.

Mocks LLM calls; no network. Validates the outline → write → polish
three-step pipeline + the ``mode="paragraph"`` route through
``compose_section``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.latex.paragraph_compose import (
    ParagraphSpec,
    _OutlineFields,
    compose_section_paragraphs,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


class _ResponseStub:
    """Minimal LLM response object."""

    def __init__(self, text: str):
        self.text = text


class _PipelineLLM:
    """Stub LLM that drives the 3-stage pipeline.

    Returns:
    - _OutlineFields when called via complete_json (outline step).
    - A canned paragraph when called via complete (write step + polish step).
    """

    model = "stub-paragraph"

    def __init__(self, specs: list[ParagraphSpec], paragraph_text: str = "Generated paragraph.",
                 polish_text: str | None = None):
        self.specs = specs
        self.paragraph_text = paragraph_text
        self.polish_text = polish_text
        self.complete_calls: list[str] = []
        self.complete_json_calls: list[str] = []

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        self.complete_json_calls.append(node)
        assert schema is _OutlineFields
        return _OutlineFields(paragraphs=self.specs)

    def complete(self, *, system, user, max_tokens=2400, temperature=0.0, node=None):
        self.complete_calls.append(node)
        if node == "section_coherence_polish":
            text = self.polish_text or "\n\n".join(
                f"polished_{i}" for i in range(len(self.specs))
            )
        else:
            # write_paragraph node — return a paragraph that mentions
            # which spec is being written (assists assertions).
            spec_idx = sum(
                1 for c in self.complete_calls if c == "paragraph_write"
            ) - 1
            text = f"Paragraph for spec P{spec_idx + 1}: {self.paragraph_text}"
        return _ResponseStub(text)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


# ----------------------------------------------------- spec schema


def test_paragraph_spec_round_trip():
    spec = ParagraphSpec(
        id="P1", role="motivation", intent="Set up the problem.",
        claim_ids=["CL1"], cite_key_candidates=["arxiv_p1"], target_words=120,
    )
    dumped = spec.model_dump(mode="json")
    reloaded = ParagraphSpec.model_validate(dumped)
    assert reloaded.id == "P1"
    assert reloaded.role == "motivation"


def test_paragraph_spec_invalid_role_rejected():
    with pytest.raises(Exception):
        ParagraphSpec(
            id="P1", role="not_a_role",  # type: ignore[arg-type]
            intent="x", target_words=100,
        )


# ----------------------------------------------------- pipeline


def test_pipeline_runs_outline_then_paragraphs_then_polish():
    specs = [
        ParagraphSpec(id="P1", role="motivation", intent="Set up.",
                      claim_ids=["CL1"], cite_key_candidates=["a"], target_words=100),
        ParagraphSpec(id="P2", role="contrast", intent="Compare to prior.",
                      claim_ids=["CL2"], cite_key_candidates=["b", "c"], target_words=150),
    ]
    llm = _PipelineLLM(specs)
    result = compose_section_paragraphs(
        section="01_intro",
        paper_plan={"thesis": "T"},
        idea={"title": "X"},
        experiment=None,
        retrieval_hits=[
            {"cite_key": "a", "title": "A", "snippet": "..."},
            {"cite_key": "b", "title": "B", "snippet": "..."},
            {"cite_key": "c", "title": "C", "snippet": "..."},
        ],
        claims=[
            {"id": "CL1", "type": "novelty", "text": "Claim 1.", "status": "needs_evidence"},
            {"id": "CL2", "type": "comparative", "text": "Claim 2.", "status": "needs_evidence"},
        ],
        target_words=400,
        llm=llm,
    )
    # 1 outline call + 2 paragraph writes + 1 polish = 1 complete_json + 3 completes
    assert llm.complete_json_calls == ["paragraph_outline"]
    assert llm.complete_calls == ["paragraph_write", "paragraph_write", "section_coherence_polish"]
    assert len(result.specs) == 2
    assert result.section_text  # non-empty
    # Default polish text concatenates per-spec stubs.
    assert "polished" in result.section_text


def test_pipeline_skip_polish_returns_raw_paragraphs():
    specs = [
        ParagraphSpec(id="P1", role="motivation", intent="x", target_words=100),
    ]
    llm = _PipelineLLM(specs)
    result = compose_section_paragraphs(
        section="01_intro",
        paper_plan=None, idea=None, experiment=None,
        retrieval_hits=[], claims=[], target_words=100,
        llm=llm, skip_polish=True,
    )
    assert "section_coherence_polish" not in llm.complete_calls
    assert "Paragraph for spec P1" in result.section_text


def test_pipeline_no_specs_skips_polish():
    """Edge case: outline returns 0 paragraphs (LLM hiccup) — pipeline should
    not call polish on an empty input."""
    llm = _PipelineLLM([])
    result = compose_section_paragraphs(
        section="01_intro",
        paper_plan=None, idea=None, experiment=None,
        retrieval_hits=[], claims=[], target_words=100,
        llm=llm,
    )
    assert llm.complete_json_calls == ["paragraph_outline"]
    assert llm.complete_calls == []
    assert result.paragraphs == []


def test_pipeline_paragraph_sees_previous_paragraphs(project):
    """The write_paragraph LLM call should receive prior paragraphs in its
    user message — verified by the paragraph_idx-based stub text."""
    specs = [
        ParagraphSpec(id="P1", role="motivation", intent="x", target_words=80),
        ParagraphSpec(id="P2", role="contrast", intent="y", target_words=80),
        ParagraphSpec(id="P3", role="result", intent="z", target_words=80),
    ]
    captured: list[str] = []

    class _CapturingLLM(_PipelineLLM):
        def complete(self, *, system, user, max_tokens=2400, temperature=0.0, node=None):
            if node == "paragraph_write":
                captured.append(user)
            return super().complete(
                system=system, user=user, max_tokens=max_tokens,
                temperature=temperature, node=node,
            )

    llm = _CapturingLLM(specs)
    compose_section_paragraphs(
        section="01_intro",
        paper_plan=None, idea=None, experiment=None,
        retrieval_hits=[], claims=[], target_words=300,
        llm=llm,
    )
    # First paragraph write has empty prev list.
    assert "this is the first paragraph" in captured[0]
    # Third paragraph write has previous paragraphs.
    assert "prev #1" in captured[2]


# ----------------------------------------------------- compose_section route


def _seed_project(project_dir, with_paper_plan: bool = False, with_library: bool = False):
    """Set up a project with sections + optional plan + optional library."""
    paths = resolve_project(str(project_dir))
    section_path = paths.drafts_dir / "sections" / "01_intro.tex"
    section_path.parent.mkdir(parents=True, exist_ok=True)
    section_path.write_text("Original stub.\n", encoding="utf-8")
    if with_paper_plan:
        save_yaml(paths.paper_plan_yaml, {
            "thesis": "Test thesis.",
            "contributions": [{"id": "C1", "title": "X", "description": "y"}],
            "section_plan": [{"name": "01_intro", "intent": "Motivate.", "supports_contributions": ["C1"]}],
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        })
    if with_library:
        from paic.mcp_server.tools.library import library_add_tool
        library_add_tool(str(project_dir), [
            {"arxiv_id": "lib1", "title": "Library Paper One",
             "abstract": "About something relevant.", "authors": ["A"]},
        ])


def test_compose_section_paragraph_mode_writes_section(project):
    from paic.latex.compose import compose_section
    _seed_project(project, with_paper_plan=True, with_library=True)
    paths = resolve_project(str(project))
    specs = [ParagraphSpec(id="P1", role="motivation",
                           intent="x", target_words=100)]
    llm = _PipelineLLM(specs, polish_text="Polished section text.\n")
    out = compose_section(
        paths,
        section="01_intro",
        mode="paragraph",
        llm=llm,
        dry_run=True,
    )
    assert "error" not in out, out
    assert out["mode"] == "paragraph"
    assert out["paragraph_count"] >= 1
    assert out["outline_spec_count"] == 1
    assert "Polished section text." in out["composed"]


def test_compose_section_paragraph_mode_invalid_mode_error_caught_elsewhere(project):
    """Mode validation for "paragraph" succeeds; only unknown modes still error."""
    from paic.latex.compose import compose_section
    _seed_project(project, with_library=True)
    paths = resolve_project(str(project))
    out = compose_section(
        paths, section="01_intro", mode="bogus",
    )
    assert out["error"] == "invalid_mode"
    assert "paragraph" in out["valid_modes"]


def test_compose_section_paragraph_handles_no_retrieval(project):
    """Paragraph mode with empty library still runs (specs may have empty
    cite_key_candidates)."""
    from paic.latex.compose import compose_section
    _seed_project(project, with_library=False)
    # Skip the empty-library guard by using a section that allows empty lib.
    paths = resolve_project(str(project))
    section_path = paths.drafts_dir / "sections" / "00_abstract.tex"
    section_path.parent.mkdir(parents=True, exist_ok=True)
    section_path.write_text("Stub.\n", encoding="utf-8")

    specs = [ParagraphSpec(id="P1", role="summary",
                           intent="x", target_words=80)]
    llm = _PipelineLLM(specs, polish_text="Abstract text.\n")
    out = compose_section(
        paths, section="00_abstract", mode="paragraph",
        llm=llm, dry_run=True,
    )
    assert "error" not in out, out
    assert out["mode"] == "paragraph"


# ----------------------------------------------------- chunk-level grounding (P0 #1)


def test_paragraph_write_prompt_includes_chunk_passages():
    """When retrieval_hits carry chunks, the write_paragraph user message
    must include a "Cite_key passages" block with the chunk text.

    Tests the prompt-formatter directly so the assertion is independent of
    BM25 retrieval ordering (which is corpus-shape-dependent).
    """
    from paic.latex.paragraph_compose import write_paragraph
    spec = ParagraphSpec(
        id="P1", role="motivation",
        intent="Motivate Fisher channel ranking.",
        cite_key_candidates=["arxiv_lib2"],
        target_words=120,
    )
    retrieval_hits = [
        {
            "cite_key": "arxiv_lib2",
            "title": "Fisher channel ranking",
            "snippet": "Fisher score channel ranking",
            "match_reason": ["fisher", "channel"],
            "chunks": [
                {
                    "chunk_id": "arxiv_lib2__c000",
                    "section_path": "Method",
                    "text": (
                        "We apply Fisher discriminant score to rank EEG "
                        "channels under cross-subject distribution shift."
                    ),
                },
                {
                    "chunk_id": "arxiv_lib2__c001",
                    "section_path": "Background",
                    "text": "Generic background about prior pruning approaches.",
                },
            ],
        },
    ]
    captured: list[str] = []

    class _CapturingLLM:
        model = "stub"

        def complete(self, *, system, user, max_tokens=2400, temperature=0.0, node=None):
            captured.append(user)
            return _ResponseStub("paragraph text")

    write_paragraph(
        spec=spec,
        prev_paragraphs=[],
        section="01_intro",
        paper_plan=None,
        idea=None,
        retrieval_hits=retrieval_hits,
        claims=[],
        llm=_CapturingLLM(),
    )
    assert captured
    user_msg = captured[0]
    assert "Cite_key passages" in user_msg
    # Each chunk's body and section_path should be reachable to the LLM.
    assert "Fisher discriminant" in user_msg
    assert "(Method)" in user_msg


def test_paragraph_write_falls_back_to_snippet_when_no_chunks():
    """Backward compatibility: papers without a chunk index (older projects)
    still produce a usable write prompt — the snippet falls back into place.
    """
    from paic.latex.paragraph_compose import write_paragraph
    spec = ParagraphSpec(
        id="P1", role="motivation", intent="x",
        cite_key_candidates=["arxiv_legacy"], target_words=100,
    )
    retrieval_hits = [
        {
            "cite_key": "arxiv_legacy",
            "title": "Old paper",
            "snippet": "Legacy snippet text from selected.yaml",
            "match_reason": [],
            # no chunks key (older retrieval response shape)
        },
    ]
    captured: list[str] = []

    class _CapturingLLM:
        model = "stub"

        def complete(self, *, system, user, max_tokens=2400, temperature=0.0, node=None):
            captured.append(user)
            return _ResponseStub("paragraph")

    write_paragraph(
        spec=spec, prev_paragraphs=[], section="01_intro",
        paper_plan=None, idea=None,
        retrieval_hits=retrieval_hits, claims=[], llm=_CapturingLLM(),
    )
    assert captured
    assert "Legacy snippet text" in captured[0]


def test_paragraph_outline_prompt_includes_chunk_excerpt():
    """Outline prompt should surface a chunk preview so the outliner picks
    cite_keys whose actual content matches the section."""
    from paic.latex.paragraph_compose import outline_section
    retrieval_hits = [
        {
            "cite_key": "arxiv_p1",
            "title": "Some Paper",
            "snippet": "snippet",
            "match_reason": [],
            "chunks": [
                {
                    "chunk_id": "arxiv_p1__c000",
                    "section_path": "Method",
                    "text": "We rank EEG channels by Fisher discriminant scores.",
                },
            ],
        },
    ]
    captured: list[str] = []

    class _CapturingLLM:
        model = "stub"

        def complete_json(self, *, system, user, schema, max_tokens=2048, temperature=0.0, node=None):
            captured.append(user)
            return _OutlineFields(paragraphs=[])

    outline_section(
        section="01_intro",
        paper_plan=None, idea=None, experiment=None,
        retrieval_hits=retrieval_hits, claims=[], target_words=400,
        llm=_CapturingLLM(),
    )
    assert captured
    outline_msg = captured[0]
    assert "excerpt" in outline_msg.lower()
    assert "Fisher discriminant" in outline_msg

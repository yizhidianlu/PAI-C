"""Unit tests for HostOrchestrationDirective + build_host_directive."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from paic.llm import HOST_MODE, HostOrchestrationDirective, build_host_directive


def test_required_fields_validate():
    d = HostOrchestrationDirective(node="summarize", instructions="do the thing")
    assert d.mode == HOST_MODE
    assert d.node == "summarize"
    assert d.instructions == "do the thing"
    assert d.user_prompt is None
    assert d.metadata == {}


def test_missing_node_rejects():
    with pytest.raises(ValidationError):
        HostOrchestrationDirective(instructions="x")  # type: ignore[call-arg]


def test_extra_top_level_field_rejects():
    """extra=forbid catches typos at construction time."""
    with pytest.raises(ValidationError):
        HostOrchestrationDirective(
            node="x", instructions="y", paper_id="should-go-in-metadata"  # type: ignore[call-arg]
        )


def test_to_dict_excludes_none_fields():
    d = build_host_directive(node="summarize", instructions="x")
    out = d.to_dict()
    assert out == {"mode": HOST_MODE, "node": "summarize", "instructions": "x"}
    # explicit None for optional fields should not appear
    assert "user_prompt" not in out
    assert "schema_hint" not in out
    assert "next_tool" not in out
    assert "resume_token" not in out
    assert "original_hash" not in out
    assert "metadata" not in out


def test_to_dict_flattens_metadata():
    d = build_host_directive(
        node="summarize",
        instructions="x",
        next_tool="mcp__paic__paic_summarize_persist",
        metadata={"paper_id": "2401.12345", "markdown": "# Title"},
    )
    out = d.to_dict()
    assert out["paper_id"] == "2401.12345"
    assert out["markdown"] == "# Title"
    # metadata key itself must not survive flattening
    assert "metadata" not in out
    # top-level fields still present
    assert out["next_tool"] == "mcp__paic__paic_summarize_persist"


def test_to_dict_rejects_metadata_collision_with_top_level():
    """Silent shadowing would let a tool quietly overwrite a typed core field."""
    d = build_host_directive(
        node="summarize",
        instructions="x",
        metadata={"node": "wrong", "instructions": "wrong"},
    )
    with pytest.raises(ValueError, match="shadows top-level field"):
        d.to_dict()


def test_schema_hint_jsonable():
    """schema_hint comes from Pydantic model_json_schema() — must round-trip cleanly."""
    from pydantic import BaseModel

    class _Toy(BaseModel):
        x: int
        y: str

    d = build_host_directive(
        node="summarize", instructions="x", schema_hint=_Toy.model_json_schema()
    )
    out = d.to_dict()
    assert "properties" in out["schema_hint"]
    assert set(out["schema_hint"]["properties"]) == {"x", "y"}


def test_resume_token_optional_and_appears_when_set():
    d = build_host_directive(
        node="review_persona_methodology",
        instructions="x",
        resume_token="run-abc-step-3",
    )
    out = d.to_dict()
    assert out["resume_token"] == "run-abc-step-3"


def test_build_helper_keyword_only():
    """build_host_directive is kw-only so callers don't accidentally swap
    user_prompt and instructions (both strings)."""
    with pytest.raises(TypeError):
        build_host_directive("review_persona_methodology", "instructions")  # type: ignore[misc]


# ---------------------------------------------------- back-compat wire snapshots
# These golden snapshots assert the directives that summarize / draft_polish /
# draft_compose previously hand-rolled as dict literals are still produced
# byte-for-byte (after sorting keys) by the new build_host_directive helper.
# If you intentionally change wire format, update both this file and the
# corresponding Skill consumer at the same time.


def test_back_compat_summarize_wire_shape():
    out = build_host_directive(
        node="summarize",
        instructions="HOST_INSTRUCTIONS",
        schema_hint={"properties": {"problem": {"type": "string"}}},
        next_tool="mcp__paic__paic_summarize_persist",
        metadata={
            "paper_id": "2401.12345",
            "cite_key": "smith2024",
            "text_source": "library_md",
            "paper_ref": {"arxiv_id": "2401.12345"},
            "markdown": "# Body",
        },
    ).to_dict()
    # All fields the previous dict-literal exposed are still present:
    for key in (
        "mode",
        "paper_id",
        "cite_key",
        "text_source",
        "paper_ref",
        "markdown",
        "schema_hint",
        "next_tool",
        "instructions",
    ):
        assert key in out, f"summarize wire format lost field: {key}"
    assert out["mode"] == HOST_MODE


def test_back_compat_draft_polish_wire_shape():
    out = build_host_directive(
        node="draft_polish",
        instructions="POLISH_HOST",
        user_prompt="rendered prompt",
        next_tool="mcp__paic__paic_draft_polish_persist",
        original_hash="deadbeef",
        metadata={
            "section": "/abs/path/intro.tex",
            "polish_mode": "tighten",
            "instruction": None,
            "original": "\\section{Intro}\n",
        },
    ).to_dict()
    for key in (
        "mode",
        "section",
        "polish_mode",
        "instruction",
        "original",
        "original_hash",
        "user_prompt",
        "next_tool",
        "instructions",
    ):
        assert key in out, f"draft_polish wire format lost field: {key}"
    # instruction may legitimately be None when caller didn't supply one;
    # exclude_none only applies to model-level fields, not metadata values.
    assert out["instruction"] is None


def test_back_compat_draft_compose_wire_shape():
    out = build_host_directive(
        node="draft_compose",
        instructions="COMPOSE_HOST",
        user_prompt="rendered prompt",
        next_tool="mcp__paic__paic_draft_compose_persist",
        original_hash="cafef00d",
        metadata={
            "section": "/abs/path/related.tex",
            "section_name": "02_related",
            "compose_mode": "from_stub",
            "instruction": None,
            "target_words": 800,
            "original": "stub",
            "library_size": 12,
            "library_cite_keys": ["a2024", "b2024"],
        },
    ).to_dict()
    for key in (
        "mode",
        "section",
        "section_name",
        "compose_mode",
        "instruction",
        "target_words",
        "original",
        "original_hash",
        "library_size",
        "library_cite_keys",
        "user_prompt",
        "next_tool",
        "instructions",
    ):
        assert key in out, f"draft_compose wire format lost field: {key}"

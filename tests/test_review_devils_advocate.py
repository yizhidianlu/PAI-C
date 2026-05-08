"""Tests for the 5-panel review with the Devil's Advocate persona (ARS-fusion P1-3)."""

from __future__ import annotations

import pytest

from paic.personas import PERSONA_NAMES, load_persona
from paic.schemas.review import PersonaName


def test_personas_now_5():
    assert PERSONA_NAMES == (
        "methodology",
        "statistics",
        "domain",
        "reviewer2",
        "devils_advocate",
    )


def test_devils_advocate_prompt_loads():
    text = load_persona("devils_advocate")
    assert text
    # Must be anchored on the persona header so the test stub / moderator
    # synthesis can disambiguate it from the moderator prompt.
    assert "# Persona: Devil's Advocate" in text
    # Must mention the role we expect the agent to play
    assert "counter-argument" in text.lower() or "fallacy" in text.lower()


def test_persona_name_literal_includes_devils_advocate():
    """PersonaName Literal must accept the new value (compile-time check via runtime)."""
    # Pydantic Literal types validate at value level — we instantiate a
    # Critique-shaped dict and ensure pydantic accepts devils_advocate.
    from datetime import UTC, datetime

    from paic.schemas.review import Critique
    Critique(
        persona="devils_advocate",
        round=1,
        severity="major",
        category="overgeneralization",
        issue="The thesis claims X but only experiment Y was run.",
        suggestion="Add an experiment Z to support X, or narrow the thesis.",
        created_at=datetime.now(UTC),
    )  # No exception → Literal validation passes


def test_load_persona_rejects_unknown_name():
    with pytest.raises(ValueError, match="Unknown persona"):
        load_persona("philosopher")


def test_devils_advocate_in_host_supported_nodes():
    """The router must accept ``review_persona_devils_advocate`` as a host node."""
    from paic.config import HOST_SUPPORTED_NODES
    assert "review_persona_devils_advocate" in HOST_SUPPORTED_NODES


def test_moderator_prompt_mentions_devils_advocate_priority_rule():
    """Moderator must know to treat devils_advocate as must-fix-tier."""
    from paic.llm.prompts import load_prompt
    text = load_prompt("review_moderator")
    assert "devils_advocate" in text
    assert "must-fix-tier" in text or "Devil's Advocate priority rule" in text


def test_review_graph_default_personas_includes_devils_advocate():
    """When no `personas=` is provided, the graph should default to all 5."""
    from paic.personas import PERSONA_NAMES
    assert "devils_advocate" in PERSONA_NAMES
    # The review_graph defaults via `state.get("personas") or list(PERSONA_NAMES)` —
    # confirm the default list length is 5.
    assert len(PERSONA_NAMES) == 5

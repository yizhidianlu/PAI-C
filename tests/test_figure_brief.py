"""Tests for build_figure_user_prompt — brief injection into the
figure_prompt LLM message (and host directive user_prompt). Covers
paper_plan / claims_by_id wiring and back-compat when both are None."""

from __future__ import annotations

from paic.images.planner import FigureSlot
from paic.images.prompt import build_figure_user_prompt


def _slot(**overrides) -> FigureSlot:
    base = dict(
        slot="teaser",
        kind="teaser",
        section_hint="01_intro",
        position_hint="page 1, after abstract",
        scene_description="A robotic arm placing colored tiles on a grid.",
        caption_hint="Overview of the proposed approach.",
        rationale="Visual metaphor for the algorithm's core mechanism.",
        supporting_claims=(),
        primary_claim_id=None,
    )
    base.update(overrides)
    return FigureSlot(**base)


def test_no_brief_sources_falls_back_to_five_fields():
    """Old behavior preserved when paper_plan + claims are None."""
    user = build_figure_user_prompt(_slot())
    # Five core fields present
    assert "slot: teaser" in user
    assert "kind: teaser" in user
    assert "section: 01_intro" in user
    assert "scene_description: A robotic arm" in user
    assert "caption_hint: Overview" in user
    # None of the new brief blocks
    assert "[Supporting claims]" not in user
    assert "[Section intent]" not in user
    assert "[Paper terminology]" not in user


def test_supporting_claims_renders_block_with_primary_first():
    """primary_claim_id surfaces as PRIMARY tag, others as ALSO; primary on top."""
    slot = _slot(
        supporting_claims=("CL2", "CL5"),
        primary_claim_id="CL5",
    )
    claims_by_id = {
        "CL2": {"id": "CL2", "type": "factual", "text": "Tiles are colored."},
        "CL5": {
            "id": "CL5",
            "type": "comparative",
            "text": "Our placement strategy beats baseline by 3.2%.",
        },
    }
    user = build_figure_user_prompt(_slot(supporting_claims=("CL2", "CL5"),
                                         primary_claim_id="CL5"),
                                    claims_by_id=claims_by_id)
    assert "[Supporting claims]" in user
    primary_idx = user.index("PRIMARY [CL5]")
    also_idx = user.index("ALSO [CL2]")
    assert primary_idx < also_idx, "PRIMARY claim must precede ALSO claims"
    assert "Our placement strategy beats baseline" in user
    assert "Tiles are colored." in user


def test_supporting_claims_without_primary_renders_all_as_also():
    """primary_claim_id=None: everything tagged ALSO, ordered by supporting_claims tuple."""
    slot = _slot(supporting_claims=("CL1", "CL2"), primary_claim_id=None)
    claims_by_id = {
        "CL1": {"id": "CL1", "type": "novelty", "text": "First system to do X."},
        "CL2": {"id": "CL2", "type": "factual", "text": "EEG signals are noisy."},
    }
    user = build_figure_user_prompt(slot, claims_by_id=claims_by_id)
    assert "PRIMARY" not in user
    assert "ALSO [CL1]" in user
    assert "ALSO [CL2]" in user


def test_supporting_claims_dropped_when_no_claims_dict():
    """Without claims_by_id, supporting_claims block is omitted (no id-only output)."""
    slot = _slot(supporting_claims=("CL1",), primary_claim_id="CL1")
    user = build_figure_user_prompt(slot, claims_by_id=None)
    assert "[Supporting claims]" not in user
    assert "CL1" not in user


def test_unknown_claim_id_silently_skipped():
    """A supporting_claims id not present in claims_by_id is dropped (no crash)."""
    slot = _slot(supporting_claims=("CL_GHOST", "CL_REAL"), primary_claim_id="CL_REAL")
    claims_by_id = {"CL_REAL": {"id": "CL_REAL", "type": "result", "text": "It works."}}
    user = build_figure_user_prompt(slot, claims_by_id=claims_by_id)
    assert "CL_GHOST" not in user
    assert "PRIMARY [CL_REAL]" in user


def test_section_intent_block_matches_by_section_hint():
    """[Section intent] line appears only when paper_plan.section_plan has a matching name."""
    slot = _slot(section_hint="03_method")
    paper_plan = {
        "section_plan": [
            {"name": "01_intro", "intent": "Motivate the problem."},
            {"name": "03_method", "intent": "Present the proposed mechanism."},
        ]
    }
    user = build_figure_user_prompt(slot, paper_plan=paper_plan)
    assert "[Section intent] 03_method: Present the proposed mechanism." in user


def test_section_intent_omitted_when_section_hint_unmatched():
    slot = _slot(section_hint="99_appendix")
    paper_plan = {"section_plan": [{"name": "01_intro", "intent": "Motivate."}]}
    user = build_figure_user_prompt(slot, paper_plan=paper_plan)
    assert "[Section intent]" not in user


def test_terminology_block_lists_canonical_phrases():
    paper_plan = {
        "terminology": {
            "encoder-decoder": "Two-stage neural net with bottleneck",
            "policy network": "Module that maps observations to actions",
        }
    }
    user = build_figure_user_prompt(_slot(), paper_plan=paper_plan)
    assert "[Paper terminology" in user
    assert "encoder-decoder" in user
    assert "policy network" in user


def test_terminology_capped_at_twelve_entries():
    """Long terminologies are truncated to keep prompt size sane."""
    paper_plan = {"terminology": {f"term_{i}": f"def_{i}" for i in range(30)}}
    user = build_figure_user_prompt(_slot(), paper_plan=paper_plan)
    # First 12 must appear
    assert "term_0" in user
    assert "term_11" in user
    # term_12 onwards must not
    assert "term_12" not in user
    assert "term_29" not in user


def test_extra_instruction_appended_when_provided():
    user = build_figure_user_prompt(_slot(), extra_instruction="emphasize the robot's grip")
    assert "extra_instruction: emphasize the robot's grip" in user


def test_full_brief_combines_all_blocks_in_order():
    """Sanity check: with everything, blocks appear in the documented order
    (scene → claims → section intent → terminology → extra_instruction)."""
    slot = _slot(
        section_hint="03_method",
        supporting_claims=("CL1",),
        primary_claim_id="CL1",
    )
    paper_plan = {
        "section_plan": [{"name": "03_method", "intent": "Present the mechanism."}],
        "terminology": {"encoder-decoder": "Two-stage net"},
    }
    claims_by_id = {"CL1": {"id": "CL1", "type": "novelty", "text": "First to combine X+Y."}}
    user = build_figure_user_prompt(
        slot,
        paper_plan=paper_plan,
        claims_by_id=claims_by_id,
        extra_instruction="prefer cool palette",
    )
    scene_idx = user.index("scene_description:")
    claims_idx = user.index("[Supporting claims]")
    section_idx = user.index("[Section intent]")
    term_idx = user.index("[Paper terminology")
    extra_idx = user.index("extra_instruction:")
    assert scene_idx < claims_idx < section_idx < term_idx < extra_idx

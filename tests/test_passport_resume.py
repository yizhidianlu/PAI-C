"""Tests for resume_from_passport=<hash> resolution + double-resume prevention."""

from __future__ import annotations

import pytest

from paic.passport.ledger import append_boundary, load_ledger
from paic.passport.resume import (
    DoubleResumeError,
    HashMismatchError,
    PassportResumeError,
    resolve_resume,
)


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "passport.yaml"


def test_resume_simple_no_pending_decision(ledger_path):
    boundary = append_boundary(ledger_path, stage=2, next_stage=3)
    plan = resolve_resume(ledger_path, hash=boundary.hash)
    assert plan.boundary.hash == boundary.hash
    assert plan.next_stage == 3
    assert plan.next_mode is None
    assert plan.used_pending_decision is False
    # Resume entry appended
    entries = load_ledger(ledger_path)
    assert len(entries) == 2
    assert entries[1].kind == "resume"
    assert entries[1].consumes_hash == boundary.hash


def test_resume_with_pending_decision_routes_via_chosen_branch(ledger_path):
    boundary = append_boundary(
        ledger_path,
        stage=3,
        next_stage=99,  # advisory only when pending_decision set
        pending_decision={
            "question": "Accept verdict?",
            "options": [
                {"value": "accept", "next_stage": 5, "next_mode": "fast"},
                {"value": "revise", "next_stage": 4},
            ],
        },
    )
    plan = resolve_resume(ledger_path, hash=boundary.hash, chosen_branch="accept")
    assert plan.used_pending_decision is True
    assert plan.next_stage == 5
    assert plan.next_mode == "fast"


def test_resume_pending_decision_requires_chosen_branch(ledger_path):
    boundary = append_boundary(
        ledger_path,
        stage=3,
        pending_decision={
            "question": "Accept?",
            "options": [{"value": "yes", "next_stage": 4}],
        },
    )
    with pytest.raises(PassportResumeError, match="pending_decision"):
        resolve_resume(ledger_path, hash=boundary.hash)


def test_resume_unknown_branch_raises(ledger_path):
    boundary = append_boundary(
        ledger_path,
        stage=3,
        pending_decision={
            "question": "Accept?",
            "options": [{"value": "yes", "next_stage": 4}],
        },
    )
    with pytest.raises(PassportResumeError, match="not in"):
        resolve_resume(ledger_path, hash=boundary.hash, chosen_branch="maybe")


def test_resume_hash_mismatch_raises(ledger_path):
    append_boundary(ledger_path, stage=2)
    with pytest.raises(HashMismatchError):
        resolve_resume(ledger_path, hash="deadbeefdead")


def test_double_resume_refused(ledger_path):
    boundary = append_boundary(ledger_path, stage=2, next_stage=3)
    resolve_resume(ledger_path, hash=boundary.hash)
    with pytest.raises(DoubleResumeError):
        resolve_resume(ledger_path, hash=boundary.hash)


def test_cli_overrides_apply_after_branch_routing(ledger_path):
    boundary = append_boundary(
        ledger_path,
        stage=3,
        pending_decision={
            "question": "?",
            "options": [{"value": "v1", "next_stage": 5, "next_mode": "fast"}],
        },
    )
    plan = resolve_resume(
        ledger_path,
        hash=boundary.hash,
        chosen_branch="v1",
        stage_override=10,
        mode_override="careful",
    )
    # CLI overrides win after branch is resolved
    assert plan.next_stage == 10
    assert plan.next_mode == "careful"
    # The resume entry records the user_override
    assert plan.resume_entry.user_override == {"stage": 10, "mode": "careful"}

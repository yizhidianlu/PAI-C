"""Tests for the append-only ledger + JCS-canonical hash (ARS-fusion P1-2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.passport.ledger import (
    LedgerCorruption,
    append_boundary,
    append_resume,
    compute_entry_hash,
    list_entries,
    load_ledger,
)
from paic.passport.schema import HASH_PLACEHOLDER, PassportEntry


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "passport.yaml"


def test_load_ledger_returns_empty_for_missing_file(ledger_path):
    assert load_ledger(ledger_path) == []


def test_append_boundary_writes_finalized_hash(ledger_path):
    entry = append_boundary(ledger_path, stage=2, deliverables=["x.md"])
    assert entry.hash != HASH_PLACEHOLDER
    assert len(entry.hash) == 12
    assert all(c in "0123456789abcdef" for c in entry.hash)
    # Round-trip through disk
    reloaded = load_ledger(ledger_path)
    assert len(reloaded) == 1
    assert reloaded[0].hash == entry.hash


def test_append_boundary_chains_hashes(ledger_path):
    e1 = append_boundary(ledger_path, stage=1)
    e2 = append_boundary(ledger_path, stage=2)
    assert e1.hash != e2.hash
    # Hash 2 depends on entry 1 in the chain (different chain → different hash)
    other = tmp_path_other = ledger_path.parent / "other.yaml"
    e2_alone = append_boundary(other, stage=2)
    assert e2.hash != e2_alone.hash


def test_compute_entry_hash_deterministic(ledger_path):
    entry = PassportEntry(
        id="01HX0",
        kind="boundary",
        stage=2,
        generated_at=datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC),
        deliverables=["a", "b"],
    )
    h1 = compute_entry_hash([], entry)
    h2 = compute_entry_hash([], entry)
    assert h1 == h2
    assert len(h1) == 12


def test_append_resume_records_consumes_hash(ledger_path):
    boundary = append_boundary(ledger_path, stage=2)
    resume = append_resume(
        ledger_path,
        consumes_hash=boundary.hash,
        stage=3,
        chosen_branch="revise",
    )
    assert resume.kind == "resume"
    assert resume.consumes_hash == boundary.hash
    assert resume.chosen_branch == "revise"
    assert resume.hash != boundary.hash


def test_list_entries_round_trip(ledger_path):
    append_boundary(ledger_path, stage=1, deliverables=["d1"])
    append_boundary(ledger_path, stage=2, deliverables=["d2"])
    entries = list_entries(ledger_path)
    assert len(entries) == 2
    assert entries[0]["stage"] == 1
    assert entries[1]["stage"] == 2


def test_load_ledger_raises_on_garbage(ledger_path):
    ledger_path.write_text("---\nthis: [is, malformed: yaml", encoding="utf-8")
    with pytest.raises(LedgerCorruption):
        load_ledger(ledger_path)


def test_pending_decision_persists_round_trip(ledger_path):
    boundary = append_boundary(
        ledger_path,
        stage=3,
        pending_decision={
            "question": "Accept revision verdict?",
            "options": [
                {"value": "accept", "next_stage": 5},
                {"value": "revise", "next_stage": 4},
                {"value": "reject", "next_stage": None},
            ],
        },
    )
    reloaded = load_ledger(ledger_path)
    assert len(reloaded) == 1
    pd = reloaded[0].pending_decision
    assert pd is not None
    assert pd.question == "Accept revision verdict?"
    assert {o.value for o in pd.options} == {"accept", "revise", "reject"}


def test_hash_independent_of_other_entries_disk_format(ledger_path, tmp_path):
    """Append two boundaries; verify the second's hash matches an in-memory recompute.

    This validates that load_ledger → compute_entry_hash produces the same
    digest as the runtime path, i.e. round-trip preserves JCS bytes.
    """
    e1 = append_boundary(ledger_path, stage=1)
    e2 = append_boundary(ledger_path, stage=2)
    reloaded = load_ledger(ledger_path)

    # Recompute e2's hash from the reloaded chain — must match.
    test_e2 = PassportEntry(**{**reloaded[1].model_dump(), "hash": HASH_PLACEHOLDER})
    recomputed = compute_entry_hash([reloaded[0]], test_e2)
    assert recomputed == e2.hash

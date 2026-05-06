"""Experiment graph tests — Phase 6."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paic.mcp_server.tools.experiment import experiment_start
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.idea import IdeaCard


class _StubExperimentLLM:
    model = "stub-experiment"

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        from paic.graphs.experiment_graph import _DesignFields
        from paic.schemas.experiment import (
            AblationAxis,
            Baseline,
            Dataset,
            Metric,
        )

        assert schema is _DesignFields
        return _DesignFields(
            research_questions=["Does X improve Y?"],
            hypotheses=["X increases Y by >2 pp"],
            datasets=[Dataset(name="ImageNet", rationale="domain standard")],
            baselines=[Baseline(name="ResNet-50", why="canonical baseline")],
            proposed_method="Train X on Y with technique Z. Pseudocode steps...",
            metrics=[Metric(name="top-1", direction="max", primary=True)],
            ablations=[
                AblationAxis(factor="learning_rate", levels=["1e-3", "1e-4"], purpose="sensitivity"),
                AblationAxis(factor="batch_size", levels=["128", "256"], purpose="scale"),
            ],
            compute_budget="8x A100, 3 days",
            success_criteria=["primary metric +2.0 over baseline at p<0.05"],
            threats_to_validity=["test set leakage"],
            timeline_weeks=4,
        )


@pytest.fixture
def project_with_idea(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache
    from paic.graphs.checkpointer import reset_checkpointer_cache
    from paic.mcp_server.runs import reset_registry_cache

    reset_config_cache()
    reset_checkpointer_cache()
    reset_registry_cache()

    p = tmp_path / "p"
    workspace_init(p)
    idea = IdeaCard(
        id="idea_test",
        title="Sample Idea",
        one_liner="Do X to get Y",
        motivation="Gap M",
        proposed_approach="Approach A",
        novelty_claim="Novel because Z",
        expected_contribution="Contribution C",
        grounded_in=["2401.12345"],
        feasibility_score=0.7,
        novelty_score=0.6,
        impact_score=0.5,
        composite_score=0.6,
        created_at=datetime.now(UTC),
    )
    from paic.workspace.store import save_yaml

    save_yaml(p / ".paic/ideas/idea_test.yaml", idea.model_dump(mode="json"))
    return p


def test_experiment_start_writes_yaml(project_with_idea):
    out = experiment_start(str(project_with_idea), "idea_test", llm=_StubExperimentLLM())
    assert out["status"] == "done"
    eid = out["experiment_id"]
    assert eid

    yaml_path = project_with_idea / ".paic/experiments" / f"{eid}.yaml"
    assert yaml_path.is_file()


def test_experiment_start_unknown_idea(project_with_idea):
    out = experiment_start(str(project_with_idea), "missing_idea", llm=_StubExperimentLLM())
    assert out["error"] == "idea_not_found"


# --------------------------------------------------------- splits coercion
def test_dataset_splits_string_coercion():
    """LLM returns descriptive strings for splits — schema must coerce to int."""
    from paic.schemas.experiment import Dataset

    d = Dataset.model_validate({
        "name": "BCI-IV-2a",
        "rationale": "domain standard",
        "splits": {
            "train": "Subject-wise training using 7 subjects (288 trials each)",
            "test": "1 held-out subject (72 trials)",
        },
    })
    assert d.splits is not None
    assert isinstance(d.splits["train"], int)
    assert d.splits["train"] == 7
    assert isinstance(d.splits["test"], int)
    assert d.splits["test"] == 1


def test_dataset_splits_no_digits_becomes_none():
    from paic.schemas.experiment import Dataset

    d = Dataset.model_validate({
        "name": "X",
        "rationale": "y",
        "splits": {"train": "all available data", "test": "held-out set"},
    })
    assert d.splits is None


def test_dataset_splits_int_passthrough():
    from paic.schemas.experiment import Dataset

    d = Dataset.model_validate({
        "name": "ImageNet",
        "rationale": "standard",
        "splits": {"train": 1281167, "val": 50000},
    })
    assert d.splits == {"train": 1281167, "val": 50000}

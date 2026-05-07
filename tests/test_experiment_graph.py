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
    # Seed a library entry that matches the _FullStubLLM baseline's paper_ref
    # so the phase-5 baseline_in_library check has something to resolve
    # against. Real projects always have at least one ingested paper before
    # /paic-experiment is invoked.
    save_yaml(
        p / ".paic/library/selected.yaml",
        {
            "papers": [
                {
                    "doi": "2010.csp",
                    "title": "Common Spatial Pattern (test stub)",
                    "authors": ["Test"],
                    "year": 2010,
                    "venue": "test",
                    "source": "manual",
                }
            ]
        },
    )
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


# --------------------------------------------------------- §quality phase 5


class _FullStubLLM:
    """LLM stub that returns a phase-5-complete plan."""

    model = "stub-full"

    def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
        from paic.graphs.experiment_graph import _DesignFields
        from paic.schemas.experiment import (
            AblationAxis,
            Baseline,
            Dataset,
            Metric,
        )

        return _DesignFields(
            research_questions=["q?"],
            hypotheses=["h"],
            datasets=[Dataset(
                name="BCI-IV-2a", rationale="standard",
                splits={"train": 7, "test": 1},
                license_note="CC-BY-NC",
            )],
            baselines=[Baseline(name="CSP", why="canonical", paper_ref="2010.csp")],
            proposed_method="Method.",
            metrics=[Metric(name="acc", direction="max", primary=True,
                            success_threshold=2.0, success_threshold_unit="absolute")],
            ablations=[AblationAxis(
                factor="lr", levels=["1e-3", "1e-4"], purpose="sensitivity",
            )],
            compute_budget="1xA100",
            success_criteria=["+2.0% at p<0.05"],
            threats_to_validity=["session leakage"],
            timeline_weeks=4,
            statistical_plan=["5 seeds", "paired t-test", "Bonferroni-corrected"],
            reproducibility=["seed=42", "config logged via wandb", "torch==2.1.0"],
        )


def test_phase5_clean_plan_has_no_warnings(project_with_idea):
    out = experiment_start(str(project_with_idea), "idea_test", llm=_FullStubLLM())
    assert out["status"] == "done"
    yaml_path = project_with_idea / ".paic/experiments" / f"{out['experiment_id']}.yaml"
    from paic.workspace.store import load_yaml
    plan = load_yaml(yaml_path)
    assert plan["validation_warnings"] == []
    assert plan["statistical_plan"] == ["5 seeds", "paired t-test", "Bonferroni-corrected"]
    assert plan["reproducibility"] == ["seed=42", "config logged via wandb", "torch==2.1.0"]
    assert plan["metrics"][0]["success_threshold"] == 2.0


def test_baseline_in_library_warning_when_paper_ref_missing(project_with_idea, tmp_path):
    """When a baseline.paper_ref isn't in selected.yaml, the verifier surfaces
    a baseline_in_library warning so the user can /paic-ingest before compose
    rejects the cite key downstream."""
    # Replace the seeded library with one that does NOT contain the stub's
    # paper_ref ("2010.csp") so the new check fires.
    from paic.workspace.store import save_yaml as _save
    _save(
        project_with_idea / ".paic/library/selected.yaml",
        {"papers": [{"doi": "10.9999/unrelated", "title": "Other", "authors": ["X"], "year": 2020}]},
    )
    out = experiment_start(str(project_with_idea), "idea_test", llm=_FullStubLLM())
    yaml_path = project_with_idea / ".paic/experiments" / f"{out['experiment_id']}.yaml"
    from paic.workspace.store import load_yaml
    plan = load_yaml(yaml_path)
    kinds = {w.split(":")[0] for w in plan["validation_warnings"]}
    assert "baseline_in_library" in kinds


def test_phase5_legacy_plan_emits_warnings(project_with_idea):
    """The pre-phase-5 stub has no statistical_plan / reproducibility / license /
    paper_ref. Verifier must surface warnings for each missing slice."""
    out = experiment_start(str(project_with_idea), "idea_test", llm=_StubExperimentLLM())
    yaml_path = project_with_idea / ".paic/experiments" / f"{out['experiment_id']}.yaml"
    from paic.workspace.store import load_yaml
    plan = load_yaml(yaml_path)
    warnings = plan["validation_warnings"]
    kinds = {w.split(":")[0] for w in warnings}
    assert "baseline_retrieve" in kinds  # ResNet-50 has no paper_ref
    assert "dataset_check" in kinds      # ImageNet has no license_note + no splits
    assert "statistical_plan" in kinds
    assert "repro_checklist" in kinds


def test_phase5_metric_warns_on_no_primary():
    from paic.graphs.experiment_graph import _verify_plan
    plan = {
        "baselines": [{"name": "x", "paper_ref": "y"}],
        "datasets": [{"name": "x", "license_note": "MIT", "splits": {"train": 1}}],
        "metrics": [{"name": "acc", "direction": "max", "primary": False}],
        "ablations": [{"factor": "x", "levels": ["a", "b"], "purpose": "y"}],
        "compute_budget": "x",
        "statistical_plan": ["x"],
        "reproducibility": ["x"],
    }
    out = _verify_plan({"plan": plan}, deps=None)
    warnings = out["plan"]["validation_warnings"]
    assert any("metric_select" in w and "primary" in w for w in warnings)


def test_phase5_metric_warns_on_multiple_primary():
    from paic.graphs.experiment_graph import _verify_plan
    plan = {
        "baselines": [{"name": "x", "paper_ref": "y"}],
        "datasets": [{"name": "x", "license_note": "MIT", "splits": {"train": 1}}],
        "metrics": [
            {"name": "a", "direction": "max", "primary": True},
            {"name": "b", "direction": "max", "primary": True},
        ],
        "ablations": [{"factor": "x", "levels": ["a", "b"], "purpose": "y"}],
        "compute_budget": "x",
        "statistical_plan": ["x"],
        "reproducibility": ["x"],
    }
    out = _verify_plan({"plan": plan}, deps=None)
    warnings = out["plan"]["validation_warnings"]
    assert any("metric_select" in w and "2 metrics" in w for w in warnings)


def test_phase5_ablation_warns_on_too_few_levels():
    from paic.graphs.experiment_graph import _verify_plan
    plan = {
        "baselines": [{"name": "x", "paper_ref": "y"}],
        "datasets": [{"name": "x", "license_note": "MIT", "splits": {"train": 1}}],
        "metrics": [{"name": "a", "direction": "max", "primary": True}],
        "ablations": [{"factor": "x", "levels": ["a"], "purpose": "y"}],  # 1 level
        "compute_budget": "x",
        "statistical_plan": ["x"],
        "reproducibility": ["x"],
    }
    out = _verify_plan({"plan": plan}, deps=None)
    warnings = out["plan"]["validation_warnings"]
    assert any("ablation_design" in w and "<2 levels" in w for w in warnings)


def test_phase5_metric_success_threshold_round_trip():
    """Metric.success_threshold persists through the schema."""
    from paic.schemas.experiment import Metric
    m = Metric(name="acc", direction="max", primary=True,
               success_threshold=3.5, success_threshold_unit="percentage points")
    dumped = m.model_dump(mode="json")
    reloaded = Metric.model_validate(dumped)
    assert reloaded.success_threshold == 3.5
    assert reloaded.success_threshold_unit == "percentage points"


def test_phase5_experiment_plan_default_lists_empty():
    """Phase-5 fields default to [] so legacy yaml files without them load fine."""
    from paic.schemas.experiment import ExperimentPlan
    minimal = {
        "id": "e1", "idea_id": "i1",
        "research_questions": [], "hypotheses": [],
        "datasets": [], "baselines": [],
        "proposed_method": "x",
        "metrics": [], "ablations": [],
        "success_criteria": [], "threats_to_validity": [],
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    plan = ExperimentPlan.model_validate(minimal)
    assert plan.statistical_plan == []
    assert plan.reproducibility == []
    assert plan.validation_warnings == []

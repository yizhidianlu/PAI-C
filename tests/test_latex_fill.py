"""LaTeX template filler tests — Phase 7."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paic.latex.filler import VENUES, fill_draft
from paic.mcp_server.tools.draft import draft_fill_tool
from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.experiment import ExperimentPlan, Metric
from paic.schemas.idea import IdeaCard
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import reset_config_cache

    reset_config_cache()

    p = tmp_path / "p"
    workspace_init(p, title="A Sample Paper")

    library_add_tool(
        str(p),
        [
            {"arxiv_id": "2401.12345", "title": "Foundational Paper", "authors": ["A. Smith"], "year": 2024},
            {"doi": "10.1234/abc", "title": "Other Reference", "authors": ["B. Jones"], "year": 2023},
        ],
    )

    idea = IdeaCard(
        id="idea_xyz",
        title="Latent Diffusion for Tabular Data",
        one_liner="Apply latent diffusion to small tabular datasets.",
        motivation="Tabular generation is dominated by GANs and VAEs; diffusion has not been tried at small scale.",
        proposed_approach="Two-stage: encode rows with a tiny VAE, then run a diffusion process in latent space.",
        novelty_claim="First application of latent diffusion to tabular data <100k rows.",
        expected_contribution="A simple, reproducible diffusion baseline for tabular benchmarks.",
        grounded_in=["2401.12345"],
        risk_factors=["latent space may collapse on small datasets"],
        feasibility_score=0.7,
        novelty_score=0.5,
        impact_score=0.4,
        composite_score=0.55,
        created_at=datetime.now(UTC),
    )
    save_yaml(p / ".paic/ideas/idea_xyz.yaml", idea.model_dump(mode="json"))

    plan = ExperimentPlan(
        id="exp_xyz",
        idea_id="idea_xyz",
        research_questions=["Does latent diffusion outperform CTGAN on Adult and Census?"],
        hypotheses=["Latent diffusion improves likelihood by >5% over CTGAN."],
        proposed_method="Encode rows via a 64-dim VAE, train DDPM on latent codes, sample via DDIM.",
        metrics=[Metric(name="negative log-likelihood", direction="min", primary=True)],
        ablations=[],
        compute_budget="single A6000, 24h",
        success_criteria=["primary metric -5% relative vs CTGAN at p<0.05"],
        threats_to_validity=["small dataset noise"],
        timeline_weeks=3,
        created_at=datetime.now(UTC),
    )
    save_yaml(p / ".paic/experiments/exp_xyz.yaml", plan.model_dump(mode="json"))
    return p


@pytest.mark.parametrize("template", VENUES)
def test_fill_draft_per_venue(project, template):
    paths = resolve_project(project)
    out = fill_draft(paths, template=template, idea_id="idea_xyz", experiment_id="exp_xyz")

    main_tex = Path(out["main_tex"])
    assert main_tex.is_file()
    assert main_tex.name == "main.tex"
    text = main_tex.read_text(encoding="utf-8")
    assert "A Sample Paper" in text  # title from project.yaml
    assert "\\input{sections/00_abstract}" in text

    # All 6 sections rendered
    assert len(out["sections_created"]) == 6
    for path in out["sections_created"]:
        assert Path(path).is_file()

    # refs.bib generated from library
    refs = Path(out["refs_bib"]).read_text(encoding="utf-8")
    assert "@misc{arxiv_2401_12345" in refs
    assert "Foundational Paper" in refs
    assert "Other Reference" in refs

    # The intro should have included the idea's expected contribution
    intro_text = Path(project / ".paic/drafts/sections/01_intro.tex").read_text(encoding="utf-8")
    assert "Latent Diffusion for Tabular Data" not in intro_text  # title is in main.tex, not intro
    assert "diffusion baseline" in intro_text  # from expected_contribution


def test_draft_fill_tool_unknown_template(project):
    out = draft_fill_tool(str(project), "nips", "idea_xyz", "exp_xyz")
    assert out["error"] == "unknown_template"
    available_names = [t["name"] for t in out["available"]]
    assert "neurips" in available_names
    assert "hint" in out


def test_draft_fill_without_experiment(project):
    out = draft_fill_tool(str(project), "neurips", "idea_xyz", experiment_id=None)
    assert "main_tex" in out
    method_text = Path(project / ".paic/drafts/sections/03_method.tex").read_text(encoding="utf-8")
    assert "TODO" in method_text  # experiment fields fall back to TODO


def test_draft_fill_idea_not_found(project):
    out = draft_fill_tool(str(project), "ieee", "idea_missing", "exp_xyz")
    assert out["error"] == "not_found"


def test_bib_keys_are_stable_across_runs(project):
    paths = resolve_project(project)
    a = fill_draft(paths, template="ieee", idea_id="idea_xyz", experiment_id="exp_xyz")
    b = fill_draft(paths, template="ieee", idea_id="idea_xyz", experiment_id="exp_xyz")
    assert a["bib_keys"] == b["bib_keys"]


# §19 — user-template support


def test_fill_with_user_template_in_project(project):
    """User-supplied template at <project>/.paic/templates/<name>/ should be usable."""
    paths = resolve_project(project)
    user_root = paths.templates_dir / "iclr2026"
    user_root.mkdir(parents=True)
    (user_root / "main.tex.j2").write_text(
        "% Custom ICLR template\n"
        "\\documentclass{article}\n"
        "\\title{ {{ project.title }} }\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        "\\input{sections/00_abstract}\n"
        "\\input{sections/01_intro}\n"
        "\\input{sections/02_related}\n"
        "\\input{sections/03_method}\n"
        "\\input{sections/04_experiments}\n"
        "\\input{sections/05_conclusion}\n"
        "\\bibliography{refs}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )

    out = fill_draft(paths, template="iclr2026", idea_id="idea_xyz", experiment_id="exp_xyz")
    assert out["template"] == "iclr2026"
    assert out["template_kind"] == "user"
    main_text = Path(out["main_tex"]).read_text(encoding="utf-8")
    assert "Custom ICLR template" in main_text
    assert "A Sample Paper" in main_text  # project.title rendered
    # All 6 sections fall back to _shared
    assert len(out["sections_created"]) == 6


def test_user_template_section_override(project):
    """A user-supplied sections/03_method.tex.j2 should override _shared."""
    paths = resolve_project(project)
    user_root = paths.templates_dir / "iclr2026"
    user_root.mkdir(parents=True)
    (user_root / "main.tex.j2").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\input{sections/03_method}\\end{document}\n",
        encoding="utf-8",
    )
    sections = user_root / "sections"
    sections.mkdir()
    (sections / "03_method.tex.j2").write_text(
        "% USER-OVERRIDDEN METHOD SECTION\n"
        "\\section{Method}\nCustom method body for {{ idea.title }}.\n",
        encoding="utf-8",
    )

    fill_draft(paths, template="iclr2026", idea_id="idea_xyz", experiment_id="exp_xyz")
    method_text = Path(project / ".paic/drafts/sections/03_method.tex").read_text(encoding="utf-8")
    assert "USER-OVERRIDDEN METHOD SECTION" in method_text
    assert "Latent Diffusion for Tabular Data" in method_text  # idea.title rendered


def test_user_template_overrides_builtin(project):
    """A user template named 'neurips' should win over the built-in."""
    paths = resolve_project(project)
    user_root = paths.templates_dir / "neurips"
    user_root.mkdir(parents=True)
    (user_root / "main.tex.j2").write_text(
        "% USER-NEURIPS\n"
        "\\documentclass{article}\\begin{document}"
        "\\input{sections/00_abstract}\\end{document}\n",
        encoding="utf-8",
    )
    out = fill_draft(paths, template="neurips", idea_id="idea_xyz", experiment_id="exp_xyz")
    assert out["template_kind"] == "user"
    main_text = Path(out["main_tex"]).read_text(encoding="utf-8")
    assert "USER-NEURIPS" in main_text
    assert "neurips_2024" not in main_text  # built-in's marker absent


def test_fill_copies_static_assets(project):
    """Non-jinja files in the template root should be copied to drafts/."""
    paths = resolve_project(project)
    user_root = paths.templates_dir / "iclr2026"
    user_root.mkdir(parents=True)
    (user_root / "main.tex.j2").write_text(
        "\\documentclass{article}\\begin{document}"
        "\\input{sections/00_abstract}\\end{document}\n",
        encoding="utf-8",
    )
    (user_root / "iclr2026.sty").write_text(
        "\\ProvidesPackage{iclr2026}\n", encoding="utf-8"
    )
    (user_root / "iclr2026.bst").write_text("% custom bibstyle\n", encoding="utf-8")

    out = fill_draft(paths, template="iclr2026", idea_id="idea_xyz", experiment_id="exp_xyz")
    assert "iclr2026.sty" in out["static_assets_copied"]
    assert "iclr2026.bst" in out["static_assets_copied"]
    assert (project / ".paic/drafts/iclr2026.sty").is_file()
    assert (project / ".paic/drafts/iclr2026.bst").is_file()


def test_fill_skips_template_main_tex(project):
    """A leftover main.tex (non-jinja) in the template root must be skipped."""
    paths = resolve_project(project)
    user_root = paths.templates_dir / "iclr2026"
    user_root.mkdir(parents=True)
    (user_root / "main.tex.j2").write_text(
        "% PAI-C\n\\documentclass{article}\\begin{document}A\\end{document}\n",
        encoding="utf-8",
    )
    # Pretend the user dropped the venue's example main.tex alongside.
    (user_root / "main.tex").write_text("% LEFTOVER FROM VENUE BUNDLE", encoding="utf-8")

    out = fill_draft(paths, template="iclr2026", idea_id="idea_xyz", experiment_id="exp_xyz")
    main_text = Path(out["main_tex"]).read_text(encoding="utf-8")
    # PAI-C's rendered main.tex (not the leftover)
    assert "PAI-C" in main_text
    assert "LEFTOVER" not in main_text
    # And static_assets_skipped flags the skip
    assert any("main.tex" in s for s in out["static_assets_skipped"])

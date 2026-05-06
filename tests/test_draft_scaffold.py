"""draft_scaffold + draft_list_templates tool tests — §19."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from paic.mcp_server.tools.draft import (
    draft_fill_tool,
    draft_list_templates_tool,
    draft_scaffold_tool,
)
from paic.mcp_server.tools.workspace import workspace_init


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "p"
    workspace_init(p, title="Test")
    return p


# ---------------------------------------------------------------- list tool
def test_list_templates_returns_three_builtins(project):
    out = draft_list_templates_tool(str(project))
    names = [t["name"] for t in out["templates"]]
    assert sorted(names) == ["cvpr", "ieee", "neurips"]
    for t in out["templates"]:
        assert t["kind"] == "builtin"
        assert t["overrides_builtin"] is False


def test_list_templates_includes_user_template(project):
    """A user-supplied template should show up in the listing."""
    user_dir = Path(project) / ".paic" / "templates" / "iclr2026"
    user_dir.mkdir(parents=True)
    (user_dir / "main.tex.j2").write_text("% main", encoding="utf-8")
    (user_dir / "template.yaml").write_text(
        yaml.safe_dump({"display_name": "ICLR 2026"}), encoding="utf-8"
    )
    out = draft_list_templates_tool(str(project))
    iclr = next(t for t in out["templates"] if t["name"] == "iclr2026")
    assert iclr["kind"] == "user"
    assert iclr["display_name"] == "ICLR 2026"


def test_list_templates_uninitialized_project_errors(tmp_path):
    out = draft_list_templates_tool(str(tmp_path / "nope"))
    assert out["error"] == "project_not_initialized"


# ---------------------------------------------------------------- scaffold
def test_scaffold_creates_template_from_neurips(project):
    out = draft_scaffold_tool(str(project), "iclr2026", base="neurips")
    assert out["name"] == "iclr2026"
    assert out["base"] == "neurips"
    main = Path(project) / ".paic" / "templates" / "iclr2026" / "main.tex.j2"
    meta = Path(project) / ".paic" / "templates" / "iclr2026" / "template.yaml"
    assert main.is_file()
    assert meta.is_file()
    # Content matches built-in NeurIPS template
    assert "neurips_2024" in main.read_text(encoding="utf-8")
    parsed = yaml.safe_load(meta.read_text(encoding="utf-8"))
    assert parsed["display_name"] == "iclr2026"


def test_scaffold_appears_in_list_after_creation(project):
    draft_scaffold_tool(str(project), "myvenue")
    out = draft_list_templates_tool(str(project))
    names = [t["name"] for t in out["templates"]]
    assert "myvenue" in names


def test_scaffold_template_already_exists(project):
    draft_scaffold_tool(str(project), "dup")
    out = draft_scaffold_tool(str(project), "dup")
    assert out["error"] == "template_already_exists"
    assert out["name"] == "dup"


def test_scaffold_unknown_base_returns_error(project):
    out = draft_scaffold_tool(str(project), "x", base="not-a-real-base")
    assert out["error"] == "unknown_base"
    assert "neurips" in out["available"]
    assert "cvpr" in out["available"]


def test_scaffold_invalid_name_path_separator(project):
    out = draft_scaffold_tool(str(project), "bad/name")
    assert out["error"] == "invalid_name"


def test_scaffold_invalid_name_dot_prefix(project):
    out = draft_scaffold_tool(str(project), ".hidden")
    assert out["error"] == "invalid_name"


def test_scaffold_empty_name_errors(project):
    out = draft_scaffold_tool(str(project), "   ")
    assert out["error"] == "invalid_name"


# ---------------------------------------------------------------- fill via user template (E2E)
def test_fill_with_scaffolded_template(project, tmp_path):
    """Scaffold → seed minimal idea → fill should work end-to-end."""
    from datetime import UTC, datetime

    from paic.schemas.idea import IdeaCard
    from paic.workspace.store import save_yaml

    # Scaffold a custom template
    draft_scaffold_tool(str(project), "iclr2026", base="neurips")

    # Seed minimal idea
    idea = IdeaCard(
        id="idea_x",
        title="Sample Idea",
        one_liner="A short summary.",
        motivation="Motivated by existing gaps.",
        proposed_approach="Use a transformer.",
        novelty_claim="First to do X.",
        expected_contribution="Improvements on benchmark.",
        feasibility_score=0.7,
        novelty_score=0.5,
        impact_score=0.4,
        composite_score=0.55,
        created_at=datetime.now(UTC),
    )
    save_yaml(Path(project) / ".paic/ideas/idea_x.yaml", idea.model_dump(mode="json"))

    out = draft_fill_tool(str(project), "iclr2026", "idea_x")
    assert "main_tex" in out
    assert out["template"] == "iclr2026"
    assert out["template_kind"] == "user"


def test_fill_unknown_template_lists_available(project):
    out = draft_fill_tool(str(project), "totally-fake-venue", "idea_x")
    assert out["error"] == "unknown_template"
    available_names = [t["name"] for t in out["available"]]
    assert "neurips" in available_names
    assert "hint" in out
    # The hint should reference scaffold so the user knows the next step
    assert "scaffold" in out["hint"].lower()

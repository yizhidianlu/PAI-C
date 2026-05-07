"""LaTeX template registry tests — §19."""

from __future__ import annotations

import pytest

from paic.latex.registry import (
    TemplateNotFound,
    discover_templates,
    resolve_template,
    shared_sections_root,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "p"
    workspace_init(p, title="Test")
    return resolve_project(p)


def _make_user_template(
    paths,
    name: str,
    *,
    main_tex: str = "% main\n\\documentclass{article}\n\\begin{document}{{ idea.title }}\\end{document}\n",
    metadata: dict | None = None,
    extra_files: dict[str, str] | None = None,
):
    """Helper: write a project-local template with a main.tex.j2 + optional extras."""
    root = paths.templates_dir / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex.j2").write_text(main_tex, encoding="utf-8")
    if metadata is not None:
        import yaml
        (root / "template.yaml").write_text(
            yaml.safe_dump(metadata, allow_unicode=True), encoding="utf-8"
        )
    for rel, body in (extra_files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


# ---------------------------------------------------------------- discovery
def test_discover_templates_builtin_only(project):
    """Without user templates, only the 3 built-ins show up."""
    records = discover_templates(project)
    names = [r.name for r in records]
    assert sorted(names) == ["cvpr", "ieee", "neurips"]
    for r in records:
        assert r.kind == "builtin"
        assert r.overrides_builtin is False


def test_discover_templates_includes_user(project):
    _make_user_template(project, "iclr2026", metadata={"display_name": "ICLR 2026"})
    records = discover_templates(project)
    names = [r.name for r in records]
    assert "iclr2026" in names
    iclr = next(r for r in records if r.name == "iclr2026")
    assert iclr.kind == "user"
    assert iclr.display_name == "ICLR 2026"


def test_discover_templates_user_listed_first(project):
    """User templates should be listed before built-ins so newly-uploaded
    templates aren't buried under the alphabetized cvpr/neurips/ieee."""
    _make_user_template(project, "iclr2026")
    records = discover_templates(project)
    assert records[0].kind == "user"


def test_discover_user_overrides_builtin(project):
    """A user template named the same as a built-in wins and is flagged."""
    _make_user_template(project, "neurips", metadata={"display_name": "Custom NeurIPS"})
    records = discover_templates(project)
    neurips_records = [r for r in records if r.name == "neurips"]
    assert len(neurips_records) == 1  # only one — user replaced built-in
    rec = neurips_records[0]
    assert rec.kind == "user"
    assert rec.overrides_builtin is True
    assert rec.display_name == "Custom NeurIPS"


def test_discover_skips_directories_without_main_tex_j2(project):
    """A directory with only static files (no main.tex.j2) is not a template."""
    bogus = project.templates_dir / "bogus"
    bogus.mkdir(parents=True)
    (bogus / "readme.md").write_text("not a template", encoding="utf-8")
    records = discover_templates(project)
    assert "bogus" not in [r.name for r in records]


def test_discover_skips_dot_directories(project):
    """Hidden directories like .git or .DS_Store are ignored."""
    hidden = project.templates_dir / ".git"
    hidden.mkdir(parents=True)
    (hidden / "main.tex.j2").write_text("% bogus", encoding="utf-8")
    records = discover_templates(project)
    assert ".git" not in [r.name for r in records]


def test_template_yaml_metadata_loaded(project):
    _make_user_template(
        project,
        "myvenue",
        metadata={
            "display_name": "My Venue 2026",
            "target_venue": "ACL 2026",
            "description": "Anonymous track template",
        },
    )
    rec = resolve_template("myvenue", project)
    assert rec.display_name == "My Venue 2026"
    assert rec.target_venue == "ACL 2026"
    assert rec.description == "Anonymous track template"


def test_template_yaml_corrupted_falls_back(project):
    """Bad yaml shouldn't crash discovery; metadata silently degrades to None."""
    root = project.templates_dir / "broken"
    root.mkdir(parents=True)
    (root / "main.tex.j2").write_text("% main", encoding="utf-8")
    (root / "template.yaml").write_text("{not: valid: yaml: at all", encoding="utf-8")
    rec = resolve_template("broken", project)
    assert rec.name == "broken"
    assert rec.display_name is None  # fell back


def test_has_static_assets_flag(project):
    """has_static_assets is True iff the template ships any non-jinja file."""
    _make_user_template(project, "lean")  # only main.tex.j2
    _make_user_template(project, "rich", extra_files={"venue.sty": "\\ProvidesPackage{venue}"})
    rec_lean = resolve_template("lean", project)
    rec_rich = resolve_template("rich", project)
    assert rec_lean.has_static_assets is False
    assert rec_rich.has_static_assets is True


# ---------------------------------------------------------------- resolution
def test_resolve_template_builtin(project):
    rec = resolve_template("neurips", project)
    assert rec.kind == "builtin"
    assert rec.name == "neurips"
    assert (rec.root / "main.tex.j2").is_file()


def test_resolve_template_user(project):
    _make_user_template(project, "iclr2026")
    rec = resolve_template("iclr2026", project)
    assert rec.kind == "user"
    assert rec.root == project.templates_dir / "iclr2026"


def test_resolve_template_not_found_lists_available(project):
    _make_user_template(project, "myvenue")
    with pytest.raises(TemplateNotFound) as exc_info:
        resolve_template("does-not-exist", project)
    assert "myvenue" in exc_info.value.available
    assert "neurips" in exc_info.value.available
    assert "does-not-exist" not in exc_info.value.available


def test_resolve_template_case_insensitive_user(project):
    """User types `AAAI` but their directory is `aaai` — should resolve.

    Common pitfall: user reads "AAAI 2026" in their submission email, types
    `--template AAAI` but their template directory is lowercase `aaai`.
    """
    _make_user_template(project, "aaai")
    rec = resolve_template("AAAI", project)
    assert rec.name == "aaai"  # actual directory case preserved
    assert rec.kind == "user"


def test_resolve_template_case_insensitive_builtin(project):
    """Built-in templates also resolve case-insensitively."""
    rec = resolve_template("NeurIPS", project)
    assert rec.name == "neurips"
    assert rec.kind == "builtin"


def test_resolve_template_exact_match_preferred_over_case_insensitive(project, monkeypatch):
    """If two records differ only in case (rare on case-sensitive FS), exact match wins.

    We can't reliably create both `aaai/` and `AAAI/` on case-insensitive FSes
    (Windows / default macOS), so we fake the discovery output instead.
    """
    from paic.latex.registry import TemplateRecord

    fake_records = [
        TemplateRecord(name="aaai", kind="user", root=project.templates_dir / "aaai"),
        TemplateRecord(name="AAAI", kind="user", root=project.templates_dir / "AAAI"),
    ]
    monkeypatch.setattr("paic.latex.registry.discover_templates", lambda paths: fake_records)
    rec = resolve_template("AAAI", project)
    assert rec.name == "AAAI"  # exact match wins over case-insensitive fallback


def test_shared_sections_root_exists():
    root = shared_sections_root()
    assert (root / "sections" / "00_abstract.tex.j2").is_file()


def test_record_to_dict_serializable(project):
    _make_user_template(project, "x", metadata={"display_name": "X"})
    rec = resolve_template("x", project)
    d = rec.to_dict()
    assert d["name"] == "x"
    assert d["kind"] == "user"
    assert d["display_name"] == "X"
    assert d["overrides_builtin"] is False
    # All values JSON-serializable
    import json
    json.dumps(d)

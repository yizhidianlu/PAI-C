"""Static asset copy tests — §19."""

from __future__ import annotations

from pathlib import Path

from paic.latex.static_assets import copy_static_assets


def _seed_template(root: Path, files: dict[str, str]):
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def test_copy_static_assets_copies_sty_cls_bst(tmp_path):
    template = tmp_path / "tpl"
    drafts = tmp_path / "drafts"
    _seed_template(template, {
        "main.tex.j2": "% jinja",
        "venue.sty": "\\ProvidesPackage{venue}",
        "venue.cls": "\\ProvidesClass{venue}",
        "myrefs.bst": "% bibstyle",
    })
    out = copy_static_assets(template, drafts)
    assert sorted(out["copied"]) == ["myrefs.bst", "venue.cls", "venue.sty"]
    assert (drafts / "venue.sty").read_text(encoding="utf-8") == "\\ProvidesPackage{venue}"


def test_copy_static_assets_skips_jinja(tmp_path):
    template = tmp_path / "tpl"
    drafts = tmp_path / "drafts"
    _seed_template(template, {
        "main.tex.j2": "% should NOT be copied — jinja",
        "intro.tex.j2": "% also jinja",
        "venue.sty": "\\ProvidesPackage{venue}",
    })
    out = copy_static_assets(template, drafts)
    assert out["copied"] == ["venue.sty"]
    assert not (drafts / "main.tex.j2").exists()
    assert not (drafts / "intro.tex.j2").exists()


def test_copy_static_assets_skips_template_yaml(tmp_path):
    template = tmp_path / "tpl"
    drafts = tmp_path / "drafts"
    _seed_template(template, {
        "main.tex.j2": "%",
        "template.yaml": "display_name: Foo",
        "venue.sty": "\\ProvidesPackage{venue}",
    })
    out = copy_static_assets(template, drafts)
    assert "template.yaml" not in out["copied"]
    assert "venue.sty" in out["copied"]


def test_copy_static_assets_skips_managed_filenames(tmp_path):
    """refs.bib and main.tex from a template are skipped (PAI-C writes its own)."""
    template = tmp_path / "tpl"
    drafts = tmp_path / "drafts"
    _seed_template(template, {
        "main.tex.j2": "%",
        "main.tex": "% leftover",
        "refs.bib": "@article{foo, ...}",
        "venue.sty": "%",
    })
    out = copy_static_assets(template, drafts)
    assert "main.tex" not in out["copied"]
    assert "refs.bib" not in out["copied"]
    assert "venue.sty" in out["copied"]
    skipped_str = " ".join(out["skipped"])
    assert "main.tex" in skipped_str
    assert "refs.bib" in skipped_str


def test_copy_static_assets_skips_sections_dir(tmp_path):
    """sections/*.tex from a template would clobber PAI-C-rendered sections."""
    template = tmp_path / "tpl"
    drafts = tmp_path / "drafts"
    _seed_template(template, {
        "main.tex.j2": "%",
        "sections/extra.tex": "% would clobber",
        "venue.sty": "%",
    })
    out = copy_static_assets(template, drafts)
    assert "sections/extra.tex" not in out["copied"]
    assert any("sections/extra.tex" in s for s in out["skipped"])
    assert "venue.sty" in out["copied"]


def test_copy_static_assets_preserves_subdirs(tmp_path):
    """Non-managed subdirectories (e.g. figures/) preserve their layout."""
    template = tmp_path / "tpl"
    drafts = tmp_path / "drafts"
    _seed_template(template, {
        "main.tex.j2": "%",
        "figures/logo.pdf": "%PDF-1.0",
        "figures/diagrams/arch.png": "PNG-fake",
    })
    out = copy_static_assets(template, drafts)
    assert "figures/logo.pdf" in out["copied"]
    assert "figures/diagrams/arch.png" in out["copied"]
    assert (drafts / "figures" / "logo.pdf").is_file()
    assert (drafts / "figures" / "diagrams" / "arch.png").is_file()


def test_copy_static_assets_overwrites_existing(tmp_path):
    """Re-running fill should overwrite stale copies of static assets."""
    template = tmp_path / "tpl"
    drafts = tmp_path / "drafts"
    _seed_template(template, {
        "main.tex.j2": "%",
        "venue.sty": "v2-content",
    })
    drafts.mkdir(parents=True)
    (drafts / "venue.sty").write_text("v1-old", encoding="utf-8")
    copy_static_assets(template, drafts)
    assert (drafts / "venue.sty").read_text(encoding="utf-8") == "v2-content"


def test_copy_static_assets_missing_template_returns_empty(tmp_path):
    """A non-existent template root is a no-op (not an error)."""
    out = copy_static_assets(tmp_path / "does-not-exist", tmp_path / "drafts")
    assert out == {"copied": [], "skipped": []}

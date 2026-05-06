"""paic_library_attach_paper tests — §24."""

from __future__ import annotations

from pathlib import Path

import pytest

from paic.mcp_server.tools.attach import library_attach_paper_tool
from paic.mcp_server.tools.workspace import workspace_init


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fresh project with a fake arxiv storage root configured."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))

    # Seed a fake arxiv MCP storage root with one .md file we can attach.
    arxiv_root = tmp_path / "arxiv-papers"
    arxiv_root.mkdir()
    (arxiv_root / "2401.12345.md").write_text(
        "# Foundational Paper\nbody text", encoding="utf-8"
    )

    # Point PAI-C at this fake storage via config.yaml.
    home = tmp_path / ".paic_home"
    home.mkdir()
    import yaml

    (home / "config.yaml").write_text(
        yaml.safe_dump({"arxiv_mcp_storage_paths": [str(arxiv_root)]}),
        encoding="utf-8",
    )

    from paic.config import reset_config_cache

    reset_config_cache()

    p = tmp_path / "p"
    workspace_init(p, title="Test")
    return p


# ---------------------------------------------------------------- happy paths
def test_attach_arxiv_auto_locates_markdown(project):
    paper = {"arxiv_id": "2401.12345", "title": "Foundational Paper"}
    out = library_attach_paper_tool(str(project), paper)

    assert out.get("error") is None, out
    assert out["copied"] is True
    assert out["cite_key"] == "arxiv_2401_12345"
    assert out["ext"] == ".md"
    dest = Path(out["dest_path"])
    assert dest.is_file()
    assert dest.name == "arxiv_2401_12345.md"
    assert dest.parent.name == "pdfs"
    assert "Foundational Paper" in dest.read_text(encoding="utf-8")


def test_attach_with_explicit_source_path(project, tmp_path):
    """Caller (paper-search-mcp path) supplies a downloaded PDF directly."""
    fake_pdf = tmp_path / "downloaded.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake content")

    paper = {
        "doi": "10.1234/abc",
        "title": "Pubmed Paper",
        "platform": "pubmed",
    }
    out = library_attach_paper_tool(str(project), paper, source_path=str(fake_pdf))

    assert out.get("error") is None, out
    assert out["copied"] is True
    assert out["cite_key"] == "doi_10_1234_abc"
    assert out["ext"] == ".pdf"
    dest = Path(out["dest_path"])
    assert dest.is_file()
    assert dest.name == "doi_10_1234_abc.pdf"
    assert dest.read_bytes() == b"%PDF-1.4 fake content"


def test_attach_skips_when_dest_exists(project):
    """Re-running attach on the same paper is a silent no-op."""
    paper = {"arxiv_id": "2401.12345", "title": "Foundational Paper"}
    library_attach_paper_tool(str(project), paper)

    # Mutate the dest so we can detect a re-copy
    dest = Path(project) / ".paic/library/pdfs/arxiv_2401_12345.md"
    dest.write_text("manually-edited", encoding="utf-8")

    out = library_attach_paper_tool(str(project), paper)
    assert out["copied"] is False
    assert out["reason"] == "already_exists"
    # File contents preserved (we did NOT overwrite)
    assert dest.read_text(encoding="utf-8") == "manually-edited"


def test_attach_uses_cite_key_filename(project, tmp_path):
    """Filename is <cite_key>.<ext>, not the bare paper_id."""
    fake_pdf = tmp_path / "x.pdf"
    fake_pdf.write_bytes(b"x")

    paper = {"arxiv_id": "2401.12345"}
    out = library_attach_paper_tool(str(project), paper, source_path=str(fake_pdf))
    # Even though arxiv_id is "2401.12345", the dest uses the cite_key
    # which is "arxiv_2401_12345" (matches BibTeX key format).
    assert Path(out["dest_path"]).name == "arxiv_2401_12345.pdf"


def test_attach_pdf_extension_from_source_suffix(project, tmp_path):
    """Source file's suffix wins (so .epub / .pdf / .html etc. are preserved)."""
    fake = tmp_path / "paper.epub"
    fake.write_bytes(b"epub")

    paper = {"doi": "10.1/abc"}
    out = library_attach_paper_tool(str(project), paper, source_path=str(fake))
    assert out["ext"] == ".epub"
    assert Path(out["dest_path"]).suffix == ".epub"


def test_attach_creates_pdfs_dir_if_missing(project):
    """First call creates the pdfs/ dir even if PROJECT_LAYOUT didn't seed it."""
    pdfs = Path(project) / ".paic/library/pdfs"
    if pdfs.exists():
        # Remove to simulate an older project
        for f in pdfs.iterdir():
            f.unlink()
        pdfs.rmdir()

    paper = {"arxiv_id": "2401.12345"}
    out = library_attach_paper_tool(str(project), paper)
    assert out.get("error") is None, out
    assert pdfs.is_dir()


# ---------------------------------------------------------------- error paths
def test_attach_arxiv_markdown_not_found_returns_error(project):
    """Asking to attach an arxiv paper that hasn't been downloaded errors out."""
    paper = {"arxiv_id": "9999.99999", "title": "Never Downloaded"}
    out = library_attach_paper_tool(str(project), paper)
    assert out["error"] == "source_not_found"
    assert "9999.99999" in out["detail"]
    assert out["cite_key"] == "arxiv_9999_99999"
    # No file produced
    dest = Path(project) / ".paic/library/pdfs/arxiv_9999_99999.md"
    assert not dest.exists()


def test_attach_no_identifiers_errors(project):
    out = library_attach_paper_tool(str(project), {})
    assert out["error"] == "cannot_derive_cite_key"


def test_attach_explicit_source_missing_errors(project):
    paper = {"doi": "10.1/abc"}
    out = library_attach_paper_tool(
        str(project), paper, source_path="/does/not/exist.pdf"
    )
    assert out["error"] == "source_not_found"
    assert "does/not/exist.pdf" in out["detail"].replace("\\", "/")


def test_attach_uninitialized_project_errors(tmp_path):
    out = library_attach_paper_tool(str(tmp_path / "nope"), {"arxiv_id": "2401.12345"})
    assert out["error"] == "project_not_initialized"


def test_attach_no_source_path_no_arxiv_id_errors(project):
    """A doi-only paper without source_path can't be auto-located."""
    paper = {"doi": "10.1/abc", "title": "Some PubMed"}
    out = library_attach_paper_tool(str(project), paper)
    assert out["error"] == "source_not_found"
    assert "no source_path" in out["detail"]

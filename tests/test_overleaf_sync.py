"""Tests for paic.latex.overleaf_sync — three-way bidirectional sync.

Covers all 9 file-state classifications, four conflict strategies, deletion
gating, direction filters, dry_run, ignore patterns, and the three error-path
short-circuits (overleaf_disabled / drafts_dir_not_found /
overleaf_target_root_missing).

Filesystem only — no network. ``target_root`` is a tmp_path subdirectory that
stands in for ``~/Dropbox/Apps/Overleaf/``.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from paic.config import OverleafConfig
from paic.latex.overleaf_sync import mirror_drafts_to_overleaf
from paic.mcp_server.tools.workspace import workspace_init
from paic.workspace.paths import resolve_project
from paic.workspace.store import load_yaml

DEFAULT_IGNORE = ("*.pdf", "*.aux", "*.bak.*")


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fresh PAI-C project with `.paic/drafts/` ready to populate."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache

    reset_config_cache()
    p = tmp_path / "p"
    workspace_init(p, title="Test")
    drafts = p / ".paic" / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def target_root(tmp_path):
    """Stand-in for ``~/Dropbox/Apps/Overleaf/`` — its parent must exist."""
    root = tmp_path / "dropbox_overleaf"
    root.mkdir()
    return root


def _cfg(target_root: Path, **kwargs):
    """Build an OverleafConfig override with sensible test defaults."""
    defaults = dict(
        enabled=True,
        target_root=target_root,
        project_subdir="myproject",
        ignore_patterns=DEFAULT_IGNORE,
        conflict_strategy="keep_both",
        prompt_on_delete=True,
    )
    defaults.update(kwargs)
    return OverleafConfig(**defaults)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ------------------------------------------------------------ error paths
def test_disabled_returns_error(project, target_root):
    cfg = _cfg(target_root, enabled=False)
    out = mirror_drafts_to_overleaf(resolve_project(str(project)), cfg)
    assert out["error"] == "overleaf_disabled"


def test_drafts_missing_returns_error(project, target_root):
    # workspace_init may have seeded scaffolding into drafts/; nuke whatever's there
    drafts = project / ".paic" / "drafts"
    shutil.rmtree(drafts)
    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert out["error"] == "drafts_dir_not_found"


def test_target_root_missing_returns_error(project, tmp_path):
    nonexistent = tmp_path / "nope" / "dropbox_overleaf"
    cfg = _cfg(nonexistent)  # parent doesn't exist
    out = mirror_drafts_to_overleaf(resolve_project(str(project)), cfg)
    assert out["error"] == "overleaf_target_root_missing"


# ----------------------------------------------------- first-sync push/pull
def test_first_sync_pushes_local_only_files(project, target_root):
    drafts = project / ".paic" / "drafts"
    _write(drafts / "main.tex", r"\documentclass{article}")
    _write(drafts / "sections" / "01_intro.tex", "Intro body.")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    target = target_root / "myproject"
    assert sorted(out["pushed"]) == ["main.tex", "sections/01_intro.tex"]
    assert out["pulled"] == []
    assert out["conflicts"] == []
    assert (target / "main.tex").read_text() == r"\documentclass{article}"
    assert (target / "sections" / "01_intro.tex").is_file()
    # Baseline written
    baseline = load_yaml(project / ".paic" / "state" / "overleaf_sync.yaml")
    assert "main.tex" in baseline["files"]


def test_first_sync_pulls_remote_only_files(project, target_root):
    target = target_root / "myproject"
    _write(target / "main.tex", "remote body")
    _write(target / "sections" / "02_related.tex", "related work")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    drafts = project / ".paic" / "drafts"
    assert out["pushed"] == []
    assert sorted(out["pulled"]) == ["main.tex", "sections/02_related.tex"]
    assert (drafts / "main.tex").read_text() == "remote body"


def test_first_sync_both_new_same_hash(project, target_root):
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "main.tex", "same content")
    _write(target / "main.tex", "same content")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    # No transfer needed; both promote to baseline.
    assert out["pushed"] == []
    assert out["pulled"] == []
    assert out["conflicts"] == []
    baseline = load_yaml(project / ".paic" / "state" / "overleaf_sync.yaml")
    assert "main.tex" in baseline["files"]


def test_first_sync_both_new_diff_hash_keep_both(project, target_root):
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "main.tex", "local body")
    _write(target / "main.tex", "remote body")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert len(out["conflicts"]) == 1
    record = out["conflicts"][0]
    assert record["path"] == "main.tex"
    assert record["state"] == "both_new_diff"
    assert record["strategy"] == "keep_both"
    assert "remote_kept_as" in record
    # remote version pulled into local tree under conflict name
    assert (drafts / record["remote_kept_as"]).read_text() == "remote body"
    # local pushed back over remote
    assert (target / "main.tex").read_text() == "local body"


# ----------------------------------------------------- post-baseline merge
def _seed_baseline(project, target_root, files: dict[str, str]):
    """Run a first sync so each ``files[rel] = body`` ends up in baseline."""
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    target.mkdir(exist_ok=True)
    for rel, body in files.items():
        _write(drafts / rel, body)
        _write(target / rel, body)
    mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))


def test_unchanged_does_nothing(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert out["pushed"] == []
    assert out["pulled"] == []
    assert out["conflicts"] == []


def test_local_modified_pushes(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    _write(drafts / "main.tex", "v2 local")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert out["pushed"] == ["main.tex"]
    assert (target_root / "myproject" / "main.tex").read_text() == "v2 local"


def test_remote_modified_pulls(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    target = target_root / "myproject"
    _write(target / "main.tex", "v2 remote")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert out["pulled"] == ["main.tex"]
    assert (project / ".paic" / "drafts" / "main.tex").read_text() == "v2 remote"


def test_both_modified_diff_keep_both(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "main.tex", "v2 local")
    _write(target / "main.tex", "v2 remote")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert len(out["conflicts"]) == 1
    record = out["conflicts"][0]
    assert record["state"] == "both_modified_diff"
    conflict_name = record["remote_kept_as"]
    assert "overleaf-conflict" in conflict_name
    assert (drafts / conflict_name).read_text() == "v2 remote"
    assert (drafts / "main.tex").read_text() == "v2 local"  # untouched
    assert (target / "main.tex").read_text() == "v2 local"  # local pushed


def test_both_modified_diff_local_wins(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "main.tex", "v2 local")
    _write(target / "main.tex", "v2 remote")

    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root, conflict_strategy="local_wins"),
    )
    assert len(out["conflicts"]) == 1
    assert out["conflicts"][0]["strategy"] == "local_wins"
    assert (target / "main.tex").read_text() == "v2 local"
    # No conflict-named file in local tree
    assert not list(drafts.glob("*overleaf-conflict*"))


def test_both_modified_diff_remote_wins(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "main.tex", "v2 local")
    _write(target / "main.tex", "v2 remote")

    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root, conflict_strategy="remote_wins"),
    )
    assert len(out["conflicts"]) == 1
    assert (drafts / "main.tex").read_text() == "v2 remote"


def test_both_modified_diff_newer_wins(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "main.tex", "v2 local")
    # Make remote 10s newer than local
    _write(target / "main.tex", "v2 remote")
    future = int(time.time()) + 10
    os.utime(target / "main.tex", (future, future))

    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root, conflict_strategy="newer_wins"),
    )
    assert out["conflicts"][0]["winner"] == "remote"
    assert (drafts / "main.tex").read_text() == "v2 remote"


def test_both_modified_same_converges(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    # Both edited to identical "v2"
    _write(drafts / "main.tex", "v2")
    _write(target / "main.tex", "v2")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert out["pushed"] == []
    assert out["pulled"] == []
    assert out["conflicts"] == []
    # Baseline updated to v2
    baseline = load_yaml(project / ".paic" / "state" / "overleaf_sync.yaml")
    assert baseline["files"]["main.tex"]["sha256"]


# ------------------------------------------------------------- deletions
def test_local_delete_pending_by_default(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1", "extra.tex": "x"})
    (project / ".paic" / "drafts" / "extra.tex").unlink()

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert out["deletions_propagated"] == []
    assert len(out["deletions_pending"]) == 1
    assert out["deletions_pending"][0]["path"] == "extra.tex"
    assert out["deletions_pending"][0]["deleted_on"] == "local"
    # File still there on remote (no propagation)
    assert (target_root / "myproject" / "extra.tex").is_file()


def test_local_delete_propagates_with_confirm(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1", "extra.tex": "x"})
    (project / ".paic" / "drafts" / "extra.tex").unlink()

    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root),
        confirm_deletions=True,
    )
    assert out["deletions_pending"] == []
    assert len(out["deletions_propagated"]) == 1
    assert not (target_root / "myproject" / "extra.tex").exists()


def test_prompt_on_delete_false_blocks_propagation(project, target_root):
    """prompt_on_delete=False must veto even confirm_deletions=True."""
    _seed_baseline(project, target_root, {"main.tex": "v1", "extra.tex": "x"})
    (project / ".paic" / "drafts" / "extra.tex").unlink()

    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root, prompt_on_delete=False),
        confirm_deletions=True,
    )
    assert out["deletions_propagated"] == []
    assert len(out["deletions_pending"]) == 1


def test_remote_delete_pending(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1", "extra.tex": "x"})
    (target_root / "myproject" / "extra.tex").unlink()

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert len(out["deletions_pending"]) == 1
    assert out["deletions_pending"][0]["deleted_on"] == "remote"
    assert (project / ".paic" / "drafts" / "extra.tex").is_file()


def test_local_modified_remote_deleted_keep_both_pushes(project, target_root):
    """Conflict: local edited, remote deleted. keep_both → re-push local."""
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "main.tex", "v2 local edits")
    (target / "main.tex").unlink()

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert len(out["conflicts"]) == 1
    assert out["conflicts"][0]["state"] == "remote_deleted_local_modified"
    assert (target / "main.tex").read_text() == "v2 local edits"


def test_local_deleted_remote_modified_keep_both_pulls(project, target_root):
    """Conflict: local deleted, remote edited. keep_both → revive locally."""
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    (drafts / "main.tex").unlink()
    _write(target / "main.tex", "v2 remote edits")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert len(out["conflicts"]) == 1
    assert out["conflicts"][0]["state"] == "local_deleted_remote_modified"
    assert (drafts / "main.tex").read_text() == "v2 remote edits"


# --------------------------------------------------------------- direction
def test_pull_only_skips_push(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "main.tex", "v2 local")  # would push
    _write(target / "extra.tex", "remote new")  # should pull

    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root),
        direction="pull_only",
    )
    assert out["pushed"] == []
    assert out["pulled"] == ["extra.tex"]
    # remote main.tex untouched
    assert (target / "main.tex").read_text() == "v1"


def test_push_only_skips_pull(project, target_root):
    _seed_baseline(project, target_root, {"main.tex": "v1"})
    drafts = project / ".paic" / "drafts"
    target = target_root / "myproject"
    _write(drafts / "extra.tex", "local new")  # should push
    _write(target / "main.tex", "v2 remote")  # would pull

    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root),
        direction="push_only",
    )
    assert out["pushed"] == ["extra.tex"]
    assert out["pulled"] == []
    assert (drafts / "main.tex").read_text() == "v1"


# -------------------------------------------------------------- ignore + dry
def test_ignore_patterns_skip_compile_artifacts(project, target_root):
    drafts = project / ".paic" / "drafts"
    _write(drafts / "main.tex", "body")
    _write(drafts / "main.aux", "junk")  # ignored
    _write(drafts / "main.pdf", b"%PDF-1.4".decode("latin-1"))  # ignored
    _write(drafts / "sections" / "01_intro.tex", "intro")
    _write(drafts / "sections" / "01_intro.tex.bak.20260101T000000Z", "old")  # ignored

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    assert sorted(out["pushed"]) == ["main.tex", "sections/01_intro.tex"]
    assert out["ignored"] >= 3
    target = target_root / "myproject"
    assert not (target / "main.aux").exists()
    assert not (target / "main.pdf").exists()


def test_dry_run_does_not_write(project, target_root):
    drafts = project / ".paic" / "drafts"
    _write(drafts / "main.tex", "body")

    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root),
        dry_run=True,
    )
    assert out["pushed"] == ["main.tex"]
    assert out["dry_run"] is True
    target = target_root / "myproject"
    # Target file NOT created (dry_run)
    assert not (target / "main.tex").exists()
    # Baseline NOT written
    assert not (project / ".paic" / "state" / "overleaf_sync.yaml").exists()


def test_explicit_target_dir_overrides_config(project, target_root, tmp_path):
    drafts = project / ".paic" / "drafts"
    _write(drafts / "main.tex", "body")

    explicit = tmp_path / "alt_target"
    explicit.parent.mkdir(parents=True, exist_ok=True)
    out = mirror_drafts_to_overleaf(
        resolve_project(str(project)),
        _cfg(target_root, project_subdir="ignored_default"),
        target_dir=str(explicit),
    )
    assert out["target_dir"] == str(explicit)
    assert (explicit / "main.tex").is_file()
    # Default path NOT used
    assert not (target_root / "ignored_default" / "main.tex").exists()


def test_subdirectory_files_mirror_correctly(project, target_root):
    drafts = project / ".paic" / "drafts"
    _write(drafts / "sections" / "01_intro.tex", "intro")
    _write(drafts / "sections" / "02_related.tex", "related")
    _write(drafts / "figures" / "teaser.png.txt", "fake png")

    out = mirror_drafts_to_overleaf(resolve_project(str(project)), _cfg(target_root))
    target = target_root / "myproject"
    assert (target / "sections" / "01_intro.tex").is_file()
    assert (target / "sections" / "02_related.tex").is_file()
    assert (target / "figures" / "teaser.png.txt").is_file()
    assert sorted(out["pushed"]) == [
        "figures/teaser.png.txt",
        "sections/01_intro.tex",
        "sections/02_related.tex",
    ]

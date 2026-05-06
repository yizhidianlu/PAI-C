"""Workspace path resolution and init tool tests — Phase 1."""

from pathlib import Path

import pytest

from paic.mcp_server.tools.workspace import workspace_init, workspace_status
from paic.workspace.paths import (
    PROJECT_LAYOUT,
    find_project_root,
    resolve_project,
)


def test_workspace_init_creates_layout(tmp_path: Path):
    project = tmp_path / "my_paper"
    result = workspace_init(project, title="My Paper", venue="NeurIPS 2026")
    assert result["created"] is True
    assert Path(result["paic_dir"]).is_dir()
    for sub in PROJECT_LAYOUT:
        assert (project / ".paic" / sub).is_dir(), f"missing {sub}"
    assert (project / ".paic" / "project.yaml").is_file()


def test_workspace_init_idempotent(tmp_path: Path):
    project = tmp_path / "p2"
    workspace_init(project, title="t1")
    second = workspace_init(project, title="t2")  # update title
    assert second["created"] is False
    status = workspace_status(project)
    assert status["initialized"] is True
    assert status["project"]["title"] == "t2"


def test_workspace_status_uninitialized(tmp_path: Path):
    status = workspace_status(tmp_path)
    assert status["initialized"] is False


def test_find_project_root_walks_up(tmp_path: Path):
    project = tmp_path / "proj"
    workspace_init(project)
    nested = project / "src" / "deep" / "nested"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == project.resolve()


def test_resolve_project_explicit(tmp_path: Path):
    workspace_init(tmp_path)
    paths = resolve_project(tmp_path)
    assert paths.root == tmp_path.resolve()


def test_resolve_project_no_match_raises(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_project(None)

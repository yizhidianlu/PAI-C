"""Workspace abstractions: path resolution + filesystem store."""

from paic.workspace.paths import (
    PROJECT_LAYOUT,
    ProjectPaths,
    ensure_project_layout,
    find_project_root,
    resolve_project,
)
from paic.workspace.store import (
    load_yaml,
    read_text,
    save_yaml,
    write_text,
)

__all__ = [
    "PROJECT_LAYOUT",
    "ProjectPaths",
    "ensure_project_layout",
    "find_project_root",
    "load_yaml",
    "read_text",
    "resolve_project",
    "save_yaml",
    "write_text",
]

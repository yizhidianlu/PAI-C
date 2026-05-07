"""``paic_quality_gate_run`` — paper-level preflight (§quality phase 10)."""

from __future__ import annotations

from typing import Any

from paic.latex.quality_gate import run_quality_gate
from paic.workspace.paths import resolve_project


def quality_gate_run_tool(
    project_dir: str,
    compile_check: bool = False,
    overrides: list[str] | None = None,
) -> dict[str, Any]:
    """Run all eight phase-10 checks and return a structured GateResult.

    ``overrides`` is a list of issue ``kind`` values to drop from the
    output — used after the user explicitly acknowledged a category
    (e.g. ``["unresolved_todos"]`` to ship a draft with TODOs intact).
    ``compile_check`` is a hook for future LaTeX compiler integration;
    phase 10 stubs it.
    """
    paths = resolve_project(project_dir)
    if not paths.paic_dir.exists():
        return {"error": "project_not_initialized", "project_dir": str(paths.root)}
    result = run_quality_gate(
        paths,
        compile_check=compile_check,
        overrides=overrides,
    )
    return result.to_dict()

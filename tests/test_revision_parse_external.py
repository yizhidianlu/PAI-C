"""Tests for paic_revision_parse_external (ARS-fusion P0-2)."""

from __future__ import annotations

import pytest
import yaml

from paic.mcp_server.tools.revisions import (
    revision_extract_persist_tool,
    revision_parse_external_tool,
)
from paic.mcp_server.tools.workspace import workspace_init


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic_home"))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


@pytest.fixture
def host_routed_project(tmp_path, monkeypatch):
    home = tmp_path / ".paic_home"
    home.mkdir()
    cfg_yaml = home / "config.yaml"
    cfg_yaml.write_text(
        yaml.safe_dump({
            "providers": {"anthropic": {"mode": "api_key", "api_key_env": "X_KEY"}},
            "routing": {
                "default": "anthropic",
                "overrides": {"revision_parse_external": "host"},
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("PAIC_HOME", str(home))
    from paic.config import reset_config_cache
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    return project_dir


def test_revision_parse_external_returns_host_directive(host_routed_project):
    out = revision_parse_external_tool(
        str(host_routed_project),
        raw_text=(
            "Reviewer 1: The methodology section needs more detail on sampling. "
            "Also, please add a baseline comparison.\n\n"
            "Reviewer 2: The introduction is well-written but the conclusion "
            "overclaims. Consider tempering the language."
        ),
        format_hint="numbered",
        round_num=1,
    )
    assert out["mode"] == "host_orchestration"
    assert out["node"] == "revision_parse_external"
    assert out["next_tool"] == "mcp__paic__paic_revision_extract_persist"
    # User prompt contains the verbatim input
    assert "Reviewer 1" in out["user_prompt"]
    assert "Reviewer 2" in out["user_prompt"]
    assert "FORMAT HINT: numbered" in out["user_prompt"]
    # Schema hint is the _ExtractFields shape
    assert "tasks" in (out["schema_hint"].get("properties") or {})


def test_revision_parse_external_rejects_empty_text(project):
    out = revision_parse_external_tool(str(project), raw_text="")
    assert out["error"] == "raw_text_required"


def test_revision_parse_external_rejects_too_short(project):
    out = revision_parse_external_tool(str(project), raw_text="too short")
    assert out["error"] == "raw_text_too_short"


def test_revision_parse_external_includes_editor_decision(host_routed_project):
    out = revision_parse_external_tool(
        str(host_routed_project),
        raw_text=(
            "R1: The paper would benefit from more experiments on smaller datasets. "
            "R2: Consider clarifying the contribution statement."
        ),
        editor_decision=(
            "Major revision required. Please address Reviewer 1's experiment "
            "request and Reviewer 2's contribution comment."
        ),
    )
    assert "EDITOR DECISION" in out["user_prompt"]
    assert "highest priority" in out["user_prompt"]


def test_revision_parse_external_truncates_long_paper_draft(host_routed_project):
    long_draft = "x" * 8000
    out = revision_parse_external_tool(
        str(host_routed_project),
        raw_text="R1: please rewrite section 3, it lacks clarity.",
        paper_draft=long_draft,
    )
    assert "[truncated" in out["user_prompt"]


def test_revision_parse_external_inline_path_persists_tasks(project, monkeypatch):
    """Cloud / fixed-backend path runs LLM inline and writes tasks to disk."""
    from paic.library.revisions import _ExtractFields, _ExtractedTask

    class _StubLLM:
        model = "stub"

        def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
            assert schema is _ExtractFields
            return _ExtractFields(tasks=[
                _ExtractedTask(
                    severity="major",
                    target_kind="section",
                    target_ref="03_method",
                    summary="add baseline",
                    detail="R1 wants a baseline comparison",
                    patch_hint="add LongBench baseline to Table 2",
                    source_persona="R1",
                ),
                _ExtractedTask(
                    severity="minor",
                    target_kind="section",
                    target_ref="06_conclusion",
                    summary="temper language",
                    patch_hint="soften 'state-of-the-art' to 'competitive'",
                    source_persona="R2",
                ),
            ])

    out = revision_parse_external_tool(
        str(project),
        raw_text=(
            "Reviewer 1: methodology needs a baseline.\n"
            "Reviewer 2: conclusion overclaims."
        ),
        round_num=1,
        llm=_StubLLM(),
    )
    assert "error" not in out
    assert out["extracted_count"] == 2
    assert out["source"] == "external_text"
    assert len(out["paths"]) == 2

    # Tasks listed via revision_list
    from paic.mcp_server.tools.revisions import revision_list_tool
    listed = revision_list_tool(str(project))
    assert listed["count"] == 2
    summaries = sorted(t["summary"] for t in listed["tasks"])
    assert summaries == ["add baseline", "temper language"]


def test_revision_extract_persist_consumes_host_directive_output(project):
    """End-to-end host path: directive metadata round-trip via the existing persist."""
    fake_extracted = {
        "tasks": [
            {
                "severity": "major",
                "target_kind": "section",
                "target_ref": "03_method",
                "summary": "add baseline",
                "patch_hint": "add LongBench baseline",
                "source_persona": "R1",
            },
        ],
    }
    out = revision_extract_persist_tool(
        str(project),
        extracted=fake_extracted,
        round_num=1,
    )
    assert out["extracted_count"] == 1
    assert len(out["paths"]) == 1
    # Confirm it lands on disk under .paic/revisions/
    from paic.workspace.paths import resolve_project
    paths = resolve_project(str(project))
    # Filename pattern is ``<round_zero_padded_3>_<ulid>.yaml`` (see task_path).
    files = list(paths.revisions_dir.glob("001_*.yaml"))
    assert len(files) == 1

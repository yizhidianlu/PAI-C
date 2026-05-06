"""paic_draft_polish tool tests — §20."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paic.llm.backends.base import LLMResponse
from paic.mcp_server.tools.draft import (
    draft_polish_persist_tool,
    draft_polish_tool,
)
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.idea import IdeaCard
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


class _StubLLM:
    """Minimal LLMClient-shaped stub.

    Records the last call and returns a canned response. Tests inject this
    into ``polish_section`` via the ``llm`` parameter (which the MCP tool
    layer doesn't expose — for those tests we monkeypatch the default
    client factory instead).
    """

    def __init__(self, response_text: str = ""):
        self.response_text = response_text
        self.calls: list[dict] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(text=self.response_text, usage={})


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-anthropic")
    from paic.config import reset_config_cache

    reset_config_cache()

    p = tmp_path / "p"
    workspace_init(p, title="Test Paper")
    paths = resolve_project(p)

    sections = paths.drafts_dir / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "01_intro.tex").write_text(
        r"\section{Introduction}" "\n"
        r"This paper studies things \cite{smith2024}." "\n"
        r"In recent years, work has been done." "\n",
        encoding="utf-8",
    )
    return p


def _patch_default_client(monkeypatch, response_text: str):
    """Make ``get_default_client`` return a stub for the duration of the test."""
    stub = _StubLLM(response_text=response_text)
    from paic.llm import client as client_mod

    monkeypatch.setattr(client_mod, "get_default_client", lambda: stub)
    return stub


# ---------------------------------------------------------------- happy paths
def test_polish_clarify_writes_and_backs_up(project, monkeypatch):
    polished_text = (
        r"\section{Introduction}" "\n"
        r"This paper investigates the topic \cite{smith2024}." "\n"
        r"Prior work has explored related directions." "\n"
    )
    stub = _patch_default_client(monkeypatch, polished_text)

    out = draft_polish_tool(str(project), "01_intro", mode="clarify")

    assert out.get("error") is None, out
    assert out["wrote"] is True
    assert out["mode"] == "clarify"
    assert out["validation"]["ok"] is True
    assert "diff" in out and out["diff"]  # non-empty diff

    # Backup created with timestamp suffix
    backup = Path(out["backup_path"])
    assert backup.is_file()
    assert ".bak." in backup.name

    # File now contains the polished text
    written = (Path(project) / ".paic/drafts/sections/01_intro.tex").read_text(encoding="utf-8")
    assert "investigates" in written

    # LLM was called with node="draft_polish"
    assert stub.calls[0]["node"] == "draft_polish"


def test_polish_dry_run_does_not_write(project, monkeypatch):
    polished_text = (
        r"\section{Introduction}" "\n"
        r"Tighter version \cite{smith2024}." "\n"
    )
    _patch_default_client(monkeypatch, polished_text)

    intro = Path(project) / ".paic/drafts/sections/01_intro.tex"
    before = intro.read_text(encoding="utf-8")

    out = draft_polish_tool(str(project), "01_intro", mode="tighten", dry_run=True)

    assert out["wrote"] is False
    assert out["backup_path"] is None
    assert intro.read_text(encoding="utf-8") == before
    # No backup files created
    siblings = list(intro.parent.glob("01_intro.tex.bak.*"))
    assert siblings == []


def test_polish_strips_markdown_fence(project, monkeypatch):
    polished_with_fence = (
        "```latex\n"
        r"\section{Introduction}" "\n"
        r"Cleaned up text \cite{smith2024}." "\n"
        "```\n"
    )
    _patch_default_client(monkeypatch, polished_with_fence)

    out = draft_polish_tool(str(project), "01_intro", mode="clarify")

    assert out.get("error") is None, out
    written = Path(out["section"]).read_text(encoding="utf-8")
    assert "```" not in written
    assert "Cleaned up text" in written


def test_polish_resolves_alias(project, monkeypatch):
    """User-friendly aliases like 'intro' resolve to 01_intro.tex."""
    polished = r"\section{Introduction}" "\n" r"Text \cite{smith2024}." "\n"
    _patch_default_client(monkeypatch, polished)

    out = draft_polish_tool(str(project), "intro", mode="clarify")
    assert out.get("error") is None
    assert out["section"].endswith("01_intro.tex")


# ---------------------------------------------------------------- guard rejections
def test_polish_rejects_added_cite_key(project, monkeypatch):
    polished_with_new_cite = (
        r"\section{Introduction}" "\n"
        r"Text \cite{smith2024} and \cite{novel2026}." "\n"  # invented cite
    )
    _patch_default_client(monkeypatch, polished_with_new_cite)

    out = draft_polish_tool(str(project), "01_intro", mode="clarify")

    assert out["error"] == "latex_validation_failed"
    assert out["validation"]["cite_keys_preserved"] is False
    # Original file untouched
    intro = Path(project) / ".paic/drafts/sections/01_intro.tex"
    assert "novel2026" not in intro.read_text(encoding="utf-8")


def test_polish_rejects_unbalanced_begin_end(project, monkeypatch):
    polished_broken = (
        r"\section{Introduction}" "\n"
        r"\begin{equation} y = x \cite{smith2024}" "\n"  # missing \end
    )
    _patch_default_client(monkeypatch, polished_broken)

    out = draft_polish_tool(str(project), "01_intro", mode="formalize")

    assert out["error"] == "latex_validation_failed"
    assert out["validation"]["begin_end_balanced"] is False


# ---------------------------------------------------------------- error paths
def test_polish_section_not_found(project, monkeypatch):
    _patch_default_client(monkeypatch, "stub")
    out = draft_polish_tool(str(project), "99_nonexistent", mode="clarify")
    assert out["error"] == "section_not_found"


def test_polish_invalid_mode(project):
    out = draft_polish_tool(str(project), "01_intro", mode="rewrite-from-scratch")
    assert out["error"] == "invalid_mode"
    assert "valid_modes" in out


def test_polish_section_empty(project, monkeypatch):
    _patch_default_client(monkeypatch, "stub")
    intro = Path(project) / ".paic/drafts/sections/01_intro.tex"
    intro.write_text("\n", encoding="utf-8")
    out = draft_polish_tool(str(project), "01_intro", mode="clarify")
    assert out["error"] == "section_empty"


def test_polish_uninitialized_project(tmp_path):
    out = draft_polish_tool(str(tmp_path / "nope"), "01_intro")
    assert out["error"] == "project_not_initialized"


# ---------------------------------------------------------------- expand mode
def test_polish_expand_loads_idea_context(project, monkeypatch):
    """expand mode pulls idea / experiment yaml into the user prompt."""
    idea = IdeaCard(
        id="idea_x",
        title="Custom Approach",
        one_liner="A tight one-liner.",
        motivation="The motivation.",
        proposed_approach="The approach.",
        novelty_claim="Novel claim.",
        expected_contribution="Expected contribution.",
        feasibility_score=0.5,
        novelty_score=0.5,
        impact_score=0.5,
        composite_score=0.5,
        created_at=datetime.now(UTC),
    )
    save_yaml(Path(project) / ".paic/ideas/idea_x.yaml", idea.model_dump(mode="json"))

    polished_text = (
        r"\section{Introduction}" "\n"
        r"Expanded text \cite{smith2024}." "\n"
    )
    stub = _patch_default_client(monkeypatch, polished_text)

    out = draft_polish_tool(
        str(project), "01_intro", mode="expand", idea_id="idea_x"
    )

    assert out.get("error") is None, out
    user_prompt = stub.calls[0]["user"]
    assert "Custom Approach" in user_prompt
    assert "expand" in user_prompt.lower()


# ---------------------------------------------------------------- host orchestration
def test_polish_host_orchestration_returns_directive(project, monkeypatch):
    """When draft_polish is routed to host, return a directive instead of calling LLM."""
    home = Path(project).parent / ".paic"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PAIC_HOME", str(home))
    import yaml

    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {"anthropic": {"mode": "api_key"}},
                "routing": {
                    "default": "anthropic",
                    "overrides": {"draft_polish": "host"},
                },
            }
        ),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache

    reset_config_cache()

    out = draft_polish_tool(str(project), "01_intro", mode="clarify")
    assert out.get("mode") == "host_orchestration"
    assert "user_prompt" in out
    assert "original_hash" in out
    assert out["next_tool"] == "mcp__paic__paic_draft_polish_persist"


# ---------------------------------------------------------------- persist
def test_polish_persist_writes_with_correct_hash(project):
    intro = Path(project) / ".paic/drafts/sections/01_intro.tex"
    original = intro.read_text(encoding="utf-8")

    import hashlib
    correct_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()

    polished = (
        r"\section{Introduction}" "\n"
        r"Polished body \cite{smith2024}." "\n"
    )

    out = draft_polish_persist_tool(
        str(project), "01_intro", polished, correct_hash
    )

    assert out.get("error") is None, out
    assert out["wrote"] is True
    assert "Polished body" in intro.read_text(encoding="utf-8")
    assert Path(out["backup_path"]).is_file()


def test_polish_persist_rejects_hash_mismatch(project):
    polished = "anything"
    out = draft_polish_persist_tool(
        str(project), "01_intro", polished, "deadbeef" * 8
    )
    assert out["error"] == "original_hash_mismatch"


def test_polish_persist_rejects_invalid_polished(project):
    intro = Path(project) / ".paic/drafts/sections/01_intro.tex"
    import hashlib
    correct_hash = hashlib.sha256(intro.read_text(encoding="utf-8").encode("utf-8")).hexdigest()

    polished_bad = (
        r"\section{Introduction}" "\n"
        r"Body \cite{smith2024} and \cite{NEW_CITE}." "\n"
    )
    out = draft_polish_persist_tool(str(project), "01_intro", polished_bad, correct_hash)
    assert out["error"] == "latex_validation_failed"
    # File untouched
    assert "NEW_CITE" not in intro.read_text(encoding="utf-8")

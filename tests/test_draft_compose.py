"""paic_draft_compose tool tests — §21."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paic.llm.backends.base import LLMResponse
from paic.mcp_server.tools.draft import (
    draft_compose_persist_tool,
    draft_compose_tool,
)
from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.workspace import workspace_init
from paic.schemas.experiment import ExperimentPlan, Metric
from paic.schemas.idea import IdeaCard
from paic.workspace.paths import resolve_project
from paic.workspace.store import save_yaml


class _StubLLM:
    def __init__(self, response_text: str = ""):
        self.response_text = response_text
        self.calls: list[dict] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(text=self.response_text, usage={})


def _patch_default_client(monkeypatch, response_text: str):
    stub = _StubLLM(response_text=response_text)
    from paic.llm import client as client_mod

    monkeypatch.setattr(client_mod, "get_default_client", lambda: stub)
    return stub


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-anthropic")
    from paic.config import reset_config_cache

    reset_config_cache()

    p = tmp_path / "p"
    workspace_init(p, title="Test Paper")
    paths = resolve_project(p)

    # Seed library with 2 papers — gives compose a non-empty cite-key whitelist.
    library_add_tool(
        str(p),
        [
            {
                "arxiv_id": "2401.12345",
                "title": "Foundational Paper",
                "authors": ["A. Smith"],
                "year": 2024,
                "abstract": "An important foundational paper on the topic.",
            },
            {
                "doi": "10.1234/abc",
                "title": "Other Reference",
                "authors": ["B. Jones"],
                "year": 2023,
                "abstract": "Studies related approaches at small scale.",
            },
        ],
    )

    # Seed an idea so most modes have something to align with.
    idea = IdeaCard(
        id="idea_x",
        title="Latent Diffusion for Tabular",
        one_liner="Apply latent diffusion to small tabular datasets.",
        motivation="Tabular generation is dominated by GANs and VAEs.",
        proposed_approach="Two-stage: VAE encoder + DDPM in latent space.",
        novelty_claim="First latent diffusion for tabular <100k rows.",
        expected_contribution="A simple diffusion baseline for tabular.",
        grounded_in=["2401.12345"],
        risk_factors=["latent collapse"],
        feasibility_score=0.7,
        novelty_score=0.5,
        impact_score=0.4,
        composite_score=0.55,
        created_at=datetime.now(UTC),
    )
    save_yaml(p / ".paic/ideas/idea_x.yaml", idea.model_dump(mode="json"))

    plan = ExperimentPlan(
        id="exp_x",
        idea_id="idea_x",
        research_questions=["Does latent diffusion outperform CTGAN?"],
        hypotheses=["Latent diffusion improves likelihood by >5%."],
        proposed_method="Encode rows via 64-dim VAE, train DDPM on latent.",
        metrics=[Metric(name="negative log-likelihood", direction="min", primary=True)],
        ablations=[],
        compute_budget="single A6000",
        success_criteria=["primary metric -5% relative vs CTGAN"],
        threats_to_validity=["small dataset noise"],
        timeline_weeks=3,
        created_at=datetime.now(UTC),
    )
    save_yaml(p / ".paic/experiments/exp_x.yaml", plan.model_dump(mode="json"))

    # Seed a section file so compose has something to read in from_stub mode.
    sections = paths.drafts_dir / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "02_related.tex").write_text(
        r"\section{Related Work}" "\n"
        "% TODO: discuss prior work on tabular generation.\n"
        "% TODO: contrast with diffusion baselines.\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------- happy paths
def test_compose_happy_writes_and_backs_up(project, monkeypatch):
    composed_text = (
        r"\section{Related Work}" "\n"
        + "Tabular generation has been studied extensively " * 10
        + r" \cite{arxiv_2401_12345}. Subsequent work \cite{doi_10_1234_abc} extended this." + "\n"
    )
    stub = _patch_default_client(monkeypatch, composed_text)

    out = draft_compose_tool(
        str(project),
        "02_related",
        mode="from_stub",
        idea_id="idea_x",
    )

    assert out.get("error") is None, out
    assert out["wrote"] is True
    assert out["section_name"] == "02_related"
    assert out["mode"] == "from_stub"
    assert out["validation"]["ok"] is True
    assert "arxiv_2401_12345" in out["cite_keys_used"]
    assert "doi_10_1234_abc" in out["cite_keys_used"]
    assert out["library_size"] == 2
    assert Path(out["backup_path"]).is_file()
    assert stub.calls[0]["node"] == "draft_compose"
    # Section file has the new content
    written = (Path(project) / ".paic/drafts/sections/02_related.tex").read_text(encoding="utf-8")
    assert "Subsequent work" in written


def test_compose_dry_run_no_write(project, monkeypatch):
    composed_text = (
        r"\section{Related Work}" "\n"
        + "X " * 60
        + r"\cite{arxiv_2401_12345}." + "\n"
    )
    _patch_default_client(monkeypatch, composed_text)

    related = Path(project) / ".paic/drafts/sections/02_related.tex"
    before = related.read_text(encoding="utf-8")

    out = draft_compose_tool(str(project), "02_related", mode="from_stub", idea_id="idea_x", dry_run=True)
    assert out["wrote"] is False
    assert out["backup_path"] is None
    assert related.read_text(encoding="utf-8") == before


def test_compose_strips_markdown_fence(project, monkeypatch):
    composed_with_fence = (
        "```latex\n"
        r"\section{Related Work}" "\n"
        + "Body " * 50
        + r"\cite{arxiv_2401_12345}." + "\n"
        "```\n"
    )
    _patch_default_client(monkeypatch, composed_with_fence)

    out = draft_compose_tool(str(project), "02_related", mode="from_stub", idea_id="idea_x")
    assert out.get("error") is None, out
    written = Path(out["section"]).read_text(encoding="utf-8")
    assert "```" not in written


def test_compose_resolves_section_alias(project, monkeypatch):
    composed = (
        r"\section{Related Work}" "\n"
        + "Body " * 60
        + r"\cite{arxiv_2401_12345}." + "\n"
    )
    _patch_default_client(monkeypatch, composed)

    out = draft_compose_tool(str(project), "related", mode="from_stub", idea_id="idea_x")
    assert out["section"].endswith("02_related.tex")


def test_compose_loads_library_context(project, monkeypatch):
    """The user prompt should list every library paper as a [cite_key] bullet."""
    composed = (
        r"\section{Related Work}" "\n"
        + "Body " * 60
        + r"\cite{arxiv_2401_12345}." + "\n"
    )
    stub = _patch_default_client(monkeypatch, composed)

    draft_compose_tool(str(project), "02_related", mode="from_stub", idea_id="idea_x")
    user_prompt = stub.calls[0]["user"]
    assert "[arxiv_2401_12345]" in user_prompt
    assert "[doi_10_1234_abc]" in user_prompt
    assert "Foundational Paper" in user_prompt
    assert "Other Reference" in user_prompt


def test_compose_target_words_in_prompt(project, monkeypatch):
    composed = (
        r"\section{Related Work}" "\n"
        + "Body " * 60
        + r"\cite{arxiv_2401_12345}." + "\n"
    )
    stub = _patch_default_client(monkeypatch, composed)

    draft_compose_tool(
        str(project), "02_related", mode="from_stub", idea_id="idea_x", target_words=600
    )
    user_prompt = stub.calls[0]["user"]
    assert "600" in user_prompt


def test_compose_from_scratch_excludes_stub(project, monkeypatch):
    """from_scratch mode should not include the section file content as a stub."""
    composed = (
        r"\section{Related Work}" "\n"
        + "Body " * 60
        + r"\cite{arxiv_2401_12345}." + "\n"
    )
    stub = _patch_default_client(monkeypatch, composed)

    draft_compose_tool(
        str(project), "02_related", mode="from_scratch", idea_id="idea_x"
    )
    user_prompt = stub.calls[0]["user"]
    # The TODO from the seeded stub should not appear in the prompt
    assert "TODO: discuss prior work on tabular generation" not in user_prompt


# ---------------------------------------------------------------- guard rejections
def test_compose_rejects_unknown_cite_key(project, monkeypatch):
    composed_with_invented = (
        r"\section{Related Work}" "\n"
        + "Body " * 60
        + r"\cite{arxiv_2401_12345} and \cite{novel_invented_2099}." + "\n"
    )
    _patch_default_client(monkeypatch, composed_with_invented)

    out = draft_compose_tool(str(project), "02_related", mode="from_stub", idea_id="idea_x")
    assert out["error"] == "latex_validation_failed"
    assert "novel_invented_2099" in out["cite_keys_missing_from_library"]
    # Original file untouched
    related = Path(project) / ".paic/drafts/sections/02_related.tex"
    assert "novel_invented_2099" not in related.read_text(encoding="utf-8")


def test_compose_rejects_unbalanced_begin_end(project, monkeypatch):
    composed_broken = (
        r"\section{Related Work}" "\n"
        + "Body " * 60 + "\n"
        + r"\begin{itemize} \item one \cite{arxiv_2401_12345}" + "\n"
        # missing \end{itemize}
    )
    _patch_default_client(monkeypatch, composed_broken)

    out = draft_compose_tool(str(project), "02_related", mode="from_stub", idea_id="idea_x")
    assert out["error"] == "latex_validation_failed"
    assert out["validation"]["begin_end_balanced"] is False


# ---------------------------------------------------------------- error paths
def test_compose_section_not_found(project, monkeypatch):
    _patch_default_client(monkeypatch, "stub")
    out = draft_compose_tool(str(project), "99_nonexistent", mode="from_stub", idea_id="idea_x")
    assert out["error"] == "section_not_found"


def test_compose_invalid_mode(project):
    out = draft_compose_tool(str(project), "02_related", mode="from-thin-air", idea_id="idea_x")
    assert out["error"] == "invalid_mode"
    assert "from_stub" in out["valid_modes"]


def test_compose_uninitialized_project(tmp_path):
    out = draft_compose_tool(str(tmp_path / "nope"), "02_related")
    assert out["error"] == "project_not_initialized"


def test_compose_empty_library_for_related_section(project, monkeypatch):
    """compose for related work without any library papers → empty_library error."""
    # Empty out the library
    selected = Path(project) / ".paic/library/selected.yaml"
    save_yaml(selected, {"papers": []})
    _patch_default_client(monkeypatch, "stub")

    out = draft_compose_tool(str(project), "02_related", mode="from_stub", idea_id="idea_x")
    assert out["error"] == "empty_library"


def test_compose_empty_library_ok_for_abstract(project, monkeypatch):
    """abstract / conclusion don't need cites — empty library should still work."""
    selected = Path(project) / ".paic/library/selected.yaml"
    save_yaml(selected, {"papers": []})

    sections = Path(project) / ".paic/drafts/sections"
    (sections / "00_abstract.tex").write_text(
        r"\section*{Abstract}" "\nTODO\n", encoding="utf-8"
    )

    composed_text = (
        r"\section*{Abstract}" "\n"
        + "We introduce a new approach. " * 20 + "\n"
    )
    _patch_default_client(monkeypatch, composed_text)

    out = draft_compose_tool(
        str(project), "00_abstract", mode="from_stub", idea_id="idea_x"
    )
    assert out.get("error") is None, out
    assert out["library_size"] == 0


# ---------------------------------------------------------------- host orchestration
def test_compose_host_orchestration_returns_directive(project, monkeypatch):
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
                    "overrides": {"draft_compose": "host"},
                },
            }
        ),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    reset_config_cache()

    out = draft_compose_tool(str(project), "02_related", mode="from_stub", idea_id="idea_x")
    assert out.get("mode") == "host_orchestration"
    assert "user_prompt" in out
    assert "original_hash" in out
    assert "library_cite_keys" in out
    assert out["next_tool"] == "mcp__paic__paic_draft_compose_persist"


# ---------------------------------------------------------------- persist
def test_compose_persist_writes_with_correct_hash(project):
    related = Path(project) / ".paic/drafts/sections/02_related.tex"
    original = related.read_text(encoding="utf-8")
    import hashlib
    correct_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()

    composed = (
        r"\section{Related Work}" "\n"
        + "Body " * 60
        + r"\cite{arxiv_2401_12345}." + "\n"
    )

    out = draft_compose_persist_tool(
        str(project), "02_related", composed, correct_hash
    )
    assert out.get("error") is None, out
    assert out["wrote"] is True
    assert "arxiv_2401_12345" in out["cite_keys_used"]
    assert "Body" in related.read_text(encoding="utf-8")


def test_compose_persist_rejects_hash_mismatch(project):
    out = draft_compose_persist_tool(
        str(project), "02_related", "anything", "deadbeef" * 8
    )
    assert out["error"] == "original_hash_mismatch"


def test_compose_persist_rejects_unknown_cite(project):
    related = Path(project) / ".paic/drafts/sections/02_related.tex"
    import hashlib
    correct_hash = hashlib.sha256(related.read_text(encoding="utf-8").encode("utf-8")).hexdigest()

    composed_bad = (
        r"\section{Related Work}" "\n"
        + "Body " * 60
        + r"\cite{not_in_library}." + "\n"
    )
    out = draft_compose_persist_tool(
        str(project), "02_related", composed_bad, correct_hash
    )
    assert out["error"] == "latex_validation_failed"
    assert "not_in_library" in out["cite_keys_missing_from_library"]

"""Host orchestration tests — §16.

Covers:
- Router: ``host`` recognized, ``HostOrchestrationRequired`` raised, default
  vs override, fallback ignored when target is host.
- summarize_run: returns ``mode: host_orchestration`` when summarize routes
  to host, with markdown + schema_hint + instructions.
- summarize_persist: validates schema, writes md+yaml, returns persisted=True.
- doctor: emits the host orchestration row only when host nodes exist.
"""

from __future__ import annotations

import pytest
import yaml

from paic.config import reset_config_cache
from paic.llm.backends import HostOrchestrationRequired
from paic.llm.router import LLMRouter
from paic.mcp_server.tools.library import library_add_tool
from paic.mcp_server.tools.summarize import _SummaryFields, summarize_persist, summarize_run
from paic.mcp_server.tools.workspace import workspace_init


def _write_config(home, **routing):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "anthropic": {"mode": "claude_agent_sdk", "model": "claude-opus-4-7"}
                },
                "routing": routing,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reset_config_cache()
    project_dir = tmp_path / "p"
    workspace_init(project_dir)
    library_add_tool(
        str(project_dir),
        [{"arxiv_id": "2401.12345", "title": "Sample Paper", "authors": ["A", "B"]}],
    )
    return project_dir


# --------------------------------------------------------------------- router
def test_router_host_override_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    _write_config(
        tmp_path / ".paic",
        default="anthropic",
        overrides={"summarize": "host"},
    )
    reset_config_cache()
    from paic.config import load_config

    cfg = load_config()
    router = LLMRouter(cfg)
    with pytest.raises(HostOrchestrationRequired) as exc_info:
        router.for_node("summarize")
    assert exc_info.value.node == "summarize"
    # Other nodes still resolve normally (anthropic.claude_agent_sdk by config).
    # We don't actually instantiate the SDK here — just check that no host
    # exception is raised for non-host nodes.
    try:
        router.for_node("ideate_brainstorm")
    except HostOrchestrationRequired:
        pytest.fail("non-host node should not raise HostOrchestrationRequired")
    except Exception:
        # Some other backend-instantiation error is fine; we only care about
        # the routing decision, not the runtime availability.
        pass


def test_router_host_default_raises_for_all_nodes(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    _write_config(tmp_path / ".paic", default="host")
    reset_config_cache()
    from paic.config import load_config

    cfg = load_config()
    router = LLMRouter(cfg)
    for node in ("summarize", "ideate_brainstorm", "review_persona_methodology"):
        with pytest.raises(HostOrchestrationRequired):
            router.for_node(node)


def test_router_is_host_orchestrated(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    _write_config(
        tmp_path / ".paic",
        default="anthropic",
        overrides={"summarize": "host"},
    )
    reset_config_cache()
    from paic.config import load_config

    router = LLMRouter(load_config())
    assert router.is_host_orchestrated("summarize") is True
    assert router.is_host_orchestrated("ideate_brainstorm") is False


def test_router_describe_lists_host_nodes(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    _write_config(
        tmp_path / ".paic",
        default="anthropic",
        overrides={"summarize": "host", "experiment_design": "host"},
    )
    reset_config_cache()
    from paic.config import load_config

    info = LLMRouter(load_config()).describe()
    assert sorted(info["host_nodes"]) == ["experiment_design", "summarize"]


def test_router_default_host_in_describe(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    _write_config(tmp_path / ".paic", default="host")
    reset_config_cache()
    from paic.config import load_config

    info = LLMRouter(load_config()).describe()
    assert "<default>" in info["host_nodes"]


def test_router_host_fallback_ignored(tmp_path, monkeypatch):
    """fallback set to host shouldn't try to wrap with AutoFallbackBackend."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    _write_config(
        tmp_path / ".paic",
        default="anthropic.api_key",
        fallback="host",
    )
    reset_config_cache()
    from paic.config import load_config

    router = LLMRouter(load_config())
    # for_node should NOT raise — non-host primary, host fallback is just ignored
    backend = router.for_node("summarize")
    assert backend.name == "anthropic.api_key"


# --------------------------------------------------------------- summarize_run
def test_summarize_run_returns_host_directive(project, tmp_path, monkeypatch):
    """When summarize routes to host, return mode=host_orchestration with markdown."""
    home = tmp_path / ".paic"
    _write_config(
        home,
        default="anthropic",
        overrides={"summarize": "host"},
    )
    reset_config_cache()
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: "# Title\n\nFull paper body here.",
    )

    out = summarize_run(str(project), "2401.12345")

    assert out["mode"] == "host_orchestration"
    assert out["paper_id"] == "2401.12345"
    assert out["markdown"] == "# Title\n\nFull paper body here."
    assert "schema_hint" in out
    assert "problem" in out["schema_hint"]["properties"]
    assert out["next_tool"] == "mcp__paic__paic_summarize_persist"
    assert "instructions" in out
    # No summary file should be written yet
    assert not (project / ".paic/library/summaries/arxiv_2401_12345.yaml").exists()


def test_summarize_run_host_uses_caller_supplied_text(project, tmp_path, monkeypatch):
    """paper_text path also produces a host directive (no LLM call)."""
    home = tmp_path / ".paic"
    _write_config(
        home,
        default="anthropic",
        overrides={"summarize": "host"},
    )
    reset_config_cache()
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: None,
    )

    out = summarize_run(
        str(project), "2401.12345", paper_text="caller body"
    )
    assert out["mode"] == "host_orchestration"
    assert out["markdown"] == "caller body"
    assert out["text_source"] == "caller_supplied"


def test_summarize_run_host_skipped_when_cached(project, tmp_path, monkeypatch):
    """If a summary is already on disk, return from_cache without going host."""
    # First, write a real summary in non-host mode to populate the cache
    monkeypatch.setattr(
        "paic.mcp_server.tools.summarize.read_local_markdown",
        lambda paper_id, cfg=None: "body",
    )

    class _StubLLM:
        model = "stub-model"
        def complete_json(self, *, system, user, schema, max_tokens=4096, temperature=0.0, node=None):
            return _SummaryFields(problem="P", method="M")

    summarize_run(str(project), "2401.12345", llm=_StubLLM())
    assert (project / ".paic/library/summaries/arxiv_2401_12345.yaml").is_file()

    # Now flip to host mode and re-run. Cache hit should short-circuit before
    # the routing check, so we never see the host directive.
    home = tmp_path / ".paic"
    _write_config(
        home,
        default="anthropic",
        overrides={"summarize": "host"},
    )
    reset_config_cache()

    out = summarize_run(str(project), "2401.12345")
    assert out.get("from_cache") is True
    assert "mode" not in out


# ----------------------------------------------------------- summarize_persist
def test_persist_writes_files(project):
    out = summarize_persist(
        str(project),
        "2401.12345",
        {
            "problem": "Generating long videos efficiently",
            "method": "Latent diffusion + temporal causal attention",
            "key_results": ["10x speedup", "FVD 23.4"],
            "limitations": ["Long-range coherence drops past 30s"],
            "techniques": ["diffusion model", "VAE"],
            "relevance_to_project": "Direct competitor on video gen",
        },
    )
    assert out["persisted"] is True
    assert out["mode"] == "host_orchestration"
    md = project / ".paic/library/summaries/arxiv_2401_12345.md"
    yml = project / ".paic/library/summaries/arxiv_2401_12345.yaml"
    assert md.is_file()
    assert yml.is_file()
    body = md.read_text(encoding="utf-8")
    assert "Latent diffusion" in body
    assert "host:claude-code-main" in body  # default summarizer_model label


def test_persist_validates_schema(project):
    """Missing required field returns schema_validation_failed."""
    out = summarize_persist(
        str(project),
        "2401.12345",
        {"problem": "missing method!"},
    )
    assert out["error"] == "schema_validation_failed"
    assert "detail" in out
    assert any("method" in str(err.get("loc", [])) for err in out["detail"])


def test_persist_idempotent(project):
    """Second persist overwrites the first cleanly."""
    summarize_persist(
        str(project),
        "2401.12345",
        {"problem": "v1", "method": "v1"},
    )
    out = summarize_persist(
        str(project),
        "2401.12345",
        {"problem": "v2", "method": "v2"},
    )
    assert out["persisted"] is True
    yml = project / ".paic/library/summaries/arxiv_2401_12345.yaml"
    persisted = yaml.safe_load(yml.read_text(encoding="utf-8"))
    assert persisted["problem"] == "v2"


def test_persist_paper_not_in_library(project):
    out = summarize_persist(
        str(project),
        "9999.99999",
        {"problem": "p", "method": "m"},
    )
    assert out["error"] == "paper_not_in_library"


def test_persist_custom_summarizer_model(project):
    """Caller can override the summarizer_model label."""
    out = summarize_persist(
        str(project),
        "2401.12345",
        {"problem": "p", "method": "m"},
        summarizer_model="host:opus-4.7-via-cc",
    )
    assert out["persisted"] is True
    assert out["structured"]["summarizer_model"] == "host:opus-4.7-via-cc"


# ----------------------------------------------------------------- doctor row
def test_doctor_emits_host_row_when_summarize_is_host(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    _write_config(
        tmp_path / ".paic",
        default="anthropic",
        overrides={"summarize": "host"},
    )
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    host_rows = [c for c in checks if c.name == "host orchestration"]
    assert len(host_rows) == 1
    assert "summarize" in host_rows[0].message


def test_doctor_no_host_row_in_default_config(tmp_path, monkeypatch):
    """No host overrides → no extra row, keeps doctor output quiet."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    assert not any(c.name == "host orchestration" for c in checks)

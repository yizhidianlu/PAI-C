"""paic doctor tests — Fix 3."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    from paic.config import reset_config_cache

    reset_config_cache()
    return tmp_path


def test_doctor_default_config_reports_missing_anthropic_key(isolated_home):
    from paic.doctor import has_errors, run_all

    checks = run_all()
    assert has_errors(checks)
    err_names = [c.name for c in checks if c.severity == "err"]
    assert "ANTHROPIC_API_KEY" in err_names


def test_doctor_passes_with_key_set(isolated_home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    from paic.config import reset_config_cache
    from paic.doctor import has_errors, run_all

    reset_config_cache()
    checks = run_all()
    api_check = next(c for c in checks if c.name == "ANTHROPIC_API_KEY")
    assert api_check.severity == "ok"
    # Anthropic SDK is in dev deps so module import check should pass too.
    sdk_check = next(c for c in checks if c.name == "anthropic importable")
    assert sdk_check.severity == "ok"
    # No errors expected in this minimal config (defaults; arxiv storage may warn)
    assert not has_errors(checks)


def test_doctor_warns_on_missing_arxiv_storage(isolated_home, monkeypatch):
    """Force every candidate root to be missing so the warning is deterministic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    # Override built-in defaults so they don't accidentally find real markdown
    # on the developer's machine.
    monkeypatch.setattr(
        "paic.config.DEFAULT_ARXIV_STORAGE_CANDIDATES",
        (isolated_home / "nope_a", isolated_home / "nope_b"),
    )
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    checks = run_all()
    arxiv = [c for c in checks if c.name == "arxiv storage"]
    assert arxiv
    # All roots missing -> a final synthesis warning saying "no markdown found"
    assert any("no markdown files found" in c.message for c in arxiv)


def test_doctor_format_report_renders(isolated_home):
    from paic.doctor import format_report, run_all

    out = format_report(run_all())
    assert "summary:" in out
    assert "ANTHROPIC_API_KEY" in out


def test_doctor_claude_cli_check_only_warns_when_not_required(isolated_home, monkeypatch):
    """If user runs default api_key mode and claude isn't on PATH, that's just a warn."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setattr("shutil.which", lambda name: None)
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    checks = run_all()
    cli_check = next(c for c in checks if c.name == "claude CLI on PATH")
    # default mode is api_key, so missing CLI is at most a warn
    assert cli_check.severity in ("warn", "skip")


def test_doctor_named_profile_warns_when_key_missing(isolated_home, monkeypatch):
    """A user-defined ``providers.openai_pro`` profile triggers a per-profile row."""
    import yaml

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    home = isolated_home / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "openai_or": {
                        "kind": "openai",
                        "mode": "compatible",
                        "model": "claude-opus-4-7",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    checks = run_all()
    profile_rows = [c for c in checks if c.name == "profile: openai_or"]
    assert len(profile_rows) == 1
    assert profile_rows[0].severity == "warn"
    assert "OPENROUTER_API_KEY not set" in profile_rows[0].message


def test_doctor_named_profile_ok_when_key_set(isolated_home, monkeypatch):
    import yaml

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    home = isolated_home / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "openai_or": {
                        "kind": "openai",
                        "mode": "compatible",
                        "model": "claude-opus-4-7",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    checks = run_all()
    row = next(c for c in checks if c.name == "profile: openai_or")
    assert row.severity == "ok"
    assert "OPENROUTER_API_KEY=set" in row.message


def test_doctor_claude_cli_errors_when_agent_sdk_required(isolated_home, monkeypatch):
    """Switching default to claude_agent_sdk and not having claude on PATH -> err."""
    import yaml

    monkeypatch.setattr("shutil.which", lambda name: None)
    home = isolated_home / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {"anthropic": {"mode": "claude_agent_sdk"}},
                "routing": {"default": "anthropic"},
            }
        ),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    from paic.doctor import has_errors, run_all

    reset_config_cache()
    checks = run_all()
    cli_check = next(c for c in checks if c.name == "claude CLI on PATH")
    assert cli_check.severity == "err"
    assert has_errors(checks)


def test_doctor_flags_invalid_host_overrides(isolated_home, monkeypatch):
    """routing.overrides.<node>: host must be a node with a host handler;
    otherwise doctor must surface an err so the user fixes it before
    the graph crashes mid-run with HostOrchestrationRequired."""
    import yaml

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    home = isolated_home / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {"anthropic": {"mode": "api_key"}},
                "routing": {
                    "default": "anthropic",
                    "overrides": {
                        # valid — has a host handler
                        "summarize": "host",
                        # invalid — claim_judge calls llm.complete_json directly
                        # without an is_host_orchestrated branch (high-frequency
                        # three-way classifier; host round-trip would dominate),
                        # so routing it to host raises HostOrchestrationRequired
                        # the first time the validator runs.
                        "claim_judge": "host",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    from paic.doctor import has_errors, run_all

    reset_config_cache()
    checks = run_all()
    row = next((c for c in checks if c.name == "host overrides"), None)
    assert row is not None, "expected a 'host overrides' row when invalid hosts present"
    assert row.severity == "err"
    assert "claim_judge" in row.message
    assert has_errors(checks)


def test_doctor_quiet_when_host_overrides_all_valid(isolated_home, monkeypatch):
    """When every routing.overrides.<node>: host targets a supported node,
    the validator stays silent — only the existing 'host orchestration' info
    row appears."""
    import yaml

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    home = isolated_home / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {"anthropic": {"mode": "api_key"}},
                "routing": {
                    "default": "anthropic",
                    "overrides": {
                        "summarize": "host",
                        "draft_polish": "host",
                        "draft_compose": "host",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    checks = run_all()
    invalid_row = next((c for c in checks if c.name == "host overrides"), None)
    assert invalid_row is None, "no 'host overrides' err row when all hosts valid"


def test_doctor_panel_routing_diversity_handles_host_persona(isolated_home, monkeypatch):
    """A persona routed to ``host`` must not crash the diversity check.

    Regression: previously the check called ``router.for_node`` inside a
    ``try/except LLMUnavailable`` that did not catch ``HostOrchestrationRequired``,
    so any user with an ideate panel persona on host (a fully legitimate
    subscription-mode setup) hit a traceback instead of a doctor row.
    """
    import yaml

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    home = isolated_home / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {"anthropic": {"mode": "api_key"}},
                "routing": {
                    "default": "anthropic",
                    "overrides": {
                        "idea_score_methodology": "host",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    checks = run_all()  # must not raise HostOrchestrationRequired
    panel_row = next((c for c in checks if c.name == "panel routing"), None)
    assert panel_row is not None, "expected a 'panel routing' row"
    assert panel_row.severity in {"ok", "warn"}, (
        f"panel routing should never err on legitimate host config: {panel_row}"
    )

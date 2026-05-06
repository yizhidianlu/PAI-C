"""sdk-probe + doctor --probe tests — §14."""

from __future__ import annotations

import pytest
from click.testing import CliRunner


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    from paic.config import reset_config_cache

    reset_config_cache()
    return tmp_path


def _stub_complete_ok(self, **kw):
    from paic.llm.backends.base import LLMResponse

    return LLMResponse(text="pong", usage={"input_tokens": 3, "output_tokens": 1})


def _stub_complete_auth_fail(self, **kw):
    from paic.llm.backends.anthropic_agent_sdk import _format_unavailable
    from paic.llm.backends.base import LLMUnavailable

    raise LLMUnavailable(
        _format_unavailable("claude-agent-sdk reported authentication_failed")
    )


# ---------------------------------------------------------------- remediation
def test_remediate_auth_keywords():
    from paic.llm.backends.anthropic_agent_sdk import _remediate_for_error

    rem = _remediate_for_error("claude-agent-sdk reported authentication_failed")
    assert rem
    assert any("claude login" in step for step in rem)


def test_remediate_not_found():
    from paic.llm.backends.anthropic_agent_sdk import _remediate_for_error

    rem = _remediate_for_error("ENOENT: claude not found")
    assert any("Install Claude Code" in step for step in rem)


def test_remediate_unknown_falls_back_to_issue_link():
    from paic.llm.backends.anthropic_agent_sdk import _remediate_for_error

    rem = _remediate_for_error("some weird internal panic")
    assert rem
    assert any("github.com" in step.lower() for step in rem)


def test_format_unavailable_embeds_remediation():
    from paic.llm.backends.anthropic_agent_sdk import _format_unavailable

    msg = _format_unavailable("claude-agent-sdk reported authentication_failed")
    assert "authentication_failed" in msg
    assert "Remediation:" in msg
    assert "claude login" in msg


# ---------------------------------------------------------------- probe_sdk
def test_probe_sdk_success(monkeypatch):
    from paic.llm.backends.anthropic_agent_sdk import (
        AnthropicAgentSDKBackend,
        probe_sdk,
    )

    monkeypatch.setattr(AnthropicAgentSDKBackend, "complete", _stub_complete_ok)
    res = probe_sdk(model="claude-opus-4-7")
    assert res.ok is True
    assert res.text == "pong"
    assert res.error == ""
    assert res.remediation == []
    assert res.latency_s >= 0.0


def test_probe_sdk_auth_failure(monkeypatch):
    from paic.llm.backends.anthropic_agent_sdk import (
        AnthropicAgentSDKBackend,
        probe_sdk,
    )

    monkeypatch.setattr(AnthropicAgentSDKBackend, "complete", _stub_complete_auth_fail)
    res = probe_sdk(model="claude-opus-4-7")
    assert res.ok is False
    assert "authentication_failed" in res.error
    assert "claude login" in res.error
    assert any("claude login" in step for step in res.remediation)


# ---------------------------------------------------------------- CLI
def test_cli_sdk_probe_success(monkeypatch, isolated_home):
    from paic.cli import main
    from paic.llm.backends.anthropic_agent_sdk import AnthropicAgentSDKBackend

    monkeypatch.setattr(AnthropicAgentSDKBackend, "complete", _stub_complete_ok)
    runner = CliRunner()
    result = runner.invoke(main, ["sdk-probe"])
    assert result.exit_code == 0, result.output
    assert "[OK ]" in result.output
    assert "pong" in result.output


def test_cli_sdk_probe_failure_exits_nonzero(monkeypatch, isolated_home):
    from paic.cli import main
    from paic.llm.backends.anthropic_agent_sdk import AnthropicAgentSDKBackend

    monkeypatch.setattr(AnthropicAgentSDKBackend, "complete", _stub_complete_auth_fail)
    runner = CliRunner()
    result = runner.invoke(main, ["sdk-probe"])
    assert result.exit_code == 1
    assert "[ERR]" in result.output
    assert "authentication_failed" in result.output
    # remediation should be visible to the user
    assert "claude login" in result.output


# ---------------------------------------------------------------- doctor
def test_doctor_no_probe_by_default(isolated_home, monkeypatch):
    """`paic doctor` (no flag) must not exercise any backend."""
    import yaml

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
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
    from paic.doctor import run_all

    reset_config_cache()
    checks = run_all()  # no probe
    assert not any(c.name == "claude_agent_sdk handshake" for c in checks)


def test_doctor_probe_skipped_for_api_key_default(isolated_home, monkeypatch):
    """`--probe` is a no-op when the routed default isn't claude_agent_sdk."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    from paic.config import reset_config_cache
    from paic.doctor import run_all

    reset_config_cache()
    checks = run_all(probe_sdk=True)
    handshake = next(c for c in checks if c.name == "claude_agent_sdk handshake")
    assert handshake.severity == "skip"


def test_doctor_probe_runs_for_sdk_default(isolated_home, monkeypatch):
    """When default routing uses claude_agent_sdk and probe is on, run it."""
    import yaml

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
    from paic.llm.backends.anthropic_agent_sdk import AnthropicAgentSDKBackend

    monkeypatch.setattr(AnthropicAgentSDKBackend, "complete", _stub_complete_ok)
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all(probe_sdk=True)
    handshake = next(c for c in checks if c.name == "claude_agent_sdk handshake")
    assert handshake.severity == "ok"
    assert "pong" in handshake.message


def test_doctor_probe_failure_surfaces_first_line(isolated_home, monkeypatch):
    """A failed probe shows just the headline + points at sdk-probe for details."""
    import yaml

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
    from paic.llm.backends.anthropic_agent_sdk import AnthropicAgentSDKBackend

    monkeypatch.setattr(AnthropicAgentSDKBackend, "complete", _stub_complete_auth_fail)
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all(probe_sdk=True)
    handshake = next(c for c in checks if c.name == "claude_agent_sdk handshake")
    assert handshake.severity == "err"
    assert "authentication_failed" in handshake.message
    # multi-line remediation goes to fix:, not the headline
    assert "\n" not in handshake.message
    assert handshake.fix and "sdk-probe" in handshake.fix

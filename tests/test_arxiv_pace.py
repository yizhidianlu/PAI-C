"""arxiv pacing tests — §15."""

from __future__ import annotations

import pytest

from paic.config import (
    DEFAULT_ARXIV_BATCH_SIZE,
    DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC,
    ProviderArxivConfig,
    load_config,
    reset_config_cache,
)
from paic.mcp_server.tools.pacing import PACE_CAP_SECONDS, arxiv_pace_run


# ----------------------------------------------------- arxiv_pace_run behavior
def test_pace_default_reads_cfg(tmp_path, monkeypatch):
    """No seconds arg → use cfg.providers_arxiv.inter_batch_delay_sec."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleeps: list[float] = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep", lambda s: sleeps.append(s)
    )

    result = arxiv_pace_run()
    assert result["slept_sec"] == DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC
    assert result["default_used"] is True
    assert sleeps == [DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC]


def test_pace_custom_seconds(tmp_path, monkeypatch):
    """Explicit seconds wins over cfg default."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleeps: list[float] = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep", lambda s: sleeps.append(s)
    )

    result = arxiv_pace_run(seconds=1.5)
    assert result["slept_sec"] == 1.5
    assert result["default_used"] is False
    assert sleeps == [1.5]


def test_pace_zero_no_sleep(tmp_path, monkeypatch):
    """seconds=0 returns immediately, no time.sleep call."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleep_called = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep",
        lambda s: sleep_called.append(s),
    )
    result = arxiv_pace_run(seconds=0)
    assert result["slept_sec"] == 0
    assert sleep_called == []


def test_pace_negative_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    with pytest.raises(ValueError, match="non-negative"):
        arxiv_pace_run(seconds=-1.0)


def test_pace_over_cap_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    with pytest.raises(ValueError, match="exceeds cap"):
        arxiv_pace_run(seconds=PACE_CAP_SECONDS + 1)


def test_pace_at_cap_ok(tmp_path, monkeypatch):
    """Exactly at the cap should be accepted."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    monkeypatch.setattr("paic.mcp_server.tools.pacing.time.sleep", lambda s: None)
    result = arxiv_pace_run(seconds=PACE_CAP_SECONDS)
    assert result["slept_sec"] == PACE_CAP_SECONDS


def test_pace_skip_sleep_flag(tmp_path, monkeypatch):
    """_skip_sleep returns slept_sec but doesn't actually sleep."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    sleep_called = []
    monkeypatch.setattr(
        "paic.mcp_server.tools.pacing.time.sleep",
        lambda s: sleep_called.append(s),
    )
    result = arxiv_pace_run(seconds=2.0, _skip_sleep=True)
    assert result["slept_sec"] == 2.0
    assert sleep_called == []


# ----------------------------------------------------- config loading
def test_config_loads_default_arxiv_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_arxiv.batch_size == DEFAULT_ARXIV_BATCH_SIZE
    assert cfg.providers_arxiv.inter_batch_delay_sec == DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC


def test_config_loads_custom_arxiv_provider(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "arxiv": {
                        "batch_size": 3,
                        "inter_batch_delay_sec": 1.5,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_arxiv.batch_size == 3
    assert cfg.providers_arxiv.inter_batch_delay_sec == 1.5


def test_config_arxiv_batch_size_floor(tmp_path, monkeypatch):
    """batch_size below 1 should be clamped to 1 (you can't have <1 paper per batch)."""
    import yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({"providers": {"arxiv": {"batch_size": 0}}}),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_arxiv.batch_size == 1


def test_config_arxiv_garbage_falls_back_to_defaults(tmp_path, monkeypatch):
    """Non-numeric values shouldn't crash — fall back to defaults."""
    import yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "arxiv": {
                        "batch_size": "not-a-number",
                        "inter_batch_delay_sec": "also-bad",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_arxiv.batch_size == DEFAULT_ARXIV_BATCH_SIZE
    assert cfg.providers_arxiv.inter_batch_delay_sec == DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC


# ----------------------------------------------------- doctor integration
def test_doctor_shows_arxiv_pacing_row(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    pacing = next(c for c in checks if c.name == "arxiv pacing")
    assert pacing.severity == "ok"
    assert "batch=1" in pacing.message
    # §23: default bumped to 6.0s (was 3.0s) because download_paper fires
    # multiple HTTP requests internally; 3s consistently triggered 429.
    assert "delay=6.0s" in pacing.message
    assert "upstream_throttling=unenforced" in pacing.message


def test_doctor_arxiv_pacing_reflects_custom_config(tmp_path, monkeypatch):
    """Custom batch_size/delay should surface in doctor output."""
    import yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "arxiv": {"batch_size": 5, "inter_batch_delay_sec": 2.0}
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    from paic.doctor import run_all

    checks = run_all()
    pacing = next(c for c in checks if c.name == "arxiv pacing")
    assert "batch=5" in pacing.message
    assert "delay=2.0s" in pacing.message


# ----------------------------------------------------- ProviderArxivConfig defaults
def test_provider_arxiv_config_defaults():
    cfg = ProviderArxivConfig()
    assert cfg.batch_size == DEFAULT_ARXIV_BATCH_SIZE
    assert cfg.inter_batch_delay_sec == DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC

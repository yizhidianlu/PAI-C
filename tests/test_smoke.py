"""Smoke tests — Phase 0."""

import paic


def test_version():
    assert paic.__version__ == "0.1.0"


def test_config_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    from paic.config import load_config, reset_config_cache

    reset_config_cache()
    cfg = load_config()
    assert cfg.global_dir == (tmp_path / ".paic").resolve()
    assert cfg.default_model == "claude-opus-4-7"

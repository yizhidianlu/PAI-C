"""Semantic Scholar config + rate limiter tests — §13."""

from __future__ import annotations

import pytest

from paic.config import (
    DEFAULT_S2_BASE_URL,
    DEFAULT_S2_TIMEOUT_SEC,
    S2_RATE_LIMIT_AUTHENTICATED,
    S2_RATE_LIMIT_UNAUTHENTICATED,
    ProviderArxivConfig,
    ProviderExternalSearchConfig,
    ProviderImagesConfig,
    ProviderSemanticScholarConfig,
    load_config,
    reset_config_cache,
)
from paic.sources.semanticscholar import _RATE_LIMITER, _RateLimiter


# ---------------------------------------------------------------- rate limiter
def test_rate_limiter_first_call_is_zero_wait():
    rl = _RateLimiter()
    waited = rl.gate(1.0)
    assert waited == 0.0


def test_rate_limiter_blocks_consecutive_calls(monkeypatch):
    """Use mocked monotonic + sleep so the test stays fast and deterministic."""
    rl = _RateLimiter()
    fake_now = [100.0]
    sleeps: list[float] = []

    monkeypatch.setattr("paic.sources.semanticscholar.time.monotonic", lambda: fake_now[0])
    monkeypatch.setattr(
        "paic.sources.semanticscholar.time.sleep",
        lambda s: (sleeps.append(s), fake_now.__setitem__(0, fake_now[0] + s))[1],
    )

    rl.gate(1.0)         # first call — no wait
    fake_now[0] += 0.2   # only 0.2s passed; need 0.8s more
    rl.gate(1.0)
    assert sleeps == [pytest.approx(0.8, abs=0.01)]
    fake_now[0] += 1.5   # plenty of time passed
    rl.gate(1.0)
    assert len(sleeps) == 1  # third call doesn't sleep


def test_rate_limiter_zero_interval_is_noop():
    rl = _RateLimiter()
    assert rl.gate(0.0) == 0.0
    assert rl.gate(-1.0) == 0.0


def test_module_singleton_exists():
    """The module-level _RATE_LIMITER is shared across imports."""
    from paic.sources import semanticscholar as ss

    assert ss._RATE_LIMITER is _RATE_LIMITER


def test_rate_limiter_reset_for_tests():
    rl = _RateLimiter()
    rl._last_call_at = 999.0
    rl.reset_for_tests()
    assert rl._last_call_at == 0.0


# ---------------------------------------------------------------- config tests
def test_config_loads_default_s2_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_s2.base_url == DEFAULT_S2_BASE_URL
    assert cfg.providers_s2.timeout_sec == DEFAULT_S2_TIMEOUT_SEC
    assert cfg.providers_s2.rate_limit_per_sec is None
    assert cfg.providers_s2.api_key_env == "SEMANTIC_SCHOLAR_API_KEY"


def test_config_loads_anthropic_base_url(tmp_path, monkeypatch):
    """providers.anthropic.base_url should round-trip from yaml."""
    import yaml as _yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "providers": {
                    "anthropic": {
                        "mode": "api_key",
                        "model": "claude-opus-4-7",
                        "base_url": "https://mytoken.top",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_anthropic.base_url == "https://mytoken.top"


def test_config_anthropic_base_url_defaults_to_none(tmp_path, monkeypatch):
    """No base_url in yaml → field is None (use SDK default endpoint)."""
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_anthropic.base_url is None


def test_config_loads_custom_s2_provider(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "semantic_scholar": {
                        "base_url": "https://example.test/v1",
                        "timeout_sec": 5,
                        "rate_limit_per_sec": 0.5,
                        "api_key_env": "MY_S2_KEY",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_S2_KEY", "tok-fake")
    reset_config_cache()
    cfg = load_config()
    assert cfg.providers_s2.base_url == "https://example.test/v1"
    assert cfg.providers_s2.timeout_sec == 5.0
    assert cfg.providers_s2.rate_limit_per_sec == 0.5
    assert cfg.semantic_scholar_api_key == "tok-fake"


def test_effective_rate_limit_auto_with_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "s2k-fake")
    reset_config_cache()
    cfg = load_config()
    assert cfg.effective_s2_rate_limit() == S2_RATE_LIMIT_AUTHENTICATED


def test_effective_rate_limit_auto_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    reset_config_cache()
    cfg = load_config()
    assert cfg.effective_s2_rate_limit() == S2_RATE_LIMIT_UNAUTHENTICATED


def test_effective_rate_limit_explicit_overrides_auto(tmp_path, monkeypatch):
    """Explicit rate_limit_per_sec wins over auto-derivation."""
    cfg = type(load_config())(  # build a fresh Config manually
        global_dir=tmp_path,
        default_model="claude-opus-4-7",
        arxiv_mcp_storage_paths=(tmp_path,),
        semantic_scholar_api_key="any",
        anthropic_api_key=None,
        openai_api_key=None,
        providers_anthropic=load_config().providers_anthropic,
        providers_openai=None,
        providers_s2=ProviderSemanticScholarConfig(rate_limit_per_sec=2.5),
        providers_arxiv=ProviderArxivConfig(),
        providers_external_search=ProviderExternalSearchConfig(),
        providers_images=ProviderImagesConfig(),
        routing=load_config().routing,
        raw={},
    )
    assert cfg.effective_s2_rate_limit() == 2.5


# ---------------------------------------------------------------- integration
def test_s2_search_consults_cfg_base_url(tmp_path, monkeypatch):
    """Custom base_url in config should reach the HTTP client."""
    import yaml

    from paic.sources import semanticscholar

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    home = tmp_path / ".paic"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"providers": {"semantic_scholar": {"base_url": "https://proxy.example/v1"}}}
        ),
        encoding="utf-8",
    )
    reset_config_cache()

    class _Resp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [], "total": 0}

    class _Client:
        def __init__(self):
            self.calls = []
        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append((url, timeout))
            return _Resp()
        def close(self):
            pass

    client = _Client()
    semanticscholar.search_papers("q", client=client, _skip_rate_limit=True)
    url, timeout = client.calls[0]
    assert url.startswith("https://proxy.example/v1")
    assert timeout == DEFAULT_S2_TIMEOUT_SEC

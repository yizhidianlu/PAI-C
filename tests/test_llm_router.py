"""LLMRouter tests — Step C."""

from __future__ import annotations

import pytest

from paic.config import (
    Config,
    ProviderAnthropicConfig,
    ProviderArxivConfig,
    ProviderExternalSearchConfig,
    ProviderImagesConfig,
    ProviderOpenAIConfig,
    ProviderSemanticScholarConfig,
    RoutingConfig,
)
from paic.llm.backends import (
    AnthropicAgentSDKBackend,
    AnthropicAPIBackend,
    LLMUnavailable,
    OpenAIAPIBackend,
    OpenAICompatibleBackend,
)
from paic.llm.router import LLMRouter


def _make_config(
    *,
    routing_default: str = "anthropic",
    overrides: dict | None = None,
    fallback: str | None = None,
    anthropic_mode: str = "api_key",
    openai: ProviderOpenAIConfig | None = ProviderOpenAIConfig(),
    anthropic_key: str | None = "fake-anth",
    openai_key: str | None = "fake-oai",
    tmp_path=None,
) -> Config:
    return Config(
        global_dir=tmp_path or __import__("pathlib").Path("/tmp/paic"),
        default_model="claude-opus-4-7",
        arxiv_mcp_storage_paths=(__import__("pathlib").Path("/tmp/arxiv"),),
        semantic_scholar_api_key=None,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
        providers_anthropic=ProviderAnthropicConfig(mode=anthropic_mode),
        providers_openai=openai,
        providers_s2=ProviderSemanticScholarConfig(),
        providers_arxiv=ProviderArxivConfig(),
        providers_external_search=ProviderExternalSearchConfig(),
        providers_images=ProviderImagesConfig(),
        routing=RoutingConfig(
            default=routing_default,
            overrides=overrides or {},
            fallback=fallback,
        ),
        raw={},
    )


def test_router_default_anthropic_api_key(tmp_path):
    cfg = _make_config(tmp_path=tmp_path)
    router = LLMRouter(cfg)
    backend = router.for_node("ideate_brainstorm")
    assert isinstance(backend, AnthropicAPIBackend)
    assert backend.name == "anthropic.api_key"
    # Default config has no base_url override → backend stores None
    assert backend._base_url is None


def test_router_anthropic_api_key_with_base_url(tmp_path):
    """providers.anthropic.base_url should propagate from config to backend."""
    cfg = _make_config(tmp_path=tmp_path)
    cfg = type(cfg)(
        global_dir=cfg.global_dir,
        default_model=cfg.default_model,
        arxiv_mcp_storage_paths=cfg.arxiv_mcp_storage_paths,
        semantic_scholar_api_key=cfg.semantic_scholar_api_key,
        anthropic_api_key=cfg.anthropic_api_key,
        openai_api_key=cfg.openai_api_key,
        providers_anthropic=ProviderAnthropicConfig(base_url="https://mytoken.top"),
        providers_openai=cfg.providers_openai,
        providers_s2=cfg.providers_s2,
        providers_arxiv=cfg.providers_arxiv,
        providers_external_search=cfg.providers_external_search,
        providers_images=cfg.providers_images,
        routing=cfg.routing,
        raw=cfg.raw,
    )
    router = LLMRouter(cfg)
    backend = router.for_node("summarize")
    assert isinstance(backend, AnthropicAPIBackend)
    assert backend._base_url == "https://mytoken.top"


def test_router_default_anthropic_agent_sdk(tmp_path):
    cfg = _make_config(anthropic_mode="claude_agent_sdk", tmp_path=tmp_path)
    router = LLMRouter(cfg)
    backend = router.for_node("ideate_brainstorm")
    assert isinstance(backend, AnthropicAgentSDKBackend)
    assert backend.name == "anthropic.claude_agent_sdk"


def test_router_default_openai_api(tmp_path):
    cfg = _make_config(routing_default="openai", tmp_path=tmp_path)
    router = LLMRouter(cfg)
    backend = router.for_node("ideate_brainstorm")
    assert isinstance(backend, OpenAIAPIBackend)
    # ensure it is the official-API subclass, not compatible
    assert backend.name == "openai.api"


def test_router_default_openai_compatible(tmp_path):
    cfg = _make_config(
        routing_default="openai",
        openai=ProviderOpenAIConfig(mode="compatible", base_url="https://example.com/v1"),
        tmp_path=tmp_path,
    )
    router = LLMRouter(cfg)
    backend = router.for_node("ideate_brainstorm")
    assert isinstance(backend, OpenAICompatibleBackend)
    assert backend.name == "openai.compatible"


def test_router_node_override(tmp_path):
    cfg = _make_config(
        routing_default="anthropic",
        overrides={"ideate_brainstorm": "openai", "review_persona_methodology": "anthropic.claude_agent_sdk"},
        tmp_path=tmp_path,
    )
    router = LLMRouter(cfg)
    assert router.for_node("ideate_brainstorm").name == "openai.api"
    assert router.for_node("review_persona_methodology").name == "anthropic.claude_agent_sdk"
    # Unrelated node -> default
    assert router.for_node("review_verdict").name == "anthropic.api_key"


def test_router_unknown_node_falls_back_to_default(tmp_path):
    cfg = _make_config(tmp_path=tmp_path)
    router = LLMRouter(cfg)
    assert router.for_node("does_not_exist").name == "anthropic.api_key"


def test_router_caches_backend_instances(tmp_path):
    cfg = _make_config(tmp_path=tmp_path)
    router = LLMRouter(cfg)
    a = router.for_node("summarize")
    b = router.for_node("review_moderator")
    assert a is b  # both default -> same backend instance


def test_router_explicit_full_name(tmp_path):
    cfg = _make_config(routing_default="anthropic.claude_agent_sdk", tmp_path=tmp_path)
    router = LLMRouter(cfg)
    assert router.for_node("any").name == "anthropic.claude_agent_sdk"


def test_router_openai_alias_without_provider_raises(tmp_path):
    cfg = _make_config(routing_default="openai", openai=None, tmp_path=tmp_path)
    router = LLMRouter(cfg)
    with pytest.raises(LLMUnavailable, match="providers.openai"):
        router.for_node("anything")


def test_router_explicit_openai_compatible_without_provider_raises(tmp_path):
    cfg = _make_config(routing_default="openai.compatible", openai=None, tmp_path=tmp_path)
    router = LLMRouter(cfg)
    with pytest.raises(LLMUnavailable):
        router.for_node("anything")


def test_router_unknown_backend_raises(tmp_path):
    cfg = _make_config(routing_default="cohere", tmp_path=tmp_path)
    router = LLMRouter(cfg)
    with pytest.raises(LLMUnavailable, match="unknown provider profile"):
        router.for_node("anything")


def test_router_fallback_wraps_in_auto_fallback(tmp_path):
    from paic.llm.backends import AutoFallbackBackend

    cfg = _make_config(
        routing_default="anthropic.claude_agent_sdk",
        fallback="anthropic.api_key",
        tmp_path=tmp_path,
    )
    router = LLMRouter(cfg)
    backend = router.for_node("ideate_brainstorm")
    assert isinstance(backend, AutoFallbackBackend)
    assert backend.primary.name == "anthropic.claude_agent_sdk"
    assert backend.secondary.name == "anthropic.api_key"


def test_router_fallback_swaps_on_unavailable(tmp_path):
    from paic.llm.backends.base import BaseLLMBackend, LLMResponse, LLMUnavailable
    from paic.llm.backends.fallback import AutoFallbackBackend

    class _Failing(BaseLLMBackend):
        name = "primary.failing"
        def __init__(self):
            super().__init__(model="x")
        def complete(self, **kwargs):
            raise LLMUnavailable("no auth")

    class _Working(BaseLLMBackend):
        name = "secondary.working"
        def __init__(self):
            super().__init__(model="x")
            self.calls = 0
        def complete(self, *, system, user, max_tokens=4096, temperature=0.2, cache_user=True):
            self.calls += 1
            return LLMResponse(text='{"ok": true}', usage={})

    p = _Failing()
    s = _Working()
    fb = AutoFallbackBackend(primary=p, secondary=s)

    out1 = fb.complete(system="s", user="u")
    out2 = fb.complete(system="s", user="u")
    assert out1.text == '{"ok": true}'
    assert out2.text == '{"ok": true}'
    # primary tried once and failed, then we stuck on secondary for both calls
    assert s.calls == 2


def test_router_fallback_same_as_primary_no_wrap(tmp_path):
    from paic.llm.backends import AnthropicAPIBackend

    cfg = _make_config(
        routing_default="anthropic.api_key",
        fallback="anthropic.api_key",  # identical -> no wrapping
        tmp_path=tmp_path,
    )
    router = LLMRouter(cfg)
    backend = router.for_node("ideate_brainstorm")
    assert isinstance(backend, AnthropicAPIBackend)


def test_describe_returns_normalized(tmp_path):
    cfg = _make_config(
        overrides={"ideate_brainstorm": "openai"},
        openai=ProviderOpenAIConfig(mode="compatible", base_url="https://x"),
        tmp_path=tmp_path,
    )
    router = LLMRouter(cfg)
    out = router.describe()
    assert out["default"] == "anthropic.api_key"
    assert out["overrides"]["ideate_brainstorm"] == "openai.compatible"


# --------------------------------------------------------- named profile routing
def _config_with_extra_profiles(
    extra: dict[str, ProviderAnthropicConfig | ProviderOpenAIConfig],
    *,
    overrides: dict | None = None,
    routing_default: str = "anthropic",
    tmp_path=None,
) -> Config:
    base = _make_config(
        routing_default=routing_default,
        overrides=overrides,
        tmp_path=tmp_path,
    )
    return Config(
        global_dir=base.global_dir,
        default_model=base.default_model,
        arxiv_mcp_storage_paths=base.arxiv_mcp_storage_paths,
        semantic_scholar_api_key=base.semantic_scholar_api_key,
        anthropic_api_key=base.anthropic_api_key,
        openai_api_key=base.openai_api_key,
        providers_anthropic=base.providers_anthropic,
        providers_openai=base.providers_openai,
        providers_s2=base.providers_s2,
        providers_arxiv=base.providers_arxiv,
        providers_external_search=base.providers_external_search,
        providers_images=base.providers_images,
        routing=base.routing,
        providers_named_extra=extra,
        raw=base.raw,
    )


def test_router_named_openai_profiles_use_distinct_models(tmp_path):
    """Two named openai profiles → distinct backend instances with distinct models."""
    extra = {
        "openai_pro": ProviderOpenAIConfig(mode="api", model="gpt-5.5"),
        "openai_mini": ProviderOpenAIConfig(mode="api", model="gpt-5.4-mini"),
    }
    cfg = _config_with_extra_profiles(
        extra,
        overrides={
            "ideate_brainstorm": "openai_pro",
            "experiment_design": "openai_mini",
        },
        tmp_path=tmp_path,
    )
    router = LLMRouter(cfg)

    pro = router.for_node("ideate_brainstorm")
    mini = router.for_node("experiment_design")

    assert isinstance(pro, OpenAIAPIBackend)
    assert isinstance(mini, OpenAIAPIBackend)
    assert pro is not mini
    assert pro.model == "gpt-5.5"
    assert mini.model == "gpt-5.4-mini"
    # Backend names carry the profile name so logs/panel-diversity see them
    assert pro.name == "openai_pro.api"
    assert mini.name == "openai_mini.api"


def test_router_named_profile_with_compatible_mode(tmp_path):
    """A named openai profile pointing at OpenRouter via mode=compatible."""
    extra = {
        "openai_or": ProviderOpenAIConfig(
            mode="compatible",
            model="claude-opus-4-7",
            base_url="https://openrouter.ai/api/v1",
        ),
    }
    cfg = _config_with_extra_profiles(
        extra,
        overrides={"review_persona_methodology": "openai_or"},
        tmp_path=tmp_path,
    )
    router = LLMRouter(cfg)
    backend = router.for_node("review_persona_methodology")
    assert isinstance(backend, OpenAICompatibleBackend)
    assert backend.model == "claude-opus-4-7"
    assert backend._base_url == "https://openrouter.ai/api/v1"
    assert backend.name == "openai_or.compatible"


def test_router_named_profile_explicit_mode_override(tmp_path):
    """``profile.mode`` form forces a specific mode regardless of profile default."""
    extra = {
        "anth_pro": ProviderAnthropicConfig(mode="api_key", model="claude-opus-4-7"),
    }
    cfg = _config_with_extra_profiles(
        extra,
        overrides={"summarize": "anth_pro.claude_agent_sdk"},
        tmp_path=tmp_path,
    )
    router = LLMRouter(cfg)
    backend = router.for_node("summarize")
    assert isinstance(backend, AnthropicAgentSDKBackend)
    assert backend.name == "anth_pro.claude_agent_sdk"


def test_router_named_profile_invalid_mode_raises(tmp_path):
    """Routing to ``openai_pro.api_key`` (anthropic mode on openai profile) errors."""
    extra = {
        "openai_pro": ProviderOpenAIConfig(mode="api", model="gpt-5.5"),
    }
    cfg = _config_with_extra_profiles(
        extra, routing_default="openai_pro.api_key", tmp_path=tmp_path
    )
    router = LLMRouter(cfg)
    with pytest.raises(LLMUnavailable, match="kind=openai"):
        router.for_node("any")


def test_router_named_profile_unknown_raises(tmp_path):
    cfg = _make_config(routing_default="openai_pro", tmp_path=tmp_path)
    router = LLMRouter(cfg)
    with pytest.raises(LLMUnavailable, match="unknown provider profile"):
        router.for_node("any")


def test_describe_lists_profiles(tmp_path):
    extra = {
        "openai_pro": ProviderOpenAIConfig(mode="api", model="gpt-5.5"),
    }
    cfg = _config_with_extra_profiles(
        extra,
        overrides={"ideate_brainstorm": "openai_pro"},
        tmp_path=tmp_path,
    )
    info = LLMRouter(cfg).describe()
    assert "openai_pro" in info["profiles"]
    assert info["overrides"]["ideate_brainstorm"] == "openai_pro.api"


def test_load_named_providers_from_yaml(tmp_path, monkeypatch):
    """End-to-end yaml → Config → router for named profiles."""
    import yaml as _yaml

    from paic.config import load_config, reset_config_cache

    monkeypatch.setenv("PAIC_HOME", str(tmp_path / ".paic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-oai")

    home = tmp_path / ".paic"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "providers": {
                    "openai": {"mode": "api", "model": "gpt-5.4-mini"},
                    "openai_pro": {
                        "kind": "openai",
                        "mode": "api",
                        "model": "gpt-5.5",
                    },
                    "openai_or": {
                        "kind": "openai",
                        "mode": "compatible",
                        "model": "claude-opus-4-7",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                    },
                },
                "routing": {
                    "default": "openai",
                    "overrides": {
                        "ideate_brainstorm": "openai_pro",
                        "experiment_design": "openai",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert "openai_pro" in cfg.providers_named_extra
    assert "openai_or" in cfg.providers_named_extra
    assert cfg.providers_named_extra["openai_pro"].model == "gpt-5.5"

    router = LLMRouter(cfg)
    pro = router.for_node("ideate_brainstorm")
    mini = router.for_node("experiment_design")
    assert pro.model == "gpt-5.5"
    assert mini.model == "gpt-5.4-mini"
    assert pro.name == "openai_pro.api"

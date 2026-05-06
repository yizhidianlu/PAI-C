"""LLMRouter — pick a backend per node label.

Naming: backend names follow ``profile`` or ``profile.mode``:
- ``anthropic``                  → use the reserved ``anthropic`` profile's mode
- ``anthropic.api_key``          → force the API-key Anthropic backend
- ``anthropic.claude_agent_sdk`` → force the Claude Agent SDK backend
- ``openai``                     → use the reserved ``openai`` profile's mode
- ``openai.api``                 → force the official OpenAI API backend
- ``openai.compatible``          → force the OpenAI-compatible (base_url) backend
- ``<custom_profile>``           → use any user-defined ``providers.<name>``
                                   profile (with ``kind: anthropic|openai``)
- ``<custom_profile>.<mode>``    → same, forcing a specific mode
- ``host``                       → sentinel: PAI-C does NOT make any LLM call
                                   for this node. Caller (the MCP tool) must
                                   handle ``HostOrchestrationRequired`` by
                                   returning a host-orchestration directive
                                   for the Skill layer to fulfill via the
                                   main Claude Code conversation.

Named profiles let one provider expose multiple model/key/base_url
combinations under different names, e.g. ``openai_pro`` (gpt-5.5) +
``openai_mini`` (gpt-5.4-mini) sharing OPENAI_API_KEY but routed to
different nodes.

Backends are built lazily and cached on the router instance — instantiating an
SDK client is cheap but not free, and a single Claude Code session may invoke
the same backend hundreds of times across a multi-round review.
"""

from __future__ import annotations

from typing import Any

from paic.config import Config, ProviderAnthropicConfig, ProviderOpenAIConfig
from paic.llm.backends import (
    AnthropicAgentSDKBackend,
    AnthropicAPIBackend,
    AutoFallbackBackend,
    BaseLLMBackend,
    HostOrchestrationRequired,
    LLMUnavailable,
    OpenAIAPIBackend,
    OpenAICompatibleBackend,
)

HOST_BACKEND = "host"


def _normalize(backend_name: str, cfg: Config) -> str:
    """Resolve a short alias to a fully-qualified ``<profile>.<mode>`` name.

    ``host`` is passed through verbatim — it's a routing sentinel, not an
    aliased profile/mode pair. Otherwise the input is parsed as either:

    - bare profile name (``anthropic``, ``openai_pro``) → uses that profile's
      configured ``mode``
    - ``<profile>.<mode>`` → uses ``<profile>``'s settings but forces ``<mode>``
      (must be a valid mode for that profile's kind)
    """
    if backend_name == HOST_BACKEND:
        return HOST_BACKEND

    profile_name, _, forced_mode = backend_name.partition(".")
    profiles = cfg.providers_named
    if profile_name not in profiles:
        # Special case: ``openai`` shorthand without configured provider gets a
        # clearer error message than the generic "unknown profile".
        if profile_name == "openai":
            raise LLMUnavailable(
                "routing references 'openai' but providers.openai is not "
                "configured in ~/.paic/config.yaml"
            )
        raise LLMUnavailable(
            f"unknown provider profile '{profile_name}'. "
            f"Known profiles: {sorted(profiles)}"
        )
    profile = profiles[profile_name]
    mode = forced_mode or profile.mode
    return f"{profile_name}.{mode}"


class LLMRouter:
    """Map node labels to concrete backend instances."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._backends: dict[str, BaseLLMBackend] = {}

    def for_node(self, node: str) -> BaseLLMBackend:
        """Return the backend that should handle ``node``.

        Resolution order: ``cfg.routing.overrides[node]`` → ``cfg.routing.default``.
        Unknown node labels silently fall through to the default — this is the
        right behaviour because we use Literal types only at the call sites,
        not at the config layer (users may add custom labels later).

        If ``cfg.routing.fallback`` is set and the resolved primary differs from
        it, the returned backend is an :class:`AutoFallbackBackend` that will
        swap to the fallback on the first ``LLMUnavailable``.

        Raises:
            HostOrchestrationRequired: if ``node`` resolves to ``host``. The
                caller must catch this and switch to host-orchestration logic.
                ``host`` does not auto-fall-back via ``routing.fallback`` —
                the caller decides whether to retry with a different node tag.
        """
        backend_name = self.cfg.routing.overrides.get(node, self.cfg.routing.default)
        full_name = _normalize(backend_name, self.cfg)

        if full_name == HOST_BACKEND:
            raise HostOrchestrationRequired(node)

        cache_key = full_name
        fallback_name: str | None = None
        if self.cfg.routing.fallback:
            fb_full = _normalize(self.cfg.routing.fallback, self.cfg)
            if fb_full != full_name and fb_full != HOST_BACKEND:
                fallback_name = fb_full
                cache_key = f"{full_name}|{fb_full}"

        if cache_key in self._backends:
            return self._backends[cache_key]

        primary = self._build(full_name)
        if fallback_name is not None:
            secondary = self._build(fallback_name)
            wrapped = AutoFallbackBackend(primary=primary, secondary=secondary)
            self._backends[cache_key] = wrapped
            return wrapped

        self._backends[cache_key] = primary
        return primary

    def is_host_orchestrated(self, node: str) -> bool:
        """Cheap check: does ``node`` route to ``host``?

        Lets callers branch on host mode without catching an exception, which
        keeps the happy-path readable. Internally still uses ``_normalize`` so
        the resolution is consistent with ``for_node``.
        """
        backend_name = self.cfg.routing.overrides.get(node, self.cfg.routing.default)
        try:
            return _normalize(backend_name, self.cfg) == HOST_BACKEND
        except LLMUnavailable:
            return False

    # ------------------------------------------------------------------ build
    def _build(self, full_name: str) -> BaseLLMBackend:
        """Instantiate the backend for a normalized ``<profile>.<mode>`` name.

        Profile lookup is by name in :pyattr:`Config.providers_named`; the
        backend class is selected by the profile's dataclass type, so a
        custom ``openai_pro`` profile and the reserved ``openai`` profile
        both flow through the same OpenAI backend code paths — they just
        carry different ``model`` / ``api_key`` / ``base_url`` values and a
        distinct ``backend.name`` for logs and panel-diversity checks.
        """
        profile_name, _, mode = full_name.partition(".")
        profile = self.cfg.providers_named.get(profile_name)
        if profile is None:
            raise LLMUnavailable(
                f"unknown provider profile '{profile_name}' in routing target "
                f"'{full_name}'. Known: {sorted(self.cfg.providers_named)}"
            )
        api_key = self.cfg.profile_api_key(profile_name)

        if isinstance(profile, ProviderAnthropicConfig):
            if mode == "api_key":
                return AnthropicAPIBackend(
                    model=profile.model,
                    api_key=api_key,
                    base_url=profile.base_url,
                    name=full_name,
                )
            if mode == "claude_agent_sdk":
                return AnthropicAgentSDKBackend(model=profile.model, name=full_name)
            raise LLMUnavailable(
                f"profile '{profile_name}' (kind=anthropic) does not support "
                f"mode '{mode}'. Valid: api_key | claude_agent_sdk"
            )

        if isinstance(profile, ProviderOpenAIConfig):
            if mode == "api":
                return OpenAIAPIBackend(
                    model=profile.model,
                    api_key=api_key,
                    base_url=profile.base_url,
                    name=full_name,
                )
            if mode == "compatible":
                return OpenAICompatibleBackend(
                    model=profile.model,
                    api_key=api_key,
                    base_url=profile.base_url,
                    name=full_name,
                )
            raise LLMUnavailable(
                f"profile '{profile_name}' (kind=openai) does not support "
                f"mode '{mode}'. Valid: api | compatible"
            )

        raise LLMUnavailable(
            f"profile '{profile_name}' has an unsupported kind {type(profile).__name__}"
        )

    # ------------------------------------------------------------------ debug
    def describe(self) -> dict[str, Any]:
        """Cheap snapshot used by /paic-status to show current routing.

        Adds ``host_nodes`` (list of node labels routed to ``host``) so callers
        can advertise which nodes bypass PAI-C-internal LLM calls. Both
        ``overrides`` map values and the ``default`` may be ``host``.
        Unresolvable backend names (e.g. references to a missing ``openai``
        provider) are passed through as-is so the snapshot still renders
        instead of raising.
        """

        def _safe_normalize(name: str) -> str:
            try:
                return _normalize(name, self.cfg)
            except LLMUnavailable:
                return name

        out: dict[str, Any] = {
            "default": _safe_normalize(self.cfg.routing.default),
            "overrides": {
                node: _safe_normalize(name)
                for node, name in self.cfg.routing.overrides.items()
            },
            "profiles": sorted(self.cfg.providers_named),
        }
        host_nodes = [
            node for node, name in self.cfg.routing.overrides.items() if name == HOST_BACKEND
        ]
        if self.cfg.routing.default == HOST_BACKEND:
            host_nodes.append("<default>")
        if host_nodes:
            out["host_nodes"] = host_nodes
        if self.cfg.routing.fallback:
            out["fallback"] = _safe_normalize(self.cfg.routing.fallback)
        return out

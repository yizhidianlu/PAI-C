"""Auto-fallback backend.

Wraps two ``BaseLLMBackend`` instances; routes calls to ``primary`` and
swaps to ``secondary`` on the first :class:`LLMUnavailable`. After a
swap we stick with ``secondary`` for the lifetime of the wrapper — no
need to re-probe a missing CLI on every node.

Used by :class:`paic.llm.router.LLMRouter` when ``cfg.routing.fallback``
is set. The most common combo is::

    primary   = AnthropicAgentSDKBackend (no API key, uses local CLI login)
    secondary = AnthropicAPIBackend      (ANTHROPIC_API_KEY)

so a missing or broken Claude Code login gracefully drops back to the
paid API path instead of failing the whole graph mid-run.
"""

from __future__ import annotations

from paic.llm.backends.base import BaseLLMBackend, LLMResponse, LLMUnavailable
from paic.logging import get_logger

LOG = get_logger("paic.llm.fallback")


class AutoFallbackBackend(BaseLLMBackend):
    def __init__(self, *, primary: BaseLLMBackend, secondary: BaseLLMBackend):
        super().__init__(model=primary.model)
        self.primary = primary
        self.secondary = secondary
        self._switched = False
        self.name = f"fallback({primary.name}->{secondary.name})"

    def _active(self) -> BaseLLMBackend:
        return self.secondary if self._switched else self.primary

    def supports_native_json(self) -> bool:
        return self._active().supports_native_json()

    def supports_prompt_caching(self) -> bool:
        return self._active().supports_prompt_caching()

    def complete(self, **kwargs) -> LLMResponse:
        if not self._switched:
            try:
                return self.primary.complete(**kwargs)
            except LLMUnavailable as exc:
                LOG.warning(
                    "primary backend %s unavailable (%s); falling back to %s for the rest of this run",
                    self.primary.name,
                    exc,
                    self.secondary.name,
                )
                self._switched = True
        return self.secondary.complete(**kwargs)

    def complete_json(self, **kwargs):
        if not self._switched:
            try:
                return self.primary.complete_json(**kwargs)
            except LLMUnavailable as exc:
                LOG.warning(
                    "primary backend %s unavailable (%s); falling back to %s for the rest of this run",
                    self.primary.name,
                    exc,
                    self.secondary.name,
                )
                self._switched = True
        return self.secondary.complete_json(**kwargs)

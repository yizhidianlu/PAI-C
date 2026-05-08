"""LLMClient — router-aware facade.

Holds an :class:`LLMRouter` internally and dispatches by ``node`` label.
Old call sites that don't pass ``node`` still work: they hit the router's
default backend, which (for the unconfigured / legacy yaml case) is
``anthropic.api_key`` — preserving v1 behaviour.

The optional ``backend`` constructor arg lets tests inject a stub backend
that bypasses the router entirely; test fixtures from earlier phases that
mock ``complete_json`` directly on a passed-in client object are also
unaffected.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from paic.config import Config, load_config
from paic.llm.backends import LLMResponse, LLMUnavailable  # re-exported
from paic.llm.backends.base import BaseLLMBackend
from paic.llm.router import LLMRouter

__all__ = [
    "LLMClient",
    "LLMResponse",
    "LLMUnavailable",
    "get_default_client",
    "reset_default_client",
]

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Backward-compatible facade in front of :class:`LLMRouter`."""

    def __init__(
        self,
        *,
        cfg: Config | None = None,
        model: str | None = None,
        backend: BaseLLMBackend | None = None,
    ):
        self.cfg = cfg or load_config()
        self.model = model or self.cfg.default_model
        self._fixed_backend = backend  # if set, ignore routing entirely
        self._router = None if backend is not None else LLMRouter(self.cfg)

    def _backend_for(self, node: str | None) -> BaseLLMBackend:
        if self._fixed_backend is not None:
            return self._fixed_backend
        assert self._router is not None
        return self._router.for_node(node or "default")

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        cache_user: bool = True,
        node: str | None = None,
    ) -> LLMResponse:
        return self._backend_for(node).complete(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_user=cache_user,
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        node: str | None = None,
    ) -> T:
        return self._backend_for(node).complete_json(
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def complete_isolated(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 4096,
        temperature: float = 0.2,
        node: str | None = None,
    ) -> T:
        """Call the backend with the strongest available conversation isolation
        (ARS-fusion P1-1 generator-evaluator contract).

        Each call MUST start with a fresh conversation context — no
        carry-over of prior system / user / assistant turns. For
        backends that already construct a stateless request per call
        (Anthropic Messages, OpenAI ChatCompletions), this is identical
        to ``complete_json``. For backends that maintain conversation
        state internally (e.g. Claude Agent SDK), this is the hook to
        force a fresh agent / session per phase.

        V1.0 ships with the simple delegation; future patches will add
        per-backend ``new_conversation()`` semantics where needed.
        """
        # Today: identical wire effect to complete_json because
        # Anthropic / OpenAI backends are stateless per call. The
        # function exists so the 4-call compose pipeline has a single
        # contract surface to upgrade when SDK-state backends ship a
        # fresh-conversation knob.
        return self._backend_for(node).complete_json(
            system=system,
            user=user,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    @property
    def router(self) -> LLMRouter | None:
        """The internal router, or ``None`` if a fixed backend was supplied."""
        return self._router


_DEFAULT: LLMClient | None = None


def get_default_client() -> LLMClient:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LLMClient()
    return _DEFAULT


def reset_default_client() -> None:
    """Drop the cached default client — handy after config changes / in tests."""
    global _DEFAULT
    _DEFAULT = None

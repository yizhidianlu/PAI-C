"""Backend abstract base class + shared JSON helpers.

Concrete backends override :meth:`complete`. The default :meth:`complete_json`
runs ``complete`` once, parses the JSON, and on parse/schema failure does one
corrective retry — the same logic that lived inline in the v1 ``LLMClient``.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from paic.logging import get_logger

LOG = get_logger("paic.llm")

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Raised when a backend can't run (missing key, SDK import, auth, …)."""


class HostOrchestrationRequired(RuntimeError):
    """Raised when a node is routed to ``host`` (sentinel, not a real backend).

    PAI-C MCP must NOT make an LLM call for this node — instead, the calling
    tool should return a ``mode: "host_orchestration"`` response that tells the
    Skill layer to have Claude Code's main conversation generate the
    structured output and persist it via a dedicated ``*_persist`` tool.

    See plan §16 for design + the ``paic_summarize_persist`` precedent.
    """

    def __init__(self, node: str):
        self.node = node
        super().__init__(
            f"node '{node}' is routed to 'host' — caller must do host orchestration"
        )


@dataclass
class LLMResponse:
    text: str
    usage: dict[str, Any]


_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str) -> str:
    """Pull the first JSON object out of a model response.

    Tolerates ```json fences and bare object form. Used by every backend's
    fallback path even when the underlying SDK supports a native JSON mode.
    """
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in model response: {text[:200]}")
    return text[start : end + 1]


JSON_DIRECTIVE = (
    "\n\nReturn ONLY a valid JSON object that conforms to the requested "
    "schema. Do not include any prose, markdown, or commentary outside "
    "the JSON. Wrap the JSON in a ```json ... ``` fence."
)


class BaseLLMBackend(ABC):
    """Uniform completion + structured-JSON interface."""

    name: str = "base"  # e.g. "anthropic.api_key" or "<profile>.<mode>"

    def __init__(self, *, model: str, name: str | None = None):
        self.model = model
        if name is not None:
            # Instance attribute shadows the class default — used by the router
            # to label a backend with its profile name (e.g. "openai_pro.api")
            # so logs / fallback / panel-diversity all see the profile.
            self.name = name

    # ------------------------------------------------------------------ caps
    def supports_native_json(self) -> bool:
        """If True, the backend honours a structured-output / JSON-mode hint."""
        return False

    def supports_prompt_caching(self) -> bool:
        """If True, the backend honours Anthropic-style ``cache_control`` markers."""
        return False

    # ------------------------------------------------------------------ raw
    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        cache_user: bool = True,
    ) -> LLMResponse:
        """Single-shot completion. Subclasses override this."""

    # ------------------------------------------------------------------ json
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> T:
        """Run :meth:`complete` and validate the response against ``schema``.

        Adds a strict JSON-only directive to the system prompt, retries once
        on parse/validation failure with a corrective follow-up.
        """
        system_full = system + JSON_DIRECTIVE
        response = self.complete(
            system=system_full, user=user, max_tokens=max_tokens, temperature=temperature
        )
        try:
            payload = json.loads(extract_json(response.text))
            return schema.model_validate(payload)
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            LOG.warning(
                "JSON parse/validation failed on %s (%s); retrying once",
                self.name,
                exc,
            )
            retry_user = (
                user
                + "\n\nYour previous response did not parse as the requested schema. "
                "Return STRICTLY a JSON object inside ```json ... ``` and nothing else."
            )
            retry = self.complete(
                system=system_full,
                user=retry_user,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            payload = json.loads(extract_json(retry.text))
            return schema.model_validate(payload)

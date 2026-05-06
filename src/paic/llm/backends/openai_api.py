"""OpenAI API key backend.

Uses the official ``openai`` SDK against ``api.openai.com``. Honours JSON
mode (``response_format={"type": "json_object"}``) when ``complete_json`` is
called — this is more reliable than the prompt-only fence we use for
backends without native structured output.

Subclassed by :class:`OpenAICompatibleBackend` to point the same client at
OpenRouter / Azure / vLLM / Ollama / etc. via a custom base URL.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from paic.llm.backends.base import (
    JSON_DIRECTIVE,
    BaseLLMBackend,
    LLMResponse,
    LLMUnavailable,
    extract_json,
)
from paic.logging import get_logger

LOG = get_logger("paic.llm.openai")

T = TypeVar("T", bound=BaseModel)


class OpenAIAPIBackend(BaseLLMBackend):
    name = "openai.api"

    # Subclasses (compatible) override.
    base_url: str | None = None

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str | None = None,
        name: str | None = None,
    ):
        super().__init__(model=model, name=name)
        self._api_key = api_key
        self._base_url = base_url or self.base_url
        self._client = None

    def supports_native_json(self) -> bool:
        # Most OpenAI-compatible servers honour `response_format`; we still keep
        # the prompt fence as a fallback inside complete_json.
        return True

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("openai SDK is not installed") from exc
        if not self._api_key:
            raise LLMUnavailable(
                f"API key is not set ({self.name} backend requires one)"
            )
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = OpenAI(**kwargs)
        return self._client

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        cache_user: bool = True,  # noqa: ARG002  (caching is automatic for OpenAI)
    ) -> LLMResponse:
        client = self._ensure_client()
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice and choice.message else ""
        usage: dict[str, Any] = {}
        if response.usage is not None:
            usage = {
                "input_tokens": getattr(response.usage, "prompt_tokens", 0),
                "output_tokens": getattr(response.usage, "completion_tokens", 0),
                "cache_read_input_tokens": getattr(
                    getattr(response.usage, "prompt_tokens_details", None),
                    "cached_tokens",
                    0,
                ) or 0,
            }
        return LLMResponse(text=text or "", usage=usage)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> T:
        """Override the base helper to use OpenAI's JSON mode when available."""
        system_full = system + JSON_DIRECTIVE
        client = self._ensure_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_full},
                    {"role": "user", "content": user},
                ],
            )
            text = (response.choices[0].message.content or "") if response.choices else ""
        except Exception as exc:
            # Some compatible servers reject `response_format`. Retry without it.
            LOG.warning(
                "%s rejected response_format=json_object (%s); retrying without",
                self.name,
                exc,
            )
            base_resp = self.complete(
                system=system_full, user=user, max_tokens=max_tokens, temperature=temperature
            )
            text = base_resp.text

        try:
            payload = json.loads(extract_json(text))
            return schema.model_validate(payload)
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            LOG.warning(
                "JSON parse failed on %s (%s); retrying once with corrective prompt",
                self.name,
                exc,
            )
            retry_user = (
                user
                + "\n\nYour previous response did not parse as the requested schema. "
                "Return STRICTLY a JSON object inside ```json ... ``` and nothing else."
            )
            retry = self.complete(
                system=system_full, user=retry_user, max_tokens=max_tokens, temperature=0.0
            )
            payload = json.loads(extract_json(retry.text))
            return schema.model_validate(payload)

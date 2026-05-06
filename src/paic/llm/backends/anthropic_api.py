"""Anthropic API key backend.

The historical v1 ``LLMClient`` lives here. Adds ``cache_control: ephemeral``
to the system block (and to the first user block when it's long enough to
benefit), which is the cheapest way to amortize repeated persona prompts.
"""

from __future__ import annotations

from typing import Any

from paic.llm.backends.base import BaseLLMBackend, LLMResponse, LLMUnavailable


class AnthropicAPIBackend(BaseLLMBackend):
    name = "anthropic.api_key"

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
        self._base_url = base_url
        self._client = None  # lazy

    def supports_prompt_caching(self) -> bool:
        return True

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("anthropic SDK is not installed") from exc
        if not self._api_key:
            raise LLMUnavailable(
                "ANTHROPIC_API_KEY is not set (anthropic.api_key backend requires one)"
            )
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = Anthropic(**kwargs)
        return self._client

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        cache_user: bool = True,
    ) -> LLMResponse:
        client = self._ensure_client()
        system_blocks = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        user_blocks: list[dict[str, Any]] = [{"type": "text", "text": user}]
        if cache_user and len(user) > 1024:
            user_blocks[0]["cache_control"] = {"type": "ephemeral"}

        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_blocks,
            messages=[{"role": "user", "content": user_blocks}],
        )
        text_parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        text = "".join(text_parts)

        usage: dict[str, Any] = {}
        if response.usage is not None:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": getattr(
                    response.usage, "cache_read_input_tokens", 0
                ),
                "cache_creation_input_tokens": getattr(
                    response.usage, "cache_creation_input_tokens", 0
                ),
            }
        return LLMResponse(text=text, usage=usage)

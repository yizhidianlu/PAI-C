"""OpenAI-compatible backend (custom base_url).

Same wire protocol as the official OpenAI API; just point the client at a
different host. Works against OpenRouter, Azure OpenAI, vLLM, Ollama,
LM Studio, llama.cpp's ``server``, and any other proxy that speaks the
OpenAI Chat Completions schema.

Subclasses :class:`OpenAIAPIBackend` so all the JSON-mode handling and
error retry comes for free; the only difference is that ``base_url`` is
mandatory here.
"""

from __future__ import annotations

from paic.llm.backends.base import LLMUnavailable
from paic.llm.backends.openai_api import OpenAIAPIBackend


class OpenAICompatibleBackend(OpenAIAPIBackend):
    name = "openai.compatible"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str | None,
        name: str | None = None,
    ):
        if not base_url:
            raise LLMUnavailable(
                "openai.compatible backend requires base_url "
                "(e.g. https://openrouter.ai/api/v1 or http://localhost:11434/v1)"
            )
        super().__init__(model=model, api_key=api_key, base_url=base_url, name=name)

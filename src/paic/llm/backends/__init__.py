"""LLM backend implementations.

Each backend wraps a single provider/auth combination behind a uniform
``BaseLLMBackend`` interface. The router (``paic.llm.router``) picks one
per node label.
"""

from paic.llm.backends.anthropic_agent_sdk import AnthropicAgentSDKBackend
from paic.llm.backends.anthropic_api import AnthropicAPIBackend
from paic.llm.backends.base import (
    BaseLLMBackend,
    HostOrchestrationRequired,
    LLMResponse,
    LLMUnavailable,
)
from paic.llm.backends.fallback import AutoFallbackBackend
from paic.llm.backends.openai_api import OpenAIAPIBackend
from paic.llm.backends.openai_compatible import OpenAICompatibleBackend

__all__ = [
    "AnthropicAPIBackend",
    "AnthropicAgentSDKBackend",
    "AutoFallbackBackend",
    "BaseLLMBackend",
    "HostOrchestrationRequired",
    "LLMResponse",
    "LLMUnavailable",
    "OpenAIAPIBackend",
    "OpenAICompatibleBackend",
]

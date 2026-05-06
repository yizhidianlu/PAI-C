"""Backend unit tests — Step B.

Each backend is exercised against a mocked SDK client. We never make a real
HTTP / subprocess call from CI.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from paic.llm.backends import (
    AnthropicAgentSDKBackend,
    AnthropicAPIBackend,
    LLMUnavailable,
    OpenAIAPIBackend,
    OpenAICompatibleBackend,
)


class _Sample(BaseModel):
    value: int
    label: str


# ===================================================================
#  AnthropicAPIBackend
# ===================================================================


class _StubAnthropicMessages:
    def __init__(self, text_response: str):
        self.text_response = text_response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class _TextBlock:
            type = "text"
            text = self.text_response

        class _Usage:
            input_tokens = 10
            output_tokens = 20
            cache_read_input_tokens = 5
            cache_creation_input_tokens = 0

        class _Resp:
            content = [_TextBlock()]
            usage = _Usage()

        # bind self.text_response into the nested class
        _TextBlock.text = self.text_response
        return _Resp()


class _StubAnthropic:
    def __init__(self, text_response: str):
        self.messages = _StubAnthropicMessages(text_response)


def test_anthropic_api_complete_emits_cache_control():
    backend = AnthropicAPIBackend(model="claude-opus-4-7", api_key="fake-key")
    backend._client = _StubAnthropic("hello")

    resp = backend.complete(system="sys", user="u" * 2000)
    assert resp.text == "hello"
    assert resp.usage["input_tokens"] == 10

    call = backend._client.messages.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Long user (>1024 chars) should also be cache-marked
    assert call["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_api_complete_short_user_skips_cache():
    backend = AnthropicAPIBackend(model="claude-opus-4-7", api_key="fake-key")
    backend._client = _StubAnthropic("ok")

    backend.complete(system="sys", user="short")
    call = backend._client.messages.calls[0]
    assert "cache_control" not in call["messages"][0]["content"][0]


def test_anthropic_api_complete_json_parses():
    backend = AnthropicAPIBackend(model="claude-opus-4-7", api_key="fake-key")
    backend._client = _StubAnthropic('```json\n{"value": 7, "label": "x"}\n```')

    out = backend.complete_json(system="s", user="u", schema=_Sample)
    assert out.value == 7
    assert out.label == "x"


def test_anthropic_api_missing_key_raises():
    backend = AnthropicAPIBackend(model="claude-opus-4-7", api_key=None)
    with pytest.raises(LLMUnavailable):
        backend.complete(system="s", user="u")


def test_anthropic_api_passes_base_url_to_sdk(monkeypatch):
    """When base_url is set, the Anthropic SDK constructor should receive it."""
    captured: dict[str, Any] = {}

    def _fake_anthropic(**kwargs):
        captured.update(kwargs)
        return _StubAnthropic("ok")

    # Patch the import target inside _ensure_client
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic)

    backend = AnthropicAPIBackend(
        model="claude-opus-4-7",
        api_key="fake-key",
        base_url="https://mytoken.top",
    )
    backend._ensure_client()
    assert captured["api_key"] == "fake-key"
    assert captured["base_url"] == "https://mytoken.top"


def test_anthropic_api_omits_base_url_when_unset(monkeypatch):
    """Default behavior (no base_url) should not pass the kwarg to the SDK."""
    captured: dict[str, Any] = {}

    def _fake_anthropic(**kwargs):
        captured.update(kwargs)
        return _StubAnthropic("ok")

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic)

    backend = AnthropicAPIBackend(model="claude-opus-4-7", api_key="fake-key")
    backend._ensure_client()
    assert captured["api_key"] == "fake-key"
    assert "base_url" not in captured


# ===================================================================
#  AnthropicAgentSDKBackend
# ===================================================================


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, text, error=None):
        self.content = [_FakeTextBlock(text)]
        self.usage = {"input_tokens": 11, "output_tokens": 22}
        self.error = error


class _FakeSDK:
    def __init__(self, *messages):
        self._messages = list(messages)
        self.last_options = None
        self.last_prompt = None

        class _AssistantMessage:
            pass

        class _Options:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        self.AssistantMessage = _FakeAssistantMessage  # type: ignore[assignment]
        self.ClaudeAgentOptions = _Options
        self.CLINotFoundError = type("CLINotFoundError", (Exception,), {})
        self.CLIConnectionError = type("CLIConnectionError", (Exception,), {})

    async def query(self, *, prompt, options):
        self.last_prompt = prompt
        self.last_options = options
        for m in self._messages:
            yield m


def test_anthropic_agent_sdk_complete_returns_text():
    sdk = _FakeSDK(_FakeAssistantMessage("agent says hi"))
    backend = AnthropicAgentSDKBackend(model="claude-opus-4-7")
    backend._sdk = sdk
    backend._options_cls = sdk.ClaudeAgentOptions

    resp = backend.complete(system="sys", user="u")
    assert resp.text == "agent says hi"
    assert resp.usage["input_tokens"] == 11

    # tool execution must be locked down
    assert sdk.last_options.kwargs["allowed_tools"] == []
    assert sdk.last_options.kwargs["permission_mode"] == "dontAsk"
    assert sdk.last_options.kwargs["system_prompt"].startswith("sys")


def test_anthropic_agent_sdk_complete_json_parses():
    sdk = _FakeSDK(_FakeAssistantMessage('{"value": 9, "label": "abc"}'))
    backend = AnthropicAgentSDKBackend(model="claude-opus-4-7")
    backend._sdk = sdk
    backend._options_cls = sdk.ClaudeAgentOptions

    out = backend.complete_json(system="s", user="u", schema=_Sample)
    assert out.value == 9
    assert out.label == "abc"


def test_anthropic_agent_sdk_authentication_error_raises():
    sdk = _FakeSDK(_FakeAssistantMessage("", error="authentication_failed"))
    backend = AnthropicAgentSDKBackend(model="claude-opus-4-7")
    backend._sdk = sdk
    backend._options_cls = sdk.ClaudeAgentOptions

    with pytest.raises(LLMUnavailable, match="authentication_failed"):
        backend.complete(system="s", user="u")


# ===================================================================
#  OpenAIAPIBackend / OpenAICompatibleBackend
# ===================================================================


class _StubOpenAIChoice:
    def __init__(self, content):
        class _Msg:
            pass

        self.message = _Msg()
        self.message.content = content


class _StubOpenAIUsage:
    prompt_tokens = 30
    completion_tokens = 40

    class _Details:
        cached_tokens = 12

    prompt_tokens_details = _Details()


class _StubOpenAIResponse:
    def __init__(self, content):
        self.choices = [_StubOpenAIChoice(content)]
        self.usage = _StubOpenAIUsage()


class _StubOpenAICompletions:
    def __init__(self, content, fail_response_format: bool = False):
        self.content = content
        self.fail_response_format = fail_response_format
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_response_format and "response_format" in kwargs:
            raise RuntimeError("response_format not supported by this server")
        return _StubOpenAIResponse(self.content)


class _StubOpenAIChat:
    def __init__(self, completions):
        self.completions = completions


class _StubOpenAIClient:
    def __init__(self, content, fail_response_format=False):
        self.chat = _StubOpenAIChat(_StubOpenAICompletions(content, fail_response_format))


def test_openai_api_complete():
    backend = OpenAIAPIBackend(model="gpt-4o", api_key="sk-fake")
    backend._client = _StubOpenAIClient("hello world")

    resp = backend.complete(system="sys", user="u")
    assert resp.text == "hello world"
    assert resp.usage["input_tokens"] == 30
    assert resp.usage["output_tokens"] == 40
    assert resp.usage["cache_read_input_tokens"] == 12


def test_openai_api_complete_json_uses_native_json_mode():
    backend = OpenAIAPIBackend(model="gpt-4o", api_key="sk-fake")
    backend._client = _StubOpenAIClient('{"value": 1, "label": "n"}')

    out = backend.complete_json(system="s", user="u", schema=_Sample)
    assert out.value == 1
    call = backend._client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}


def test_openai_api_complete_json_fallback_when_response_format_rejected():
    backend = OpenAIAPIBackend(model="gpt-4o", api_key="sk-fake")
    backend._client = _StubOpenAIClient(
        '{"value": 2, "label": "ok"}', fail_response_format=True
    )

    out = backend.complete_json(system="s", user="u", schema=_Sample)
    assert out.value == 2
    # First call had response_format and failed; backend retried via plain complete()
    assert len(backend._client.chat.completions.calls) >= 2


def test_openai_api_missing_key_raises():
    backend = OpenAIAPIBackend(model="gpt-4o", api_key=None)
    with pytest.raises(LLMUnavailable):
        backend.complete(system="s", user="u")


def test_openai_compatible_requires_base_url():
    with pytest.raises(LLMUnavailable, match="base_url"):
        OpenAICompatibleBackend(model="x", api_key="k", base_url=None)


def test_openai_compatible_threads_base_url():
    backend = OpenAICompatibleBackend(
        model="gpt-4o-mini",
        api_key="sk-or",
        base_url="https://openrouter.ai/api/v1",
    )
    assert backend._base_url == "https://openrouter.ai/api/v1"
    assert backend.name == "openai.compatible"

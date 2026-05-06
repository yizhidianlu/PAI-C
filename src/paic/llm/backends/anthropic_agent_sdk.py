"""Anthropic backend that piggy-backs on a local Claude Code login.

Uses the ``claude-agent-sdk`` package, which spawns the user's already-logged-in
``claude`` CLI to run completions. Effect: cost lands on the user's Claude Code
subscription (or whichever plan that CLI is authenticated against), not on a
PAI-C-specific API key.

Trade-offs vs. ``AnthropicAPIBackend``:
- No prompt-caching markers (the agent SDK abstracts that away)
- Slightly slower (CLI subprocess overhead per call, ~200–500 ms)
- Tool execution disabled — we force ``allowed_tools=[]`` and
  ``permission_mode='dontAsk'`` so the SDK can't accidentally run anything
  while answering a structured-text prompt

Failure-mode UX (§14): the most common cause of ``LLMUnavailable`` from this
backend is the OAuth token in the user's ``claude`` CLI keychain having
expired. PAI-C MCP runs as a child of Claude Code, so the user can't see the
remediation steps unless we embed them directly in the exception message —
which is what :func:`_format_unavailable` does. The ``paic sdk-probe`` CLI
exposes :func:`probe_sdk` for explicit out-of-band diagnosis.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from paic.llm.backends.base import BaseLLMBackend, LLMResponse, LLMUnavailable


# ----------------------------------------------------------------- remediation
def _remediate_for_error(error_text: str) -> list[str]:
    """Map SDK error keywords to concrete next steps.

    Order matters — first match wins. Keep keywords lowercase. The list is
    embedded verbatim into ``LLMUnavailable.message`` so users see it both in
    MCP tool output and in ``paic sdk-probe``.
    """
    lower = error_text.lower()
    if any(k in lower for k in ("authentication_failed", "unauthorized", "401", "not authenticated", "invalid_token")):
        return [
            "Run `claude login` in a fresh terminal — your OAuth token may be expired.",
            "Verify with `claude --print 'hi'` outside MCP that the CLI works.",
            "Fully quit and reopen Claude Code so the MCP child re-spawns.",
            "If still failing, run `uv run paic sdk-probe` for the raw error.",
        ]
    if any(k in lower for k in ("not found", "enoent", "no such file", "command not found")):
        return [
            "Install Claude Code (https://claude.com/claude-code) so `claude` is on PATH.",
            "Or switch to api_key mode in ~/.paic/config.yaml: providers.anthropic.mode=api_key.",
        ]
    if any(k in lower for k in ("connection refused", "econnrefused", "timed out", "timeout", "network")):
        return [
            "Network issue talking to Claude. Run `claude doctor` to self-check.",
            "Check VPN / firewall / proxy settings.",
        ]
    return [
        "Unrecognized SDK error. Run `uv run paic sdk-probe` for the raw output,",
        "then open an issue at https://github.com/yizhidianlu/PAI-C/issues.",
    ]


def _format_unavailable(error_message: str) -> str:
    """Build the multi-line ``LLMUnavailable`` message with remediation hints."""
    lines = [error_message]
    if not error_message.endswith("."):
        lines[-1] += "."
    rem = _remediate_for_error(error_message)
    if rem:
        lines.append("Remediation:")
        for i, step in enumerate(rem, 1):
            lines.append(f"  {i}. {step}")
    return "\n".join(lines)


class AnthropicAgentSDKBackend(BaseLLMBackend):
    name = "anthropic.claude_agent_sdk"

    def __init__(self, *, model: str, name: str | None = None):
        super().__init__(model=model, name=name)
        self._sdk = None
        self._options_cls = None

    # No "ephemeral cache_control" knob to flip; the CLI handles caching itself.
    def supports_prompt_caching(self) -> bool:
        return False

    def _ensure_sdk(self):
        if self._sdk is not None:
            return self._sdk
        try:
            import claude_agent_sdk
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable("claude-agent-sdk is not installed") from exc
        self._sdk = claude_agent_sdk
        self._options_cls = claude_agent_sdk.ClaudeAgentOptions
        return self._sdk

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        cache_user: bool = True,
    ) -> LLMResponse:
        sdk = self._ensure_sdk()
        options = self._options_cls(
            system_prompt=system,
            model=self.model,
            allowed_tools=[],          # text-only — no tool execution
            permission_mode="dontAsk",  # deny anything not pre-approved
            max_turns=1,                # one assistant reply, no follow-ups
        )

        async def _run() -> tuple[str, dict[str, Any]]:
            text_parts: list[str] = []
            usage: dict[str, Any] = {}
            try:
                async for message in sdk.query(prompt=user, options=options):
                    if isinstance(message, sdk.AssistantMessage):
                        for block in message.content:
                            text = getattr(block, "text", None)
                            if text:
                                text_parts.append(text)
                        if message.usage:
                            usage = dict(message.usage)
                        if message.error:
                            raise LLMUnavailable(
                                _format_unavailable(
                                    f"claude-agent-sdk reported {message.error}"
                                )
                            )
                    # ResultMessage / system messages: ignore for now.
            except sdk.CLINotFoundError as exc:  # pragma: no cover
                raise LLMUnavailable(
                    _format_unavailable(
                        "claude CLI not found on PATH"
                    )
                ) from exc
            except sdk.CLIConnectionError as exc:  # pragma: no cover
                raise LLMUnavailable(
                    _format_unavailable(
                        "claude CLI is not authenticated"
                    )
                ) from exc
            return "".join(text_parts), usage

        try:
            text, usage = asyncio.run(_run())
        except RuntimeError as exc:
            # If we're already inside an event loop (rare for our sync graph nodes),
            # fall back to creating a fresh loop in a worker thread.
            if "asyncio.run() cannot be called" in str(exc):
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    text, usage = pool.submit(asyncio.run, _run()).result()
            else:  # pragma: no cover
                raise

        return LLMResponse(text=text, usage=usage)


# --------------------------------------------------------------------- probe
@dataclass
class ProbeResult:
    """Outcome of a single ``claude_agent_sdk`` round-trip.

    ``error`` is the full ``LLMUnavailable`` message when ``ok=False`` —
    which already includes remediation lines via :func:`_format_unavailable`.
    Callers (CLI, doctor) just print it as-is.
    """

    ok: bool
    text: str = ""
    error: str = ""
    latency_s: float = 0.0
    remediation: list[str] = field(default_factory=list)


def probe_sdk(*, model: str) -> ProbeResult:
    """Run a one-shot 'ping → pong' through ``AnthropicAgentSDKBackend``.

    Returns a :class:`ProbeResult` instead of raising — callers format their
    own output. On success, ``text`` holds the assistant's reply (typically
    "pong"); on failure, ``error`` holds the formatted ``LLMUnavailable``
    message and ``remediation`` is its keyword-derived steps (already embedded
    in ``error`` too, exposed separately for structured callers).

    No timeout enforcement — the SDK either returns fast or fails fast. If a
    real hang ever occurs, ctrl-c is the right escape.
    """
    backend = AnthropicAgentSDKBackend(model=model)
    start = time.monotonic()
    try:
        resp = backend.complete(
            system="Reply with exactly the single word: pong",
            user="ping",
            max_tokens=10,
            temperature=0.0,
            cache_user=False,
        )
        return ProbeResult(
            ok=True,
            text=resp.text.strip(),
            latency_s=time.monotonic() - start,
        )
    except LLMUnavailable as exc:
        msg = str(exc)
        return ProbeResult(
            ok=False,
            error=msg,
            latency_s=time.monotonic() - start,
            remediation=_remediate_for_error(msg),
        )

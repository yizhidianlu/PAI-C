"""``paic doctor`` — startup diagnostic.

Walks every preflight check that can plausibly fail in real deployments
(missing API keys, SDK import errors, ``claude`` CLI not on PATH, arxiv MCP
storage path mismatch, broken routing config) and prints a one-line OK / WARN
/ ERR report per check.

Goal: surface the same gotchas users hit at runtime — but at install time, in
~1 second, with concrete remediation hints. Inspired by ``brew doctor``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from paic.config import DOMAIN_PRESETS, Config, load_config
from paic.sources.arxiv_bridge import iter_storage_roots

Severity = Literal["ok", "warn", "err", "skip"]


@dataclass
class Check:
    name: str
    severity: Severity
    message: str
    fix: str | None = None


def _check_anthropic_api_key(cfg: Config) -> Check:
    if cfg.anthropic_api_key:
        return Check("ANTHROPIC_API_KEY", "ok", "set")
    if cfg.providers_anthropic.mode == "api_key":
        return Check(
            "ANTHROPIC_API_KEY",
            "err",
            "not set, but providers.anthropic.mode is api_key",
            fix="Set ANTHROPIC_API_KEY in your environment, or switch mode to claude_agent_sdk.",
        )
    return Check("ANTHROPIC_API_KEY", "warn", "not set (only needed for api_key mode)")


def _check_openai_api_key(cfg: Config) -> Check:
    if cfg.providers_openai is None:
        return Check("OPENAI_API_KEY", "skip", "openai provider not configured")
    if cfg.openai_api_key:
        return Check("OPENAI_API_KEY", "ok", "set")
    return Check(
        "OPENAI_API_KEY",
        "err",
        "not set, but providers.openai is configured",
        fix="Set OPENAI_API_KEY, or remove the providers.openai block from ~/.paic/config.yaml.",
    )


def _check_images_backend(cfg: Config) -> Check | None:
    """Surface the image-generation backend status when ``/paic-figure`` is opted in.

    Returns ``None`` when ``providers.images.enabled=false`` so the doctor
    output stays quiet for the common case (image gen is opt-in by design).
    """
    images = cfg.providers_images
    if not images.enabled:
        return None
    api_key = os.environ.get(images.api_key_env)
    parts = [f"model={images.model}"]
    if images.base_url:
        parts.append(f"base_url={images.base_url}")
    parts.append(f"size={images.size}")
    parts.append(f"quality={images.quality}")
    if not api_key:
        return Check(
            "images backend",
            "err",
            f"{' | '.join(parts)} | {images.api_key_env}=NOT SET",
            fix=(
                f"Set {images.api_key_env} in your environment, then restart "
                "Claude Code. Or set providers.images.enabled=false to disable "
                "the /paic-figure pipeline."
            ),
        )
    parts.append(f"{images.api_key_env}=set")
    if images.model == "dall-e-3":
        return Check(
            "images backend",
            "warn",
            " | ".join(parts) + " — dall-e-3 cannot edit/variant",
            fix=(
                "Switch providers.images.model to gpt-image-1 (recommended) "
                "or dall-e-2 if you want /paic-figure edit/variant to work."
            ),
        )
    return Check("images backend", "ok", " | ".join(parts))


def _check_named_profiles(cfg: Config) -> list[Check]:
    """Per-profile API key + reachability check for user-defined named profiles.

    The reserved ``anthropic`` / ``openai`` profiles are already covered by the
    dedicated checks above. Here we surface every extra profile (e.g.
    ``openai_pro``) so users immediately see whether the env var configured
    via ``api_key_env`` is actually populated. Without this, a typo in a
    profile name silently routes calls to a backend that fails at runtime.
    """
    if not cfg.providers_named_extra:
        return []
    rows: list[Check] = []
    for name, profile in cfg.providers_named_extra.items():
        kind = "anthropic" if "anthropic" in type(profile).__name__.lower() else "openai"
        key = cfg.profile_api_key(name)
        label = f"profile: {name}"
        info = f"kind={kind} model={profile.model} mode={profile.mode}"
        if key:
            rows.append(Check(label, "ok", f"{info} | {profile.api_key_env}=set"))
        else:
            rows.append(
                Check(
                    label,
                    "warn",
                    f"{info} | {profile.api_key_env} not set",
                    fix=(
                        f"Set {profile.api_key_env} in your environment, "
                        "then restart Claude Code. (Skip if no node routes to "
                        f"'{name}'.)"
                    ),
                )
            )
    return rows


def _check_module(module: str, *, severity_if_missing: Severity = "err") -> Check:
    spec = importlib.util.find_spec(module)
    if spec is not None:
        return Check(f"{module} importable", "ok", "found")
    return Check(
        f"{module} importable",
        severity_if_missing,
        "not installed",
        fix=f"Run `uv sync` to install {module}.",
    )


def _check_claude_cli(cfg: Config) -> Check:
    needed = (
        cfg.providers_anthropic.mode == "claude_agent_sdk"
        or cfg.routing.default.startswith("anthropic.claude_agent_sdk")
        or any(
            v.startswith("anthropic.claude_agent_sdk")
            for v in cfg.routing.overrides.values()
        )
        or cfg.routing.fallback == "anthropic.claude_agent_sdk"
    )
    path = shutil.which("claude")
    if path:
        return Check("claude CLI on PATH", "ok", path)
    if needed:
        return Check(
            "claude CLI on PATH",
            "err",
            "not found, but anthropic.claude_agent_sdk is referenced",
            fix="Install Claude Code (https://claude.com/claude-code), then run `claude login`. "
            "Or switch the relevant routing entries to anthropic.api_key.",
        )
    return Check(
        "claude CLI on PATH", "warn", "not found (only needed for claude_agent_sdk mode)"
    )


def _check_arxiv_storage(cfg: Config) -> Iterable[Check]:
    roots = iter_storage_roots(cfg=cfg)
    any_hit = any(exists and count > 0 for _, exists, count in roots)
    for root, exists, count in roots:
        if exists:
            sev: Severity = "ok" if count > 0 else "warn"
            msg = f"{root}  ({count} markdown file{'s' if count != 1 else ''})"
            yield Check("arxiv storage", sev, msg)
        else:
            yield Check("arxiv storage", "skip", f"{root}  (not present)")
    if not any_hit:
        yield Check(
            "arxiv storage",
            "warn",
            "no markdown files found in any candidate root",
            fix="Run an arxiv MCP download once (e.g. via /paic-search + /paic-ingest), "
            "or add the actual storage path to ~/.paic/config.yaml under arxiv_mcp_storage_paths.",
        )


def _check_arxiv_pacing(cfg: Config) -> Check:
    """Show the active arxiv pacing knobs.

    Skill-layer voluntary throttling — surfaces here so users can confirm the
    config knobs they set actually loaded. ``upstream_throttling=unenforced``
    is a deliberate hint: ``blazickjp/arxiv-mcp-server``'s download/read paths
    do not rate-limit, so ``paic_arxiv_pace`` is the only safeguard.
    """
    pacing = cfg.providers_arxiv
    parts = [
        f"batch={pacing.batch_size}",
        f"delay={pacing.inter_batch_delay_sec:.1f}s",
        "upstream_throttling=unenforced",
    ]
    return Check("arxiv pacing", "ok", " | ".join(parts))


def _check_overleaf(cfg: Config) -> Check:
    """Surface the Overleaf bidirectional sync status.

    Reports enabled/disabled and whether the configured Dropbox target_root
    actually exists locally. Missing target_root just means the user hasn't
    connected Dropbox in Overleaf yet — the SKILL silently skips when this
    is the case, so this is a hint not an error.
    """
    overleaf = cfg.overleaf
    if not overleaf.enabled:
        return Check(
            "overleaf sync",
            "skip",
            "disabled (set overleaf.enabled=true in ~/.paic/config.yaml to enable "
            "bidirectional sync via Dropbox)",
        )
    target_root = overleaf.target_root.expanduser()
    if not target_root.exists():
        return Check(
            "overleaf sync",
            "warn",
            (
                f"enabled=true | target_root={target_root} (missing) — open Overleaf "
                "→ Account Settings → Linked Accounts → connect Dropbox; the folder "
                "is auto-created on first connect"
            ),
        )
    return Check(
        "overleaf sync",
        "ok",
        (
            f"enabled=true | target_root={target_root} | "
            f"strategy={overleaf.conflict_strategy} | "
            f"prompt_on_delete={overleaf.prompt_on_delete}"
        ),
    )


def _check_external_search(cfg: Config) -> Check:
    """Surface the multi-platform search status — §18.

    Reports enabled/disabled, active preset, platform count, and any
    optional-key warnings. When disabled (the default), emits a single info
    line so users can see how to flip the switch.
    """
    ext = cfg.providers_external_search
    if not ext.enabled:
        return Check(
            "external search",
            "skip",
            "disabled (set providers.external_search.enabled=true in ~/.paic/config.yaml "
            "to enable multi-platform via paper-search-mcp)",
        )
    parts = [
        "enabled=true",
        f"preset={ext.default_preset}",
        f"max_results={ext.max_results_per_platform}",
        "upstream_throttling=per-platform",
    ]
    return Check("external search", "ok", " | ".join(parts))


def _check_external_search_credentials(cfg: Config) -> list[Check]:
    """Surface gated paper-search-mcp credentials when external search is on.

    Without these warnings users only learn at ``/paic-search`` or
    ``/paic-ingest`` time that IEEE/ACM/Unpaywall are silently skipped.
    Reports per-platform status for everything in the default preset that
    gates on an env key, plus Unpaywall (always hot — used by
    ``download_with_fallback`` for DOI-only papers).
    """
    ext = cfg.providers_external_search
    if not ext.enabled:
        return []

    from paic.mcp_server.tools.strategy import _PLATFORM_KEY_REQUIREMENTS

    preset_name = ext.default_preset
    preset_platforms = set(DOMAIN_PRESETS.get(preset_name, []))
    # Always check unpaywall — download_with_fallback uses it regardless of preset.
    targets = sorted(set(_PLATFORM_KEY_REQUIREMENTS) & (preset_platforms | {"unpaywall"}))

    rows: list[Check] = []
    for platform in targets:
        env_names, _hint = _PLATFORM_KEY_REQUIREMENTS[platform]
        if any(os.environ.get(name) for name in env_names):
            rows.append(Check(f"credential: {platform}", "ok", f"{env_names[0]} set"))
        else:
            in_preset = platform in preset_platforms
            reason = (
                f"present in '{preset_name}' preset — search will silently skip"
                if in_preset
                else "used by download_with_fallback for DOI-only papers"
            )
            rows.append(
                Check(
                    f"credential: {platform}",
                    "warn",
                    f"{env_names[0]} not set — {reason}",
                    fix=(
                        f"set {env_names[0]} (or {env_names[1]}) in your environment, "
                        "then restart Claude Code"
                    ),
                )
            )
    return rows


def _check_semantic_scholar(cfg: Config) -> Check:
    from paic.config import DEFAULT_S2_BASE_URL

    rate = cfg.effective_s2_rate_limit()
    parts: list[str] = [f"rate={rate:.2f} req/s"]
    if cfg.semantic_scholar_api_key:
        parts.append("key=set")
    else:
        parts.append("key=anonymous")
    if cfg.providers_s2.base_url != DEFAULT_S2_BASE_URL:
        parts.append(f"base_url={cfg.providers_s2.base_url}")
    return Check("semantic scholar", "ok", " | ".join(parts))


def _check_routing(cfg: Config) -> Check:
    from paic.llm.backends import LLMUnavailable
    from paic.llm.router import LLMRouter

    router = LLMRouter(cfg)
    try:
        info = router.describe()
    except LLMUnavailable as exc:
        return Check("routing", "err", str(exc), fix="Fix ~/.paic/config.yaml routing block.")
    parts = [f"default={info['default']}"]
    if info.get("fallback"):
        parts.append(f"fallback={info['fallback']}")
    if info["overrides"]:
        parts.append(f"overrides={len(info['overrides'])}")
    if info.get("host_nodes"):
        parts.append(f"host_nodes={','.join(info['host_nodes'])}")
    return Check("routing", "ok", ", ".join(parts))


def _check_panel_routing_diversity(cfg: Config) -> Check:
    """§26.6.2 R87 — warn when ≥3/4 ideate panel personas resolve to the same backend.

    Same-backend personas produce highly-correlated scores (all 4 noise, no
    extra signal). Encouraging users to route at least one persona (typically
    reviewer2) to a distinct backend is the cheapest way to get genuine
    diversity.
    """
    from paic.llm.backends import LLMUnavailable
    from paic.llm.router import LLMRouter

    personas = ("methodology", "novelty", "impact", "reviewer2")
    router = LLMRouter(cfg)
    backends: list[str] = []
    for p in personas:
        try:
            backend = router.for_node(f"idea_score_{p}")
            backends.append(getattr(backend, "name", "unknown"))
        except LLMUnavailable:
            backends.append("unavailable")

    # Count distinct backends
    distinct = set(backends)
    if len(distinct) >= 3:
        return Check(
            "panel routing", "ok",
            f"diverse: {len(distinct)} distinct backends across 4 personas",
        )
    # 1 or 2 distinct → warn (not err — this is a quality concern, not a breakage)
    most = max(distinct, key=lambda b: backends.count(b))
    same_count = backends.count(most)
    if same_count >= 3:
        return Check(
            "panel routing", "warn",
            f"{same_count}/4 personas resolve to {most} — scoring may be redundant",
            fix=(
                "Add an entry like `idea_score_reviewer2: openai` (or any "
                "distinct backend) under routing.overrides in ~/.paic/config.yaml "
                "so at least one persona disagrees structurally with the rest. "
                "See docs/configuration.md → ideate panel diversification."
            ),
        )
    return Check(
        "panel routing", "ok",
        f"{len(distinct)} distinct backends across 4 personas",
    )


def _check_host_orchestration(cfg: Config) -> Check | None:
    """Optional info row that surfaces which nodes bypass internal LLM calls.

    Returns ``None`` when no node routes to ``host`` — keeps the doctor output
    quiet for the common case. When host nodes exist, emits a separate row so
    users can confirm the subscription/orchestration setup is wired correctly
    without parsing the routing line.
    """
    from paic.llm.router import LLMRouter

    router = LLMRouter(cfg)
    info = router.describe()
    nodes = info.get("host_nodes") or []
    if not nodes:
        return None
    return Check(
        "host orchestration",
        "ok",
        f"enabled for: {', '.join(nodes)} (no PAI-C-internal LLM call)",
    )


def _check_sdk_handshake(cfg: Config) -> Check:
    """Live ``claude_agent_sdk`` round-trip — only run when ``--probe`` is on.

    Skips itself when default routing isn't using ``claude_agent_sdk``: there's
    no reason to spend 1-3s exercising a backend that won't be hit.
    """
    from paic.llm.backends.anthropic_agent_sdk import probe_sdk
    from paic.llm.router import _normalize

    try:
        default_full = _normalize(cfg.routing.default, cfg)
    except Exception:  # pragma: no cover - routing already covered by other check
        return Check(
            "claude_agent_sdk handshake", "skip", "routing default cannot be resolved"
        )
    if "claude_agent_sdk" not in default_full:
        return Check(
            "claude_agent_sdk handshake",
            "skip",
            f"default routing is {default_full}; nothing to probe",
        )

    result = probe_sdk(model=cfg.providers_anthropic.model)
    if result.ok:
        return Check(
            "claude_agent_sdk handshake",
            "ok",
            f"reply={result.text!r} (latency={result.latency_s:.2f}s)",
        )
    first_line = result.error.splitlines()[0] if result.error else "unknown error"
    return Check(
        "claude_agent_sdk handshake",
        "err",
        first_line,
        fix="Run `uv run paic sdk-probe` to see the full error and remediation steps.",
    )


def run_all(*, probe_sdk: bool = False) -> list[Check]:
    cfg = load_config()
    checks: list[Check] = []
    checks.append(_check_anthropic_api_key(cfg))
    checks.append(_check_openai_api_key(cfg))
    checks.extend(_check_named_profiles(cfg))
    checks.append(_check_module("anthropic"))
    checks.append(_check_module("claude_agent_sdk", severity_if_missing="warn"))
    checks.append(_check_module("openai", severity_if_missing="warn"))
    checks.append(_check_claude_cli(cfg))
    checks.extend(_check_arxiv_storage(cfg))
    checks.append(_check_arxiv_pacing(cfg))
    checks.append(_check_external_search(cfg))
    checks.extend(_check_external_search_credentials(cfg))
    checks.append(_check_overleaf(cfg))
    checks.append(_check_semantic_scholar(cfg))
    checks.append(_check_routing(cfg))
    checks.append(_check_panel_routing_diversity(cfg))
    host_row = _check_host_orchestration(cfg)
    if host_row is not None:
        checks.append(host_row)
    images_row = _check_images_backend(cfg)
    if images_row is not None:
        checks.append(images_row)
    if probe_sdk:
        checks.append(_check_sdk_handshake(cfg))
    return checks


def format_report(checks: list[Check]) -> str:
    badge = {"ok": "[OK ]", "warn": "[WARN]", "err": "[ERR ]", "skip": "[--- ]"}
    lines: list[str] = []
    for c in checks:
        line = f"{badge[c.severity]}  {c.name:30s}  {c.message}"
        lines.append(line)
        if c.fix and c.severity in ("err", "warn"):
            lines.append(f"        fix: {c.fix}")
    err_count = sum(1 for c in checks if c.severity == "err")
    warn_count = sum(1 for c in checks if c.severity == "warn")
    lines.append("")
    lines.append(f"summary: {err_count} error(s), {warn_count} warning(s)")
    return "\n".join(lines)


def has_errors(checks: list[Check]) -> bool:
    return any(c.severity == "err" for c in checks)

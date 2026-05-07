"""Configuration loader.

Resolves the global PAI-C directory (`~/.paic/`), reads `config.yaml`, and
exposes typed accessors for runtime settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

DEFAULT_GLOBAL_DIR = Path.home() / ".paic"
# Probe these paths in order; arxiv MCP versions disagree on default storage.
DEFAULT_ARXIV_STORAGE_CANDIDATES: tuple[Path, ...] = (
    Path.home() / "Documents" / "arxiv-papers",
    Path.home() / ".arxiv-mcp" / "papers",
)
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
DEFAULT_OPENAI_MODEL = "gpt-4o"

# Semantic Scholar API defaults
DEFAULT_S2_BASE_URL = "https://api.semanticscholar.org/graph/v1"
DEFAULT_S2_TIMEOUT_SEC = 30.0
# Auto-derived rate limits (req/sec):
#   - Authenticated: 1.0 (per S2 docs); we cap at 0.95 to leave a safety margin.
#   - Unauthenticated: ~0.33 (S2 unauth ≈ 100 req / 5 min ≈ 0.33/s).
S2_RATE_LIMIT_AUTHENTICATED = 0.95
S2_RATE_LIMIT_UNAUTHENTICATED = 0.33

# arxiv MCP pacing defaults — Skill-layer voluntary throttling. arxiv.org's
# published recommendation is 1 req per 3 seconds. Default to strict serial
# (batch=1) and 6s pace because mcp__arxiv__download_paper internally fires
# multiple HTTP requests per call (HTML probe + PDF) — a 3s pace produces
# ~0.67 req/s, double the limit and consistently 429s on batch ingest.
# 6s gives an effective ~0.33 req/s (≈ 2 HTTP per 6s), aligned with the
# published limit. See plan §23 for benchmarks.
# Users on stable / private networks who never see 429 can loosen via
# providers.arxiv.inter_batch_delay_sec in config.yaml.
DEFAULT_ARXIV_BATCH_SIZE = 1
DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC = 6.0

# External-search (paper-search-mcp) defaults — §18. Per-platform delays are
# Skill-layer voluntary throttling (same model as arxiv pacing): the Skill
# prompt invokes ``paic_search_pace(platform=...)`` between successive calls
# to ``mcp__paper_search__search_<platform>``. Defaults are conservative — set
# to stay safely under each upstream's published rate limit.
DEFAULT_EXTERNAL_SEARCH_PRESET = "interdisciplinary"
DEFAULT_EXTERNAL_SEARCH_MAX_RESULTS = 10
_DEFAULT_PLATFORM_PACING: dict[str, float] = {
    "pubmed": 0.4,         # NCBI: 3 req/s anon, 10 req/s with key — keep margin
    "biorxiv": 1.0,
    "medrxiv": 1.0,
    "europepmc": 0.4,
    "pmc": 0.4,
    "crossref": 0.05,      # Polite pool ~50 req/s
    "openalex": 0.1,       # Polite pool ~10 req/s
    "core": 1.5,           # 10 req/min anon; harder cap than most
    "dblp": 1.0,
    "doaj": 1.0,
    "openaire": 1.0,
    "zenodo": 0.5,
    "hal": 1.0,
    "ssrn": 5.0,           # Cloudflare bot detection — go slow
    "google_scholar": 5.0, # Bot detection — slow + recommend proxy
    "iacr": 1.0,
    "citeseerx": 1.0,
    "base": 1.0,
    "unpaywall": 1.0,      # 10万 calls/day soft cap
    "ieee": 1.0,           # Per-contract; depends on IEEE_API_KEY tier
    "acm": 1.0,
}

# Overleaf sync defaults — bidirectional sync between
# ``<project>/.paic/drafts/`` and ``<target_root>/<project_subdir>/``,
# typically the user's Dropbox + Overleaf-linked folder. ignore_patterns
# are fnmatch globs against file basenames; matched files are skipped
# both ways and never enter the baseline manifest.
DEFAULT_OVERLEAF_TARGET_ROOT = Path.home() / "Dropbox" / "Apps" / "Overleaf"
_DEFAULT_OVERLEAF_IGNORE_PATTERNS: tuple[str, ...] = (
    "*.pdf",
    "*.aux",
    "*.log",
    "*.out",
    "*.bbl",
    "*.blg",
    "*.synctex.gz",
    "*.toc",
    "*.nav",
    "*.snm",
    "*.bak.*",
    ".DS_Store",
    "Thumbs.db",
)

# Domain presets — used by /paic-init to seed the project's platform list.
# arxiv + semantic_scholar are NOT included here (they always go through the
# existing arxiv MCP / paic_s2_search path); workspace_init prepends them
# automatically when external_search is enabled.
DOMAIN_PRESETS: dict[str, list[str]] = {
    "cs_ml": ["arxiv", "semantic_scholar", "openalex", "dblp", "acm"],
    "biomed": [
        "pubmed", "biorxiv", "medrxiv", "europepmc", "pmc", "semantic_scholar",
    ],
    "physics_math": ["arxiv", "semantic_scholar", "openalex"],
    "econ_social": ["ssrn", "openalex", "semantic_scholar", "crossref"],
    "engineering": ["arxiv", "openalex", "crossref", "ieee", "acm"],
    "interdisciplinary": [
        "arxiv", "semantic_scholar", "openalex", "crossref", "doaj",
    ],
}


@dataclass(frozen=True)
class ProviderAnthropicConfig:
    """Anthropic provider settings.

    ``mode='api_key'``  → use ``anthropic`` SDK with ``ANTHROPIC_API_KEY``
    ``mode='claude_agent_sdk'`` → use ``claude-agent-sdk`` (relies on local Claude Code login)

    ``base_url`` lets ``mode='api_key'`` point at an Anthropic-compatible relay
    (e.g. ``https://mytoken.top``) — leave ``None`` for the official endpoint.
    Ignored in ``claude_agent_sdk`` mode (the SDK spawns the local CLI which
    has its own routing).
    """
    mode: Literal["api_key", "claude_agent_sdk"] = "api_key"
    model: str = DEFAULT_ANTHROPIC_MODEL
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str | None = None


@dataclass(frozen=True)
class ProviderOpenAIConfig:
    """OpenAI provider settings.

    ``mode='api'``        → official ``api.openai.com``
    ``mode='compatible'`` → any OpenAI-compatible server (OpenRouter / Azure / Ollama / vLLM)
    """
    mode: Literal["api", "compatible"] = "api"
    model: str = DEFAULT_OPENAI_MODEL
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None  # required for compatible mode


@dataclass(frozen=True)
class ProviderArxivConfig:
    """arxiv MCP pacing settings — Skill-layer voluntary throttling.

    PAI-C's Python process is *not* in the arxiv-call path (Skills tell Claude
    to invoke ``mcp__arxiv__*`` directly), so we cannot enforce rate limiting
    server-side the way we do for Semantic Scholar. Instead, the
    ``paic_arxiv_pace`` MCP tool reads ``inter_batch_delay_sec`` here and sleeps
    that long when called between successive arxiv operations.

    Defaults align with arxiv.org's published recommendation (1 req per 3s).
    """
    batch_size: int = DEFAULT_ARXIV_BATCH_SIZE
    inter_batch_delay_sec: float = DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC


@dataclass(frozen=True)
class ProviderExternalSearchConfig:
    """Multi-platform paper search via paper-search-mcp — §18.

    Opt-in (``enabled=False`` by default). When enabled, ``/paic-search``
    fans out to additional platforms (PubMed / bioRxiv / OpenAlex / …)
    beyond arxiv + Semantic Scholar. Per-platform delays are Skill-layer
    voluntary throttling — see ``paic_search_pace``.

    ``default_preset`` seeds the platform list at ``/paic-init`` when the
    user doesn't pick a domain explicitly. ``inter_call_delay_sec`` is a
    per-platform dict; missing platforms fall back to 1.0s.
    """
    enabled: bool = False
    default_preset: str = DEFAULT_EXTERNAL_SEARCH_PRESET
    max_results_per_platform: int = DEFAULT_EXTERNAL_SEARCH_MAX_RESULTS
    inter_call_delay_sec: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_PLATFORM_PACING)
    )

    def delay_for(self, platform: str) -> float:
        """Lookup per-platform delay; default 1.0s for unknown platforms."""
        return float(self.inter_call_delay_sec.get(platform, 1.0))


@dataclass(frozen=True)
class ProviderImagesConfig:
    """Image generation backend (gpt-image-1 / dall-e-3 / OpenAI-compatible relays).

    Conceptually parallel to the LLM providers but uses a different OpenAI
    endpoint family (``/v1/images/{generations,edits,variations}``) so it
    needs its own backend rather than a node tag in the LLM router.
    Opt-in (``enabled=False`` default) — ``/paic-figure`` short-circuits
    when disabled with a config-pointer error.

    ``backend='openai_compatible'`` is the only supported backend in v1;
    set ``base_url`` to a relay (e.g. ``https://mytoken.top/v1``) or leave
    ``None`` for official ``api.openai.com``.

    ``model='gpt-image-1'`` is recommended (supports edits + variations);
    ``dall-e-3`` is generation-only (edit/variant calls return
    ``backend_does_not_support`` errors).
    """
    enabled: bool = False
    backend: Literal["openai_compatible"] = "openai_compatible"
    model: str = "gpt-image-1"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None
    size: str = "1024x1024"
    quality: str = "high"  # gpt-image-1: low|medium|high|auto


@dataclass(frozen=True)
class ProviderSemanticScholarConfig:
    """Semantic Scholar API client settings.

    The S2 graph API enforces ``1 req/sec cumulative across all endpoints`` for
    authenticated callers (lower for anonymous). We do client-side gating so
    bursts of ``/paic-search`` calls don't trip the limiter and waste a 429
    retry round.
    """
    api_key_env: str = "SEMANTIC_SCHOLAR_API_KEY"
    base_url: str = DEFAULT_S2_BASE_URL
    timeout_sec: float = DEFAULT_S2_TIMEOUT_SEC
    # None = auto-derive from whether we have a key (the recommended default).
    rate_limit_per_sec: float | None = None


@dataclass(frozen=True)
class OverleafConfig:
    """Bidirectional Overleaf sync via Dropbox — opt-in.

    Mechanism: Overleaf's account-level Dropbox integration creates
    ``~/Dropbox/Apps/Overleaf/`` and maps each subdirectory under it to
    one Overleaf project. PAI-C mirrors ``<project>/.paic/drafts/`` into
    that subdirectory and pulls back any Overleaf-side edits using a
    three-way merge against a baseline manifest stored at
    ``<project>/.paic/state/overleaf_sync.yaml``.

    Defaults are conservative — ``enabled=True`` only enables the
    feature; SKILL still asks the user before each sync, and missing
    ``target_root`` (i.e. user hasn't connected Dropbox in Overleaf)
    short-circuits silently.

    ``conflict_strategy``:

    - ``keep_both``: local stays put, remote version is renamed to
      ``<name>.overleaf-conflict.<UTC>.<ext>`` and pulled into the local
      tree; user manually merges.
    - ``local_wins`` / ``remote_wins``: deterministic overrides — drops
      one side's edit; useful when one end is the clear authority.
    - ``newer_wins``: by mtime; cross-OS / cross-Dropbox-client mtime
      precision can be unreliable, use with care.

    ``prompt_on_delete``: when True (default), a unilateral delete on
    one side is queued in ``deletions_pending`` and the SKILL asks the
    user before mirroring the delete to the other side. False = never
    propagate deletes (most conservative).
    """
    enabled: bool = True
    target_root: Path = DEFAULT_OVERLEAF_TARGET_ROOT
    project_subdir: str | None = None
    ignore_patterns: tuple[str, ...] = _DEFAULT_OVERLEAF_IGNORE_PATTERNS
    conflict_strategy: Literal[
        "keep_both", "local_wins", "remote_wins", "newer_wins"
    ] = "keep_both"
    prompt_on_delete: bool = True


# White-list of nodes whose tool implementation actually returns the
# ``mode: "host_orchestration"`` shape (markdown / schema_hint / next_tool).
# Routing any other node to ``host`` causes ``HostOrchestrationRequired`` at
# graph runtime — there's no client-side handler to catch it gracefully.
# Doctor and ``RoutingConfig.invalid_host_overrides`` use this to surface the
# misconfig at startup instead of mid-run.
HOST_SUPPORTED_NODES: frozenset[str] = frozenset(
    {"summarize", "draft_polish", "draft_compose"}
)


@dataclass(frozen=True)
class RoutingConfig:
    """Per-node routing.

    ``default``: backend name to use when a node label is not in ``overrides``.
        Accepted forms: ``anthropic`` (use anthropic provider's configured mode),
        ``openai`` (use openai provider's configured mode),
        ``anthropic.api_key`` / ``anthropic.claude_agent_sdk`` /
        ``openai.api`` / ``openai.compatible`` (force a specific mode).

    ``overrides``: ``{node_label: backend_name}``.

    ``fallback``: optional backend to retry on if the primary backend raises
        ``LLMUnavailable``. Useful when ``default: anthropic.claude_agent_sdk``
        relies on a Claude Code login that may not be present — set
        ``fallback: anthropic.api_key`` and the router quietly switches over.
    """
    default: str = "anthropic"
    overrides: dict[str, str] = field(default_factory=dict)
    fallback: str | None = None

    def invalid_host_overrides(self) -> list[str]:
        """Return node labels routed to ``host`` that aren't in the
        ``HOST_SUPPORTED_NODES`` white-list.

        These are configuration mistakes that crash the corresponding graph
        node with ``HostOrchestrationRequired`` the first time it's invoked.
        """
        return sorted(
            node
            for node, backend in self.overrides.items()
            if backend == "host" and node not in HOST_SUPPORTED_NODES
        )


# Reserved keys under ``providers:`` — not treated as named LLM profiles. The
# rest of the keys are interpreted as user-defined provider profiles (must
# carry a ``kind: anthropic|openai`` field, see ``_load_named_providers``).
RESERVED_PROVIDER_KEYS: frozenset[str] = frozenset(
    {"anthropic", "openai", "s2", "semantic_scholar", "arxiv", "external_search", "images"}
)


@dataclass(frozen=True)
class Config:
    global_dir: Path
    default_model: str
    arxiv_mcp_storage_paths: tuple[Path, ...]
    semantic_scholar_api_key: str | None
    anthropic_api_key: str | None
    openai_api_key: str | None
    providers_anthropic: ProviderAnthropicConfig
    providers_openai: ProviderOpenAIConfig | None
    providers_s2: ProviderSemanticScholarConfig
    providers_arxiv: ProviderArxivConfig
    providers_external_search: ProviderExternalSearchConfig
    providers_images: ProviderImagesConfig
    routing: RoutingConfig
    # User-defined named profiles beyond the reserved ``anthropic`` / ``openai``
    # keys. Each entry is either ProviderAnthropicConfig or ProviderOpenAIConfig;
    # the router instantiates the matching backend on demand.
    providers_named_extra: dict[str, ProviderAnthropicConfig | ProviderOpenAIConfig] = field(
        default_factory=dict
    )
    # Bidirectional Overleaf sync — opt-in via ~/.paic/config.yaml. Has a
    # default factory so older test fixtures that construct Config(...) without
    # specifying overleaf continue to work.
    overleaf: OverleafConfig = field(default_factory=OverleafConfig)
    raw: dict = field(default_factory=dict)

    @property
    def providers_named(self) -> dict[str, ProviderAnthropicConfig | ProviderOpenAIConfig]:
        """All available provider profiles keyed by profile name.

        Includes the reserved ``anthropic`` profile, optionally ``openai``, and
        every user-defined extra profile. Used by :class:`paic.llm.router.LLMRouter`
        to resolve backend names like ``openai_pro.api`` or just ``openai_pro``.
        """
        out: dict[str, ProviderAnthropicConfig | ProviderOpenAIConfig] = {
            "anthropic": self.providers_anthropic,
        }
        if self.providers_openai is not None:
            out["openai"] = self.providers_openai
        out.update(self.providers_named_extra)
        return out

    def profile_api_key(self, name: str) -> str | None:
        """Resolve the API key for a named provider profile.

        Reserved profiles reuse the cached ``anthropic_api_key`` / ``openai_api_key``
        fields (so changing ``api_key_env`` after :func:`load_config` runs is
        a no-op for those). Extra profiles read their ``api_key_env`` live —
        cheap and avoids stale-cache surprises after ``setx`` mid-session.
        """
        if name == "anthropic":
            return self.anthropic_api_key
        if name == "openai":
            return self.openai_api_key
        profile = self.providers_named_extra.get(name)
        if profile is None:
            return None
        return os.environ.get(profile.api_key_env)

    def effective_s2_rate_limit(self) -> float:
        """Return the rate limit (req/sec) the S2 client should enforce.

        Resolution: explicit ``providers_s2.rate_limit_per_sec`` > auto-derived
        based on whether an API key is available.
        """
        explicit = self.providers_s2.rate_limit_per_sec
        if explicit is not None and explicit > 0:
            return float(explicit)
        return (
            S2_RATE_LIMIT_AUTHENTICATED
            if self.semantic_scholar_api_key
            else S2_RATE_LIMIT_UNAUTHENTICATED
        )

    @property
    def arxiv_mcp_storage_path(self) -> Path:
        """First candidate (back-compat shim — prefer ``arxiv_mcp_storage_paths``)."""
        return self.arxiv_mcp_storage_paths[0]

    @property
    def cache_dir(self) -> Path:
        return self.global_dir / "cache"

    @property
    def s2_cache_dir(self) -> Path:
        return self.cache_dir / "s2"

    @property
    def llm_cache_dir(self) -> Path:
        return self.cache_dir / "llm"

    @property
    def pdf_text_cache_dir(self) -> Path:
        """Cache for pypdf text extraction (§25). Keyed by content sha256."""
        return self.cache_dir / "pdf_text"

    @property
    def personas_dir(self) -> Path:
        return self.global_dir / "personas"

    @property
    def runs_index_db(self) -> Path:
        return self.global_dir / "runs_index.sqlite"

    @property
    def library_meta_db(self) -> Path:
        return self.global_dir / "library_meta" / "papers.sqlite"


def _expand(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None:
        return None
    return Path(os.path.expandvars(str(value))).expanduser()


def _resolve_arxiv_storage_paths(raw: dict[str, Any]) -> tuple[Path, ...]:
    """Read ``arxiv_mcp_storage_path[s]`` from yaml; accept str | list[str] | None.

    Preserves order — caller probes first match. Always appends the built-in
    defaults at the end so a user-supplied custom path doesn't lose the
    fallback to the historical ``~/.arxiv-mcp/papers`` location.
    """
    raw_value = raw.get("arxiv_mcp_storage_paths") or raw.get("arxiv_mcp_storage_path")
    user: list[Path] = []
    if isinstance(raw_value, str):
        path = _expand(raw_value)
        if path is not None:
            user.append(path)
    elif isinstance(raw_value, list):
        for item in raw_value:
            path = _expand(item)
            if path is not None:
                user.append(path)

    seen: set[Path] = set()
    out: list[Path] = []
    for p in (*user, *DEFAULT_ARXIV_STORAGE_CANDIDATES):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


def _load_provider_anthropic(raw: dict[str, Any]) -> ProviderAnthropicConfig:
    block = raw.get("providers", {}).get("anthropic", {}) if isinstance(raw, dict) else {}
    if not isinstance(block, dict):
        block = {}
    base_url = block.get("base_url")
    return ProviderAnthropicConfig(
        mode=block.get("mode", "api_key"),
        model=block.get("model", DEFAULT_ANTHROPIC_MODEL),
        api_key_env=block.get("api_key_env", "ANTHROPIC_API_KEY"),
        base_url=str(base_url) if base_url else None,
    )


def _load_provider_openai(raw: dict[str, Any]) -> ProviderOpenAIConfig | None:
    block = raw.get("providers", {}).get("openai") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return None
    return ProviderOpenAIConfig(
        mode=block.get("mode", "api"),
        model=block.get("model", DEFAULT_OPENAI_MODEL),
        api_key_env=block.get("api_key_env", "OPENAI_API_KEY"),
        base_url=block.get("base_url"),
    )


def _load_named_providers(
    raw: dict[str, Any],
) -> dict[str, ProviderAnthropicConfig | ProviderOpenAIConfig]:
    """Parse user-defined provider profiles under ``providers.<name>``.

    Skips :data:`RESERVED_PROVIDER_KEYS` (those have dedicated loaders).
    Each remaining entry must be a dict carrying a ``kind: anthropic|openai``
    field; profiles missing/with an unknown kind are silently dropped (logging
    here would require a logger import; unknown profiles will raise a clear
    error from the router instead).
    """
    block = raw.get("providers", {}) if isinstance(raw, dict) else {}
    if not isinstance(block, dict):
        return {}
    out: dict[str, ProviderAnthropicConfig | ProviderOpenAIConfig] = {}
    for name, entry in block.items():
        if name in RESERVED_PROVIDER_KEYS:
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind == "anthropic":
            base_url = entry.get("base_url")
            out[str(name)] = ProviderAnthropicConfig(
                mode=entry.get("mode", "api_key"),
                model=entry.get("model", DEFAULT_ANTHROPIC_MODEL),
                api_key_env=entry.get("api_key_env", "ANTHROPIC_API_KEY"),
                base_url=str(base_url) if base_url else None,
            )
        elif kind == "openai":
            out[str(name)] = ProviderOpenAIConfig(
                mode=entry.get("mode", "api"),
                model=entry.get("model", DEFAULT_OPENAI_MODEL),
                api_key_env=entry.get("api_key_env", "OPENAI_API_KEY"),
                base_url=entry.get("base_url"),
            )
        # Unknown kind → drop. Router will raise a descriptive error if anyone
        # routes to this name.
    return out


def _load_provider_arxiv(raw: dict[str, Any]) -> ProviderArxivConfig:
    block = raw.get("providers", {}).get("arxiv") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}
    batch_size = block.get("batch_size", DEFAULT_ARXIV_BATCH_SIZE)
    delay = block.get("inter_batch_delay_sec", DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC)
    try:
        batch_size = max(1, int(batch_size))
    except (TypeError, ValueError):
        batch_size = DEFAULT_ARXIV_BATCH_SIZE
    try:
        delay = max(0.0, float(delay))
    except (TypeError, ValueError):
        delay = DEFAULT_ARXIV_INTER_BATCH_DELAY_SEC
    return ProviderArxivConfig(batch_size=batch_size, inter_batch_delay_sec=delay)


def _load_provider_external_search(raw: dict[str, Any]) -> ProviderExternalSearchConfig:
    block = raw.get("providers", {}).get("external_search") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}

    enabled = bool(block.get("enabled", False))
    preset = str(block.get("default_preset", DEFAULT_EXTERNAL_SEARCH_PRESET))
    if preset not in DOMAIN_PRESETS:
        preset = DEFAULT_EXTERNAL_SEARCH_PRESET

    max_results = block.get("max_results_per_platform", DEFAULT_EXTERNAL_SEARCH_MAX_RESULTS)
    try:
        max_results = max(1, int(max_results))
    except (TypeError, ValueError):
        max_results = DEFAULT_EXTERNAL_SEARCH_MAX_RESULTS

    # Start with built-in defaults, then layer user overrides on top — so a user
    # who sets only ``pubmed: 0.6`` still gets sensible defaults for every other
    # platform without re-typing the table.
    pacing: dict[str, float] = dict(_DEFAULT_PLATFORM_PACING)
    user_pacing = block.get("inter_call_delay_sec") or {}
    if isinstance(user_pacing, dict):
        for platform, delay in user_pacing.items():
            try:
                pacing[str(platform)] = max(0.0, float(delay))
            except (TypeError, ValueError):
                continue

    return ProviderExternalSearchConfig(
        enabled=enabled,
        default_preset=preset,
        max_results_per_platform=max_results,
        inter_call_delay_sec=pacing,
    )


def _load_provider_images(raw: dict[str, Any]) -> ProviderImagesConfig:
    block = raw.get("providers", {}).get("images") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return ProviderImagesConfig()
    return ProviderImagesConfig(
        enabled=bool(block.get("enabled", False)),
        backend=block.get("backend", "openai_compatible"),
        model=block.get("model", "gpt-image-1"),
        api_key_env=block.get("api_key_env", "OPENAI_API_KEY"),
        base_url=block.get("base_url"),
        size=block.get("size", "1024x1024"),
        quality=block.get("quality", "high"),
    )


def _load_provider_s2(raw: dict[str, Any]) -> ProviderSemanticScholarConfig:
    block = raw.get("providers", {}).get("semantic_scholar") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}
    rate = block.get("rate_limit_per_sec")
    return ProviderSemanticScholarConfig(
        api_key_env=block.get("api_key_env", "SEMANTIC_SCHOLAR_API_KEY"),
        base_url=block.get("base_url", DEFAULT_S2_BASE_URL),
        timeout_sec=float(block.get("timeout_sec", DEFAULT_S2_TIMEOUT_SEC)),
        rate_limit_per_sec=float(rate) if rate is not None else None,
    )


def _load_overleaf(raw: dict[str, Any]) -> OverleafConfig:
    block = raw.get("overleaf") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return OverleafConfig()

    target_root_raw = block.get("target_root")
    if isinstance(target_root_raw, str) and target_root_raw.strip():
        target_root = Path(target_root_raw).expanduser()
    else:
        target_root = DEFAULT_OVERLEAF_TARGET_ROOT

    patterns_raw = block.get("ignore_patterns")
    if isinstance(patterns_raw, list) and patterns_raw:
        ignore_patterns: tuple[str, ...] = tuple(str(p) for p in patterns_raw)
    else:
        ignore_patterns = _DEFAULT_OVERLEAF_IGNORE_PATTERNS

    strategy = block.get("conflict_strategy", "keep_both")
    if strategy not in ("keep_both", "local_wins", "remote_wins", "newer_wins"):
        strategy = "keep_both"

    return OverleafConfig(
        enabled=bool(block.get("enabled", True)),
        target_root=target_root,
        project_subdir=block.get("project_subdir") or None,
        ignore_patterns=ignore_patterns,
        conflict_strategy=strategy,  # type: ignore[arg-type]
        prompt_on_delete=bool(block.get("prompt_on_delete", True)),
    )


def _load_routing(raw: dict[str, Any]) -> RoutingConfig:
    block = raw.get("routing", {}) if isinstance(raw, dict) else {}
    if not isinstance(block, dict):
        block = {}
    overrides = block.get("overrides") or {}
    if not isinstance(overrides, dict):
        overrides = {}
    return RoutingConfig(
        default=block.get("default", "anthropic"),
        overrides={str(k): str(v) for k, v in overrides.items()},
        fallback=block.get("fallback"),
    )


@lru_cache(maxsize=1)
def load_config(global_dir: Path | None = None) -> Config:
    base = global_dir or Path(os.environ.get("PAIC_HOME", DEFAULT_GLOBAL_DIR))
    base = base.expanduser().resolve()

    config_file = base / "config.yaml"
    raw: dict = {}
    if config_file.exists():
        with config_file.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                raw = loaded

    arxiv_storage_paths = _resolve_arxiv_storage_paths(raw)
    providers_anthropic = _load_provider_anthropic(raw)
    providers_openai = _load_provider_openai(raw)
    providers_s2 = _load_provider_s2(raw)
    providers_arxiv = _load_provider_arxiv(raw)
    providers_external_search = _load_provider_external_search(raw)
    providers_images = _load_provider_images(raw)
    overleaf = _load_overleaf(raw)
    providers_named_extra = _load_named_providers(raw)
    routing = _load_routing(raw)

    # S2 key precedence: yaml literal > providers_s2.api_key_env > legacy env name.
    # The yaml-literal path is kept for backward compat but discouraged (use env).
    s2_key = (
        raw.get("semantic_scholar_api_key")
        or os.environ.get(providers_s2.api_key_env)
        or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    )

    return Config(
        global_dir=base,
        default_model=raw.get("default_model") or providers_anthropic.model,
        arxiv_mcp_storage_paths=arxiv_storage_paths,
        semantic_scholar_api_key=s2_key,
        anthropic_api_key=os.environ.get(providers_anthropic.api_key_env),
        openai_api_key=os.environ.get(providers_openai.api_key_env)
        if providers_openai is not None
        else os.environ.get("OPENAI_API_KEY"),
        providers_anthropic=providers_anthropic,
        providers_openai=providers_openai,
        providers_s2=providers_s2,
        providers_arxiv=providers_arxiv,
        providers_external_search=providers_external_search,
        providers_images=providers_images,
        overleaf=overleaf,
        routing=routing,
        providers_named_extra=providers_named_extra,
        raw=raw,
    )


def ensure_global_dirs(cfg: Config | None = None) -> Config:
    """Create the global directory tree on first access."""
    cfg = cfg or load_config()
    for path in (
        cfg.global_dir,
        cfg.cache_dir,
        cfg.s2_cache_dir,
        cfg.llm_cache_dir,
        cfg.personas_dir,
        cfg.global_dir / "library_meta",
        cfg.global_dir / "prompts_overrides",
    ):
        path.mkdir(parents=True, exist_ok=True)
    return cfg


def reset_config_cache() -> None:
    """Clear cached config — useful in tests."""
    load_config.cache_clear()

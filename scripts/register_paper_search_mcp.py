"""Register the upstream paper-search-mcp server with Claude Code — §18.

Adds an entry to ``~/.claude.json`` under ``mcpServers`` named ``paper_search``,
pointing at a local checkout of ``paper-search-mcp``. This is the **opt-in**
path for PAI-C's multi-platform search: PAI-C does not bundle paper-search-mcp,
and ``register_mcp.py`` does not register it automatically.

Default location is ``~/Desktop/paper-search-mcp``; override with ``--path``
or ``$PAPER_SEARCH_MCP_HOME``. The script writes only the registration
entry — install paper-search-mcp's own dependencies separately
(``cd <path> && uv sync``) before running this.

Optional API keys / contact emails that paper-search-mcp consumes are
propagated from the host environment via Claude Code's ``${VAR}``
substitution. None are required; missing keys gracefully degrade (e.g.
IEEE / ACM are simply skipped at search time).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CLAUDE_CONFIG = Path.home() / ".claude.json"
SERVER_KEY = "paper_search"
DEFAULT_PATH = Path.home() / "Desktop" / "paper-search-mcp"


def _resolve_path(arg_path: str | None) -> Path:
    if arg_path:
        candidate = Path(arg_path).expanduser().resolve()
    elif os.environ.get("PAPER_SEARCH_MCP_HOME"):
        candidate = Path(os.environ["PAPER_SEARCH_MCP_HOME"]).expanduser().resolve()
    else:
        candidate = DEFAULT_PATH.expanduser().resolve()
    if not candidate.exists():
        sys.exit(
            f"paper-search-mcp checkout not found at {candidate}.\n"
            f"Pass --path <dir> or set PAPER_SEARCH_MCP_HOME, or clone "
            f"paper-search-mcp to {DEFAULT_PATH} first."
        )
    pyproject = candidate / "pyproject.toml"
    if not pyproject.is_file():
        sys.exit(
            f"{candidate} doesn't look like a paper-search-mcp checkout "
            f"(no pyproject.toml). Pass the actual repo root via --path."
        )
    return candidate


def _load() -> dict:
    if not CLAUDE_CONFIG.exists():
        return {}
    try:
        return json.loads(CLAUDE_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"Refusing to overwrite malformed {CLAUDE_CONFIG}: {exc}")


def _save(cfg: dict) -> None:
    CLAUDE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_CONFIG.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def build_entry(repo_path: Path) -> dict:
    """Build the mcpServers entry for paper-search-mcp.

    The ``env`` block below is ADDITIVE — Claude Code merges these into the
    inherited environment of the spawned MCP child. Every value uses
    ``${VAR}`` substitution so we do NOT bake host secrets into the json file
    on disk. Variables that are unset on the host produce empty strings,
    which paper-search-mcp treats as "no key" and falls back to anonymous /
    skip for the corresponding platform.
    """
    return {
        "type": "stdio",
        "command": "uv",
        "args": [
            "run", "--directory", str(repo_path),
            "python", "-m", "paper_search_mcp.server",
        ],
        "env": {
            # Optional upstream API keys (all gracefully degrade if unset):
            "PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "${SEMANTIC_SCHOLAR_API_KEY}",
            "PAPER_SEARCH_MCP_CORE_API_KEY": "${CORE_API_KEY}",
            "PAPER_SEARCH_MCP_IEEE_API_KEY": "${IEEE_API_KEY}",
            "PAPER_SEARCH_MCP_ACM_API_KEY": "${ACM_API_KEY}",
            "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL": "${UNPAYWALL_EMAIL}",
            "PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL": "${GOOGLE_SCHOLAR_PROXY_URL}",
        },
    }


def add(repo_path: Path) -> None:
    cfg = _load()
    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"][SERVER_KEY] = build_entry(repo_path)
    _save(cfg)
    print(f"Registered '{SERVER_KEY}' MCP server in {CLAUDE_CONFIG}")
    print(f"  command: uv run --directory {repo_path} python -m paper_search_mcp.server")
    print()
    print("Next steps:")
    print("  1. Make sure paper-search-mcp's deps are installed:")
    print(f"       cd {repo_path} && uv sync")
    print( "  2. Enable PAI-C's multi-platform flow by editing ~/.paic/config.yaml:")
    print( "       providers:")
    print( "         external_search:")
    print( "           enabled: true")
    print( "  3. Fully restart Claude Code so both MCP servers re-spawn.")
    print( "  4. Check the wiring with `uv run paic doctor`.")


def remove() -> None:
    cfg = _load()
    servers = cfg.get("mcpServers", {})
    if SERVER_KEY in servers:
        del servers[SERVER_KEY]
        _save(cfg)
        print(f"Removed '{SERVER_KEY}' from {CLAUDE_CONFIG}")
    else:
        print(f"'{SERVER_KEY}' was not registered.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        help=f"Path to the paper-search-mcp checkout (default: {DEFAULT_PATH}).",
    )
    parser.add_argument("--remove", action="store_true", help="Remove the entry instead of adding it.")
    args = parser.parse_args()
    if args.remove:
        remove()
    else:
        repo_path = _resolve_path(args.path)
        add(repo_path)


if __name__ == "__main__":
    main()

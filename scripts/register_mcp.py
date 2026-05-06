"""Register the PAI-C MCP server with Claude Code.

Adds an entry to ``~/.claude.json`` under ``mcpServers`` named ``paic``. Re-run
to overwrite the existing entry. Pass ``--remove`` to delete it.

The server is launched via ``uv run --directory <repo> paic-mcp`` so that it
always uses this checkout's virtualenv (no install pollution into the system
Python). ``ANTHROPIC_API_KEY`` and ``OPENAI_API_KEY`` are propagated from the
host environment.

Side-effect on first run: also bootstraps ``~/.paic/`` (the global PAI-C
workspace) and seeds ``~/.paic/config.yaml`` from ``docs/config.yaml.example``
if it doesn't already exist. PAI-C's runtime would create the directory lazily
on first ``/paic-search`` or ``/paic-ideate``, but doing it at registration
time is much less surprising for new users.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CONFIG = Path.home() / ".claude.json"
PAIC_HOME = Path.home() / ".paic"
EXAMPLE_CONFIG = REPO_ROOT / "docs" / "config.yaml.example"
SERVER_KEY = "paic"


def load_config() -> dict:
    if not CLAUDE_CONFIG.exists():
        return {}
    try:
        return json.loads(CLAUDE_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"Refusing to overwrite malformed {CLAUDE_CONFIG}: {exc}")


def save_config(cfg: dict) -> None:
    CLAUDE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_CONFIG.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def build_entry() -> dict:
    # The ``env`` block below is ADDITIVE — Claude Code merges it into the
    # spawned MCP child's inherited environment rather than replacing it. That
    # means HOME / USERPROFILE / PATH / and the keychain-style storage that
    # ``claude login`` writes to all flow through automatically. Do NOT switch
    # to a replacement-style env: ``claude_agent_sdk`` relies on inheriting
    # the user's HOME so it can locate the OAuth token written by ``claude
    # login``. Removing that inheritance silently breaks subscription mode.
    return {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "--directory", str(REPO_ROOT), "paic-mcp"],
        "env": {
            # Claude Code substitutes ${VAR} from the host env at spawn time.
            "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
            "OPENAI_API_KEY": "${OPENAI_API_KEY}",
            "SEMANTIC_SCHOLAR_API_KEY": "${SEMANTIC_SCHOLAR_API_KEY}",
        },
    }


def bootstrap_paic_home() -> None:
    """Create ``~/.paic/`` and seed ``config.yaml`` from the example.

    Mirrors what ``paic.config.ensure_global_dirs`` does at runtime, plus a
    one-time copy of the example config. We don't import paic here to avoid
    pulling the whole dep tree into the registration step.
    """
    for sub in ("", "cache", "cache/s2", "cache/llm", "personas",
                "library_meta", "prompts_overrides"):
        (PAIC_HOME / sub).mkdir(parents=True, exist_ok=True)

    target = PAIC_HOME / "config.yaml"
    if not target.exists() and EXAMPLE_CONFIG.exists():
        shutil.copyfile(EXAMPLE_CONFIG, target)
        print(f"Seeded {target} from docs/config.yaml.example")
    else:
        print(f"Global workspace ready at {PAIC_HOME}")


def add() -> None:
    cfg = load_config()
    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"][SERVER_KEY] = build_entry()
    save_config(cfg)
    bootstrap_paic_home()
    print(f"Registered '{SERVER_KEY}' MCP server in {CLAUDE_CONFIG}")
    print(f"  command: uv run --directory {REPO_ROOT} paic-mcp")


def remove() -> None:
    cfg = load_config()
    servers = cfg.get("mcpServers", {})
    if SERVER_KEY in servers:
        del servers[SERVER_KEY]
        save_config(cfg)
        print(f"Removed '{SERVER_KEY}' from {CLAUDE_CONFIG}")
    else:
        print(f"'{SERVER_KEY}' was not registered.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="Remove the entry instead of adding it")
    args = parser.parse_args()
    if args.remove:
        remove()
    else:
        add()


if __name__ == "__main__":
    main()

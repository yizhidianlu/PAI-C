"""Developer CLI for PAI-C.

Most user-facing entry points are Claude Code skills + MCP tools. This CLI is
only for ops & debugging: starting the MCP server, listing graph runs, etc.
"""

from __future__ import annotations

import click

from paic import __version__


@click.group()
@click.version_option(__version__)
def main() -> None:
    """PAI-C developer CLI."""


@main.command("info")
def info() -> None:
    """Print resolved global config."""
    from paic.config import load_config
    from paic.llm.router import LLMRouter

    cfg = load_config()
    click.echo(f"PAI-C {__version__}")
    click.echo(f"global_dir         : {cfg.global_dir}")
    click.echo(f"default_model      : {cfg.default_model}")
    if len(cfg.arxiv_mcp_storage_paths) == 1:
        click.echo(f"arxiv mcp storage  : {cfg.arxiv_mcp_storage_paths[0]}")
    else:
        click.echo("arxiv mcp storage  :")
        for path in cfg.arxiv_mcp_storage_paths:
            mark = "OK " if path.exists() else "-- "
            click.echo(f"    [{mark}] {path}")
    click.echo(
        f"anthropic api key  : {'set' if cfg.anthropic_api_key else 'NOT SET'}"
    )
    click.echo(
        f"openai api key     : {'set' if cfg.openai_api_key else 'not set'}"
    )
    click.echo(f"anthropic mode     : {cfg.providers_anthropic.mode}")
    if cfg.providers_openai is not None:
        click.echo(f"openai mode        : {cfg.providers_openai.mode}")
        if cfg.providers_openai.base_url:
            click.echo(f"openai base_url    : {cfg.providers_openai.base_url}")
    else:
        click.echo("openai mode        : (not configured)")
    if cfg.providers_named_extra:
        click.echo("named profiles    :")
        for name, profile in cfg.providers_named_extra.items():
            kind = "anthropic" if "anthropic" in type(profile).__name__.lower() else "openai"
            base_suffix = f" base_url={profile.base_url}" if profile.base_url else ""
            click.echo(
                f"  {name:25s} kind={kind} mode={profile.mode} "
                f"model={profile.model}{base_suffix}"
            )

    try:
        routing = LLMRouter(cfg).describe()
        click.echo(f"routing default    : {routing['default']}")
        if routing["overrides"]:
            click.echo("routing overrides:")
            for node, backend in routing["overrides"].items():
                click.echo(f"  {node:35s} → {backend}")
        else:
            click.echo("routing overrides  : (none)")
    except Exception as exc:  # pragma: no cover
        click.echo(f"routing            : ERROR {exc}")


@main.command("doctor")
@click.option(
    "--probe",
    is_flag=True,
    help="Also do a live claude_agent_sdk handshake (1-3s, costs ~10 tokens). "
    "Off by default to keep doctor fast and offline-friendly.",
)
def doctor(probe: bool) -> None:
    """Run preflight diagnostics. Exits non-zero if any check fails."""
    import sys as _sys

    from paic.doctor import format_report, has_errors, run_all

    checks = run_all(probe_sdk=probe)
    click.echo(format_report(checks))
    if has_errors(checks):
        _sys.exit(1)


@main.command("sdk-probe")
def sdk_probe() -> None:
    """Probe claude_agent_sdk: do a 1-token roundtrip and print the raw result.

    Use this when /paic-* commands fail with `claude-agent-sdk reported ...`
    and you want to see the underlying error WITHOUT going through Claude
    Code's MCP layer. Exits 0 on success, 1 on failure.
    """
    import sys as _sys

    from paic.config import load_config
    from paic.llm.backends.anthropic_agent_sdk import probe_sdk as _probe_sdk

    cfg = load_config()
    model = cfg.providers_anthropic.model
    click.echo(f"Probing claude_agent_sdk (model={model})...")
    result = _probe_sdk(model=model)
    if result.ok:
        click.echo(f"[OK ] handshake succeeded ({result.latency_s:.2f}s)")
        click.echo(f"  reply: {result.text!r}")
        click.echo("")
        click.echo(
            "claude_agent_sdk auth is working from this shell. If /paic-* commands"
        )
        click.echo(
            "are still failing INSIDE Claude Code, the issue is the MCP spawn env"
        )
        click.echo(
            "(token not visible to the child process). See troubleshooting.md."
        )
        return
    click.echo(f"[ERR] handshake failed ({result.latency_s:.2f}s)")
    click.echo("")
    click.echo(result.error)
    _sys.exit(1)


@main.command("serve")
def serve() -> None:
    """Run the PAI-C MCP server (stdio transport)."""
    from paic.mcp_server.server import main as serve_main

    serve_main()


# NOTE: a local ``paic.web`` workbench exists out-of-tree; it is not part
# of the installable package. Run it directly via ``python -m paic.web.server``
# if you have ``src/paic/web/`` locally.


if __name__ == "__main__":
    main()

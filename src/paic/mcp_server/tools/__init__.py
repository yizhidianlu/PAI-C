"""MCP tool implementations.

Each module exposes pure-Python functions that the FastMCP server registers as
tools. Keeping the logic separate from the server entry point makes it
unit-testable without spinning up MCP transport.
"""

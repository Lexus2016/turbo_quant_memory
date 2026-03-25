"""Command line interface for the local Turbo Quant Memory MCP server."""

from __future__ import annotations

import argparse
from typing import Sequence

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="turbo-memory-mcp",
        description="Run the local Turbo Quant Memory MCP server.",
        epilog="Blessed runtime: turbo-memory-mcp serve",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="command")
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the local stdio MCP server.",
        description="Start the local stdio MCP server.",
    )
    serve_parser.set_defaults(handler=_handle_serve)
    return parser


def _handle_serve(_: argparse.Namespace) -> int:
    from .server import run_stdio_server

    run_stdio_server()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return int(handler(args))

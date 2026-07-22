"""``hermes context`` subcommand parser adapter.

The authoritative parser tree and command implementation live in
``hermes_cli.context_commands``. This module only adapts that parser builder to
the dependency-injection convention used by ``hermes_cli.main``.
"""

from __future__ import annotations

from typing import Callable

from hermes_cli.context_commands import (
    build_context_parser as build_authoritative_context_parser,
    context_command as cmd_context,
)


def build_context_parser(subparsers, *, cmd_context: Callable) -> None:
    """Attach the authoritative ``context`` parser and inject its handler."""
    context_parser = build_authoritative_context_parser(subparsers)
    context_parser.set_defaults(func=cmd_context)

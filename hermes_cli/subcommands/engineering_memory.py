"""``hermes engineering-memory`` subcommand parser adapter."""

from __future__ import annotations

from typing import Callable

from hermes_cli.engineering_memory_commands import (
    build_engineering_memory_parser as build_authoritative_engineering_memory_parser,
)
from hermes_cli.engineering_memory_commands import (
    engineering_memory_command as cmd_engineering_memory,
)


def build_engineering_memory_parser(
    subparsers,
    *,
    cmd_engineering_memory: Callable,
) -> None:
    """Attach the authoritative Engineering Memory parser."""
    parser = build_authoritative_engineering_memory_parser(
        subparsers
    )
    parser.set_defaults(func=cmd_engineering_memory)

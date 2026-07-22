"""``hermes autonomous-backlog`` subcommand parser adapter."""

from __future__ import annotations

from typing import Callable

from hermes_cli.autonomous_backlog_commands import (
    autonomous_backlog_command as cmd_autonomous_backlog,
)
from hermes_cli.autonomous_backlog_commands import (
    build_autonomous_backlog_parser as build_authoritative_autonomous_backlog_parser,
)


def build_autonomous_backlog_parser(
    subparsers,
    *,
    cmd_autonomous_backlog: Callable,
) -> None:
    parser = build_authoritative_autonomous_backlog_parser(
        subparsers
    )
    parser.set_defaults(func=cmd_autonomous_backlog)

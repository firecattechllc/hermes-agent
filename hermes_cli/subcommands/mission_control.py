"""``hermes mission-control`` subcommand parser adapter."""

from __future__ import annotations

from typing import Callable

from hermes_cli.mission_control_commands import (
    build_mission_control_parser as build_authoritative_mission_control_parser,
    mission_control_command as cmd_mission_control,
)


def build_mission_control_parser(subparsers, *, cmd_mission_control: Callable) -> None:
    parser = build_authoritative_mission_control_parser(subparsers)
    parser.set_defaults(func=cmd_mission_control)

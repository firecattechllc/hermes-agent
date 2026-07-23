"""``hermes link`` parser adapter."""

from hermes_cli.hermes_link.commands import (
    build_link_parser as _build,
    link_command as cmd_link,
)


def build_link_parser(subparsers, *, cmd_link):
    parser = _build(subparsers)
    parser.set_defaults(func=cmd_link)

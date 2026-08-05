"""``hermes knowledge`` parser adapter."""

from hermes_cli.knowledge.commands import (
    build_knowledge_parser as _build,
    knowledge_command as cmd_knowledge,
)


def build_knowledge_parser(subparsers, *, cmd_knowledge):
    parser = _build(subparsers)
    parser.set_defaults(func=cmd_knowledge)

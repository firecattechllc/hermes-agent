from __future__ import annotations

import argparse

from hermes_cli.knowledge.commands import build_knowledge_parser


def parser():
    root = argparse.ArgumentParser()
    build_knowledge_parser(root.add_subparsers(dest="command", required=True))
    return root


def test_cli_exposes_all_step33_commands():
    for command in (
        "discover",
        "status",
        "entities",
        "show",
        "neighbors",
        "changes",
        "impact",
        "export",
        "collectors",
    ):
        args = parser().parse_args(
            ["knowledge", command]
            + (
                ["entity:1"]
                if command in {"show", "neighbors", "impact"}
                else ["--redacted", "--output", "out.json"]
                if command == "export"
                else []
            )
        )
        assert args.knowledge_command == command

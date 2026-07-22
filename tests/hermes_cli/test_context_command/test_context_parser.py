"""Behavioral tests for the ``hermes context`` CLI."""

from __future__ import annotations

import argparse
from unittest.mock import Mock

import pytest

from hermes_cli.context_commands import (
    build_context_parser,
    context_command,
)


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    """Build a minimal top-level parser containing the context command."""
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")

    context_parser = build_context_parser(subparsers)
    context_parser.set_defaults(func=context_command)

    return parser


def test_context_project_add_parses_and_registers_dispatch(parser):
    args = parser.parse_args(
        [
            "context",
            "project",
            "add",
            "Hermes Platform",
            "--project-id",
            "hermes-platform",
            "--repository",
            "firecattechllc/hermes-platform",
        ]
    )

    assert args.command == "context"
    assert args.context_action == "project"
    assert args.project_action == "add"
    assert args.display_name == "Hermes Platform"
    assert args.project_id == "hermes-platform"
    assert args.repository == "firecattechllc/hermes-platform"
    assert args.func is context_command


def test_context_record_add_parses_required_values(parser):
    args = parser.parse_args(
        [
            "context",
            "record",
            "add",
            "hermes-platform",
            "decision",
            "Use one authoritative parser",
            "--tag",
            "launch-001a",
        ]
    )

    assert args.context_action == "record"
    assert args.record_action == "add"
    assert args.project == "hermes-platform"
    assert args.type == "decision"
    assert args.title == "Use one authoritative parser"
    assert args.tag == ["launch-001a"]


def test_context_snapshot_show_requires_project(parser):
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["context", "snapshot", "show"])

    assert exc_info.value.code != 0

    args = parser.parse_args(
        ["context", "snapshot", "show", "hermes-platform", "--markdown"]
    )

    assert args.context_action == "snapshot"
    assert args.snapshot_action == "show"
    assert args.project == "hermes-platform"
    assert args.markdown is True


def test_context_audit_accepts_optional_project(parser):
    all_projects = parser.parse_args(["context", "audit"])
    one_project = parser.parse_args(
        ["context", "audit", "hermes-platform", "--limit", "25"]
    )

    assert all_projects.context_action == "audit"
    assert all_projects.project is None
    assert one_project.project == "hermes-platform"
    assert one_project.limit == 25


def test_context_command_dispatches_project_action():
    handler = Mock(return_value=17)
    args = type(
        "Args",
        (),
        {
            "context_action": "project",
            "project_action": "add",
            "_project_add": handler,
            "_project_show": Mock(),
            "_project_list": Mock(),
            "_project_archive": Mock(),
        },
    )()

    result = context_command(args)

    assert result == 17
    handler.assert_called_once_with(args)

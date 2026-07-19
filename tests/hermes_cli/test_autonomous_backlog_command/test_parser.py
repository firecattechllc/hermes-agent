import argparse

from hermes_cli.autonomous_backlog_commands import (
    build_autonomous_backlog_parser,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_autonomous_backlog_parser(subparsers)
    return parser


def test_add_parser_accepts_governance_fields() -> None:
    args = _parser().parse_args(
        [
            "autonomous-backlog",
            "add",
            "hermes-platform",
            "--item-id",
            "backlog_1",
            "--title",
            "Build CLI integration",
            "--description",
            "Wire the autonomous backlog into Hermes.",
            "--source-ref",
            "manual:test",
            "--priority",
            "high",
            "--risk",
            "medium",
            "--acceptance",
            "Focused tests pass.",
            "--allow-path",
            "hermes_cli/",
        ]
    )

    assert args.command == "autonomous-backlog"
    assert args.autonomous_backlog_action == "add"
    assert args.project == "hermes-platform"
    assert args.item_id == "backlog_1"
    assert args.priority == "high"
    assert args.risk == "medium"
    assert args.acceptance == ["Focused tests pass."]
    assert args.allow_path == ["hermes_cli/"]
    assert callable(args._autonomous_backlog_handler)


def test_backlog_alias_routes_to_start() -> None:
    args = _parser().parse_args(
        [
            "backlog",
            "start",
            "hermes-platform",
            "backlog_1",
            "--expected-version",
            "2",
        ]
    )

    assert args.autonomous_backlog_action == "start"
    assert args.project == "hermes-platform"
    assert args.item == "backlog_1"
    assert args.expected_version == 2
    assert callable(args._autonomous_backlog_handler)


def test_complete_accepts_multiple_evidence_refs() -> None:
    args = _parser().parse_args(
        [
            "autonomous-backlog",
            "complete",
            "hermes-platform",
            "backlog_1",
            "--evidence",
            "pytest:focused",
            "--evidence",
            "review:approved",
        ]
    )

    assert args.evidence == [
        "pytest:focused",
        "review:approved",
    ]

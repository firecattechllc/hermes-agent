"""``hermes autonomous-backlog`` CLI commands."""

from __future__ import annotations

import argparse
import sys
from typing import List

from hermes_cli.autonomous_backlog import models as m
from hermes_cli.autonomous_backlog.service import (
    AutonomousBacklogService,
)


def _get_service() -> AutonomousBacklogService:
    return AutonomousBacklogService()


def _cmd_add(args: argparse.Namespace) -> int:
    service = _get_service()

    source_refs = tuple(args.source_ref or ())

    source = m.BacklogSource(
        source_type=m.BacklogSourceType(args.source_type),
        source_refs=source_refs,
        captured_by="cli",
    )

    try:
        item = service.create_item(
            project_id=args.project,
            item_id=args.item_id or None,
            title=args.title,
            description=args.description,
            source=source,
            actor="cli",
            priority=m.BacklogPriority(args.priority),
            risk_level=m.BacklogRiskLevel(args.risk),
            dependencies=args.depends_on or [],
            acceptance_criteria=args.acceptance or [],
            required_capabilities=args.capability or [],
            allowed_paths=args.allow_path or [],
            denied_paths=args.deny_path or [],
            execution_policy_id=args.execution_policy or None,
            correlation_id=args.correlation_id or None,
            idempotency_key=args.idempotency_key or None,
        )
    except ValueError as exc:
        print(
            f"autonomous-backlog add: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"backlog: created {item.item_id}")
    print(f"  project:  {item.project_id}")
    print(f"  status:   {item.status.value}")
    print(f"  priority: {item.priority.value}")
    print(f"  risk:     {item.risk_level.value}")
    print(f"  title:    {item.title}")
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        item = service.approve_item(
            args.project,
            args.item,
            actor="cli",
            expected_version=args.expected_version,
            correlation_id=args.correlation_id or None,
            idempotency_key=args.idempotency_key or None,
        )
    except ValueError as exc:
        print(
            f"autonomous-backlog approve: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"backlog: approved {item.item_id}")
    print(f"  status:  {item.status.value}")
    print(f"  version: {item.version}")
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        item = service.start_item(
            args.project,
            args.item,
            actor="cli",
            expected_version=args.expected_version,
            correlation_id=args.correlation_id or None,
            idempotency_key=args.idempotency_key or None,
        )
    except ValueError as exc:
        print(
            f"autonomous-backlog start: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"backlog: started {item.item_id}")
    print(f"  status:  {item.status.value}")
    print(f"  version: {item.version}")
    return 0


def _cmd_complete(args: argparse.Namespace) -> int:
    service = _get_service()
    evidence_refs: List[str] = args.evidence or []

    try:
        item = service.complete_item(
            args.project,
            args.item,
            evidence_refs=evidence_refs,
            actor="cli",
            expected_version=args.expected_version,
            correlation_id=args.correlation_id or None,
            idempotency_key=args.idempotency_key or None,
        )
    except ValueError as exc:
        print(
            f"autonomous-backlog complete: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"backlog: completed {item.item_id}")
    print(f"  status:   {item.status.value}")
    print(f"  version:  {item.version}")
    print(f"  evidence: {len(item.evidence_refs)}")
    return 0


def _add_transition_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument("project", help="Project ID")
    parser.add_argument("item", help="Backlog item ID")
    parser.add_argument(
        "--expected-version",
        type=int,
        default=None,
        help="Require the current item version to match",
    )
    parser.add_argument(
        "--correlation-id",
        default="",
        help="Correlation identifier",
    )
    parser.add_argument(
        "--idempotency-key",
        default="",
        help="Idempotency key",
    )


def build_autonomous_backlog_parser(
    parent_subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = parent_subparsers.add_parser(
        "autonomous-backlog",
        aliases=["autonomous_backlog", "backlog"],
        help="Manage governed autonomous engineering work",
        description=(
            "Create and transition durable autonomous backlog items."
        ),
    )

    sub = parser.add_subparsers(
        dest="autonomous_backlog_action",
    )

    add = sub.add_parser(
        "add",
        aliases=["create"],
        help="Create a backlog candidate",
    )
    add.add_argument("project", help="Project ID")
    add.add_argument("--item-id", default="", help="Explicit item ID")
    add.add_argument("--title", required=True, help="Backlog title")
    add.add_argument(
        "--description",
        required=True,
        help="Detailed backlog description",
    )
    add.add_argument(
        "--source-type",
        choices=[value.value for value in m.BacklogSourceType],
        default=m.BacklogSourceType.HUMAN.value,
        help="Candidate source type",
    )
    add.add_argument(
        "--source-ref",
        action="append",
        default=[],
        help="Source reference; repeat for multiple values",
    )
    add.add_argument(
        "--priority",
        choices=[value.value for value in m.BacklogPriority],
        default=m.BacklogPriority.NORMAL.value,
    )
    add.add_argument(
        "--risk",
        choices=[value.value for value in m.BacklogRiskLevel],
        default=m.BacklogRiskLevel.MEDIUM.value,
    )
    add.add_argument(
        "--depends-on",
        action="append",
        default=[],
        help="Dependency item ID; repeatable",
    )
    add.add_argument(
        "--acceptance",
        action="append",
        default=[],
        help="Acceptance criterion; repeatable",
    )
    add.add_argument(
        "--capability",
        action="append",
        default=[],
        help="Required capability; repeatable",
    )
    add.add_argument(
        "--allow-path",
        action="append",
        default=[],
        help="Allowed repository path; repeatable",
    )
    add.add_argument(
        "--deny-path",
        action="append",
        default=[],
        help="Denied repository path; repeatable",
    )
    add.add_argument(
        "--execution-policy",
        default="",
        help="Execution policy identifier",
    )
    add.add_argument("--correlation-id", default="")
    add.add_argument("--idempotency-key", default="")
    add.set_defaults(
        _autonomous_backlog_handler=_cmd_add,
    )

    approve = sub.add_parser(
        "approve",
        help="Approve a candidate backlog item",
    )
    _add_transition_arguments(approve)
    approve.set_defaults(
        _autonomous_backlog_handler=_cmd_approve,
    )

    start = sub.add_parser(
        "start",
        aliases=["execute"],
        help="Start execution of an approved item",
    )
    _add_transition_arguments(start)
    start.set_defaults(
        _autonomous_backlog_handler=_cmd_start,
    )

    complete = sub.add_parser(
        "complete",
        help="Complete an executing backlog item",
    )
    _add_transition_arguments(complete)
    complete.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="Evidence reference; repeatable",
    )
    complete.set_defaults(
        _autonomous_backlog_handler=_cmd_complete,
    )

    return parser


def autonomous_backlog_command(
    args: argparse.Namespace,
) -> int:
    handler = getattr(
        args,
        "_autonomous_backlog_handler",
        None,
    )

    if handler is None:
        print(
            "autonomous-backlog: missing action",
            file=sys.stderr,
        )
        return 1

    return handler(args)

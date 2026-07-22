"""``hermes engineering-memory`` governed knowledge commands.

Structured Engineering Memory is separate from the existing ``hermes memory``
provider command. This command manages durable, project-scoped engineering
knowledge with explicit candidate, verification, rejection, supersession, and
archival lifecycle controls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_cli.engineering_memory import models as m
from hermes_cli.engineering_memory.service import EngineeringMemoryService


def _get_service() -> EngineeringMemoryService:
    return EngineeringMemoryService()


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _timestamp(value: Optional[int]) -> str:
    if value is None:
        return "(none)"
    return datetime.fromtimestamp(
        value,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")


def _memory_to_dict(memory: m.MemoryRecord) -> Dict[str, Any]:
    return memory.model_dump(mode="json")


def _event_to_dict(event: m.MemoryEvent) -> Dict[str, Any]:
    return event.model_dump(mode="json")


def _snapshot_to_dict(
    snapshot: m.MemorySnapshot,
) -> Dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    payload["integrity_hash"] = snapshot.integrity_hash()
    return payload


def _parse_memory_type(value: str) -> m.MemoryType:
    try:
        return m.MemoryType(value)
    except ValueError as exc:
        valid = ", ".join(item.value for item in m.MemoryType)
        raise ValueError(
            f"unknown memory type {value!r}; expected one of: {valid}"
        ) from exc


def _parse_memory_status(value: str) -> m.MemoryStatus:
    try:
        return m.MemoryStatus(value)
    except ValueError as exc:
        valid = ", ".join(item.value for item in m.MemoryStatus)
        raise ValueError(
            f"unknown memory status {value!r}; expected one of: {valid}"
        ) from exc


def _read_optional_text(
    inline_value: Optional[str],
    file_value: Optional[str],
) -> Optional[str]:
    if file_value:
        return Path(file_value).read_text(encoding="utf-8")
    return inline_value


def _provenance_from_args(
    args: argparse.Namespace,
) -> m.MemoryProvenance:
    metadata: Dict[str, Any] = {}

    if getattr(args, "metadata", None):
        try:
            parsed = json.loads(args.metadata)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid provenance metadata JSON: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                "provenance metadata JSON must be an object"
            )
        metadata = parsed

    return m.MemoryProvenance(
        source_type=m.MemorySourceType.HUMAN,
        source_ids=tuple(args.source_id or ()),
        evidence_refs=tuple(args.evidence or ()),
        captured_at=m._utc_now(),
        captured_by=getattr(args, "actor", None) or "cli",
        metadata=metadata,
    )


def _print_memory(memory: m.MemoryRecord) -> None:
    print(
        f"{memory.memory_id} "
        f"[{memory.memory_type.value}] "
        f"[{memory.status.value}]"
    )
    print(f"  project:    {memory.project_id}")
    print(f"  title:      {memory.title}")
    print(f"  summary:    {memory.summary}")

    if memory.body:
        print("  body:")
        for line in memory.body.splitlines():
            print(f"    {line}")

    if memory.confidence is not None:
        print(f"  confidence: {memory.confidence:.0%}")

    if memory.tags:
        print(f"  tags:       {', '.join(memory.tags)}")

    if memory.related_memory_ids:
        print(
            "  related:    "
            + ", ".join(memory.related_memory_ids)
        )

    print(
        f"  source:     "
        f"{memory.provenance.source_type.value}"
    )

    if memory.provenance.source_ids:
        print(
            "  source IDs: "
            + ", ".join(memory.provenance.source_ids)
        )

    if memory.provenance.evidence_refs:
        print(
            "  evidence:   "
            + ", ".join(memory.provenance.evidence_refs)
        )

    print(f"  created:    {_timestamp(memory.created_at)}")
    print(f"  updated:    {_timestamp(memory.updated_at)}")

    if memory.created_by:
        print(f"  created by: {memory.created_by}")

    if memory.reviewed_by:
        print(f"  reviewed by:{memory.reviewed_by}")

    if memory.reviewed_at is not None:
        print(f"  reviewed:   {_timestamp(memory.reviewed_at)}")

    if memory.review_note:
        print(f"  review note:{memory.review_note}")

    if memory.supersedes:
        print(
            "  supersedes: "
            + ", ".join(memory.supersedes)
        )

    if memory.superseded_by:
        print(f"  superseded by: {memory.superseded_by}")


def _cmd_create(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        body = _read_optional_text(
            args.body,
            args.body_file,
        )
        structured_payload = None

        if args.payload:
            structured_payload = json.loads(args.payload)
            if not isinstance(structured_payload, dict):
                raise ValueError(
                    "structured payload JSON must be an object"
                )

        memory = service.create_candidate(
            args.project,
            _parse_memory_type(args.type),
            args.title,
            args.summary,
            provenance=_provenance_from_args(args),
            body=body,
            structured_payload=structured_payload,
            confidence=args.confidence,
            tags=args.tag or [],
            related_memory_ids=args.related or [],
            created_by=args.actor or "cli",
            actor=args.actor or "cli",
            source_idempotency_key=args.idempotency_key,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"engineering-memory create: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        _print_json(_memory_to_dict(memory))
    else:
        print(
            f"memory: created candidate {memory.memory_id}"
        )
        _print_memory(memory)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        status = (
            _parse_memory_status(args.status)
            if args.status
            else None
        )
        memory_type = (
            _parse_memory_type(args.type)
            if args.type
            else None
        )
        memories = service.list_memories(
            args.project,
            status=status,
            memory_type=memory_type,
            include_inactive=(
                args.all or status is not None
            ),
        )
    except ValueError as exc:
        print(
            f"engineering-memory list: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        _print_json(
            {
                "project_id": args.project,
                "memories": [
                    _memory_to_dict(memory)
                    for memory in memories
                ],
            }
        )
        return 0

    if not memories:
        print("(no engineering memories)")
        return 0

    for memory in memories:
        confidence = (
            f" confidence={memory.confidence:.0%}"
            if memory.confidence is not None
            else ""
        )
        print(
            f"  {memory.memory_id} "
            f"[{memory.memory_type.value}] "
            f"[{memory.status.value}]"
            f"{confidence}"
        )
        print(f"    {memory.title}")

        if memory.tags:
            print(f"    tags: {', '.join(memory.tags)}")

    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    service = _get_service()
    memory = service.get_memory(
        args.project,
        args.memory,
    )

    if memory is None:
        print(
            f"engineering-memory show: no such memory "
            f"{args.memory!r} in project {args.project!r}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        _print_json(_memory_to_dict(memory))
    else:
        _print_memory(memory)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        memory = service.verify_memory(
            args.project,
            args.memory,
            reviewed_by=args.reviewer,
            review_note=args.note,
            confidence=args.confidence,
            actor=args.reviewer,
        )
    except ValueError as exc:
        print(
            f"engineering-memory verify: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        _print_json(_memory_to_dict(memory))
    else:
        print(f"memory: verified {memory.memory_id}")
        _print_memory(memory)
    return 0


def _cmd_reject(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        memory = service.reject_memory(
            args.project,
            args.memory,
            reviewed_by=args.reviewer,
            review_note=args.note,
            actor=args.reviewer,
        )
    except ValueError as exc:
        print(
            f"engineering-memory reject: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        _print_json(_memory_to_dict(memory))
    else:
        print(f"memory: rejected {memory.memory_id}")
        _print_memory(memory)
    return 0


def _cmd_supersede(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        body = _read_optional_text(
            args.body,
            args.body_file,
        )
        replacement = service.supersede_memory(
            args.project,
            args.memory,
            replacement_type=_parse_memory_type(args.type),
            replacement_title=args.title,
            replacement_summary=args.summary,
            replacement_provenance=_provenance_from_args(
                args
            ),
            replacement_body=body,
            replacement_confidence=args.confidence,
            replacement_tags=args.tag or [],
            replacement_created_by=args.actor or "cli",
            actor=args.actor or "cli",
            source_idempotency_key=args.idempotency_key,
        )
    except (OSError, ValueError) as exc:
        print(
            f"engineering-memory supersede: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        _print_json(_memory_to_dict(replacement))
    else:
        print(
            f"memory: {args.memory} superseded by "
            f"{replacement.memory_id}"
        )
        _print_memory(replacement)
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        memory = service.archive_memory(
            args.project,
            args.memory,
            actor=args.actor or "cli",
            archive_note=args.note,
        )
    except ValueError as exc:
        print(
            f"engineering-memory archive: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        _print_json(_memory_to_dict(memory))
    else:
        print(f"memory: archived {memory.memory_id}")
        _print_memory(memory)
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    service = _get_service()

    try:
        snapshot = service.build_snapshot(
            args.project,
            generated_by=args.actor or "cli",
        )
    except ValueError as exc:
        print(
            f"engineering-memory snapshot: {exc}",
            file=sys.stderr,
        )
        return 1

    payload = _snapshot_to_dict(snapshot)

    if args.output:
        path = Path(args.output)
        path.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"snapshot exported to {path}")
        return 0

    if args.json:
        _print_json(payload)
        return 0

    print(
        f"Engineering Memory Snapshot "
        f"v{snapshot.version} "
        f"[{snapshot.project_id}]"
    )
    print(f"  events:   {snapshot.event_count}")
    print(f"  memories: {len(snapshot.memories)}")
    print(f"  hash:     {snapshot.integrity_hash()}")
    print(f"  generated:{_timestamp(snapshot.generated_at)}")
    return 0


def _cmd_events(args: argparse.Namespace) -> int:
    service = _get_service()
    events = service.list_events(
        project_id=args.project,
        limit=args.limit,
    )

    if args.json:
        _print_json(
            {
                "events": [
                    _event_to_dict(event)
                    for event in events
                ]
            }
        )
        return 0

    if not events:
        print("(no engineering memory events)")
        return 0

    for event in events:
        print(
            f"  {event.sequence:>6} "
            f"{event.event_type.value} "
            f"[{event.project_id}] "
            f"{event.memory_id}"
        )

        if args.verbose:
            print(f"    event: {event.event_id}")
            print(f"    actor: {event.actor or '(none)'}")
            print(f"    time:  {_timestamp(event.timestamp)}")

    return 0


def _add_provenance_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--source-id",
        action="append",
        help="Provenance source identifier (repeatable)",
    )
    parser.add_argument(
        "--evidence",
        action="append",
        help="Evidence reference (repeatable)",
    )
    parser.add_argument(
        "--metadata",
        help="Provenance metadata as a JSON object",
    )
    parser.add_argument(
        "--actor",
        help="Actor creating the candidate (default: cli)",
    )


def build_engineering_memory_parser(
    parent_subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Attach the governed Engineering Memory command tree."""
    parser = parent_subparsers.add_parser(
        "engineering-memory",
        aliases=[
            "eng-memory",
            "engineering_memory",
        ],
        help="Governed structured engineering knowledge",
        description=(
            "Create and review durable project-scoped engineering "
            "knowledge. New records always begin as candidates and "
            "must be explicitly verified before being trusted."
        ),
    )
    sub = parser.add_subparsers(
        dest="engineering_memory_action"
    )

    p_create = sub.add_parser(
        "create",
        aliases=["add"],
        help="Create a candidate engineering memory",
    )
    p_create.add_argument("project", help="Project ID")
    p_create.add_argument(
        "type",
        help="Memory type",
    )
    p_create.add_argument("title", help="Memory title")
    p_create.add_argument("summary", help="Memory summary")
    p_create.add_argument("--body", help="Detailed body")
    p_create.add_argument(
        "--body-file",
        help="Read detailed body from a UTF-8 file",
    )
    p_create.add_argument(
        "--payload",
        help="Structured payload as a JSON object",
    )
    p_create.add_argument(
        "--confidence",
        type=float,
        help="Confidence from 0.0 to 1.0",
    )
    p_create.add_argument(
        "--tag",
        action="append",
        help="Tag (repeatable)",
    )
    p_create.add_argument(
        "--related",
        action="append",
        help="Related memory ID (repeatable)",
    )
    p_create.add_argument(
        "--idempotency-key",
        help="Source idempotency key",
    )
    p_create.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    _add_provenance_arguments(p_create)
    p_create.set_defaults(
        _engineering_memory_handler=_cmd_create
    )

    p_list = sub.add_parser(
        "list",
        aliases=["ls"],
        help="List project engineering memories",
    )
    p_list.add_argument("project", help="Project ID")
    p_list.add_argument(
        "--status",
        help="Filter by lifecycle status",
    )
    p_list.add_argument(
        "--type",
        help="Filter by memory type",
    )
    p_list.add_argument(
        "--all",
        action="store_true",
        help="Include rejected, superseded, and archived records",
    )
    p_list.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    p_list.set_defaults(
        _engineering_memory_handler=_cmd_list
    )

    p_show = sub.add_parser(
        "show",
        help="Show one engineering memory",
    )
    p_show.add_argument("project", help="Project ID")
    p_show.add_argument("memory", help="Memory ID")
    p_show.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    p_show.set_defaults(
        _engineering_memory_handler=_cmd_show
    )

    p_verify = sub.add_parser(
        "verify",
        help="Verify a candidate memory",
    )
    p_verify.add_argument("project", help="Project ID")
    p_verify.add_argument("memory", help="Memory ID")
    p_verify.add_argument(
        "--reviewer",
        required=True,
        help="Reviewer identity",
    )
    p_verify.add_argument(
        "--note",
        help="Optional review note",
    )
    p_verify.add_argument(
        "--confidence",
        type=float,
        help="Reviewed confidence from 0.0 to 1.0",
    )
    p_verify.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    p_verify.set_defaults(
        _engineering_memory_handler=_cmd_verify
    )

    p_reject = sub.add_parser(
        "reject",
        help="Reject a candidate memory",
    )
    p_reject.add_argument("project", help="Project ID")
    p_reject.add_argument("memory", help="Memory ID")
    p_reject.add_argument(
        "--reviewer",
        required=True,
        help="Reviewer identity",
    )
    p_reject.add_argument(
        "--note",
        required=True,
        help="Required rejection reason",
    )
    p_reject.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    p_reject.set_defaults(
        _engineering_memory_handler=_cmd_reject
    )

    p_supersede = sub.add_parser(
        "supersede",
        help="Replace a candidate or verified memory",
    )
    p_supersede.add_argument("project", help="Project ID")
    p_supersede.add_argument(
        "memory",
        help="Memory ID being superseded",
    )
    p_supersede.add_argument(
        "type",
        help="Replacement memory type",
    )
    p_supersede.add_argument(
        "title",
        help="Replacement title",
    )
    p_supersede.add_argument(
        "summary",
        help="Replacement summary",
    )
    p_supersede.add_argument("--body", help="Replacement body")
    p_supersede.add_argument(
        "--body-file",
        help="Read replacement body from a UTF-8 file",
    )
    p_supersede.add_argument(
        "--confidence",
        type=float,
        help="Replacement confidence from 0.0 to 1.0",
    )
    p_supersede.add_argument(
        "--tag",
        action="append",
        help="Replacement tag (repeatable)",
    )
    p_supersede.add_argument(
        "--idempotency-key",
        help="Replacement source idempotency key",
    )
    p_supersede.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    _add_provenance_arguments(p_supersede)
    p_supersede.set_defaults(
        _engineering_memory_handler=_cmd_supersede
    )

    p_archive = sub.add_parser(
        "archive",
        help="Archive a non-superseded memory",
    )
    p_archive.add_argument("project", help="Project ID")
    p_archive.add_argument("memory", help="Memory ID")
    p_archive.add_argument(
        "--note",
        help="Archive note",
    )
    p_archive.add_argument(
        "--actor",
        help="Actor archiving the record (default: cli)",
    )
    p_archive.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    p_archive.set_defaults(
        _engineering_memory_handler=_cmd_archive
    )

    p_snapshot = sub.add_parser(
        "snapshot",
        help="Build a deterministic project snapshot",
    )
    p_snapshot.add_argument("project", help="Project ID")
    p_snapshot.add_argument(
        "--actor",
        help="Snapshot generator identity",
    )
    p_snapshot.add_argument(
        "--output",
        help="Write JSON snapshot to a file",
    )
    p_snapshot.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    p_snapshot.set_defaults(
        _engineering_memory_handler=_cmd_snapshot
    )

    p_events = sub.add_parser(
        "events",
        aliases=["audit"],
        help="Show append-only memory lifecycle events",
    )
    p_events.add_argument(
        "project",
        nargs="?",
        help="Optional project ID",
    )
    p_events.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Newest events to return (default: 100)",
    )
    p_events.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show event identity, actor, and timestamp",
    )
    p_events.add_argument(
        "--json",
        action="store_true",
        help="Output JSON",
    )
    p_events.set_defaults(
        _engineering_memory_handler=_cmd_events
    )

    return parser


def engineering_memory_command(
    args: argparse.Namespace,
) -> int:
    handler = getattr(
        args,
        "_engineering_memory_handler",
        None,
    )
    if handler is None:
        print(
            "engineering-memory: missing action",
            file=sys.stderr,
        )
        return 1
    return handler(args)

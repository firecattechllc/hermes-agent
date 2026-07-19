"""``hermes mission-control`` CLI commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from hermes_cli.mission_control import models as m
from hermes_cli.mission_control.service import MissionControlService


def _get_service() -> MissionControlService:
    return MissionControlService()


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _event_to_dict(event: m.TelemetryEvent) -> Dict[str, Any]:
    return event.model_dump(mode="json")


def _snapshot_to_dict(snapshot: m.MissionControlSnapshot) -> Dict[str, Any]:
    data = snapshot.model_dump(mode="json")
    data["integrity_hash"] = snapshot.integrity_hash()
    return data


def _launch_rows(events: List[m.TelemetryEvent]) -> List[Dict[str, Any]]:
    launches: OrderedDict[str, Dict[str, Any]] = OrderedDict()
    for event in events:
        if event.event_type != "context_launch_imported":
            continue
        launch_id = event.launch_id or event.payload.get("launch_id") or ""
        if not launch_id:
            continue
        row = launches.setdefault(
            launch_id,
            {
                "launch_id": launch_id,
                "project_id": event.project_id,
                "task_id": event.task_id,
                "backlog_id": event.backlog_id,
                "first_sequence": event.sequence,
                "last_sequence": event.sequence,
                "status": None,
                "stage": None,
                "failure_reason": None,
            },
        )
        row.update(
            last_sequence=event.sequence,
            status=event.payload.get("status"),
            stage=event.payload.get("stage"),
            failure_reason=event.payload.get("failure_reason"),
        )
    return list(launches.values())


def _cmd_status(args: argparse.Namespace) -> int:
    service = _get_service()
    project_ids = service.list_project_ids()
    rows = [
        {"project_id": pid, "event_count": service.event_count(pid)}
        for pid in project_ids
    ]
    total_events = sum(row["event_count"] for row in rows)
    if args.json:
        _print_json({
            "project_count": len(project_ids),
            "event_count": total_events,
            "projects": rows,
        })
        return 0
    print("Mission Control")
    print(f"  projects: {len(project_ids)}")
    print(f"  events:   {total_events}")
    return 0


def _cmd_projects(args: argparse.Namespace) -> int:
    service = _get_service()
    rows = [
        {"project_id": pid, "event_count": service.event_count(pid)}
        for pid in service.list_project_ids()
    ]
    if args.json:
        _print_json({"projects": rows})
        return 0
    if not rows:
        print("(no Mission Control projects)")
        return 0
    for row in rows:
        print(f"  {row['project_id']}  events={row['event_count']}")
    return 0


def _cmd_launches(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        rows = _launch_rows(service.get_events(args.project))
    except ValueError as exc:
        print(f"mission-control launches: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print_json({"project_id": args.project, "launches": rows})
        return 0
    if not rows:
        print("(no launches)")
        return 0
    for row in rows:
        status = row["status"] or "unknown"
        stage = row["stage"] or "unknown"
        print(f"  {row['launch_id']}  status={status} stage={stage}")
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        snapshot = service.get_snapshot(args.project, generated_by="cli")
    except ValueError as exc:
        print(f"mission-control snapshot: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(_snapshot_to_dict(snapshot))
        return 0
    print(f"Snapshot v{snapshot.version} for {snapshot.project_id}")
    print(f"  events:     {snapshot.event_count}")
    print(f"  agents:     {len(snapshot.agent_states)}")
    print(f"  backlog:    {len(snapshot.backlog_states)}")
    print(f"  approvals:  {len(snapshot.approval_states)}")
    print(f"  evidence:   {len(snapshot.evidence_states)}")
    print(f"  promotions: {len(snapshot.promotion_states)}")
    print(f"  hash:       {snapshot.integrity_hash()}")
    return 0


def _cmd_events(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        events = service.get_events(args.project) if args.project else [
            event
            for pid in service.list_project_ids()
            for event in service.get_events(pid)
        ]
    except ValueError as exc:
        print(f"mission-control events: {exc}", file=sys.stderr)
        return 1
    if args.limit is not None:
        events = events[-args.limit:]
    if args.json:
        _print_json({"events": [_event_to_dict(event) for event in events]})
        return 0
    if not events:
        print("(no events)")
        return 0
    for event in events:
        print(
            f"  {event.sequence:>6}  {event.event_type}"
            f"  [{event.project_id}]  {event.event_id}"
        )
    return 0


def build_mission_control_parser(
    parent_subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = parent_subparsers.add_parser(
        "mission-control",
        aliases=["mission_control"],
        help="Mission Control telemetry visibility",
        description="Read Mission Control telemetry, launches, snapshots, and events.",
    )
    sub = parser.add_subparsers(dest="mission_control_action")

    p_status = sub.add_parser("status", help="Show Mission Control status")
    p_status.add_argument("--json", action="store_true", help="Output JSON")
    p_status.set_defaults(_mission_control_handler=_cmd_status)

    p_projects = sub.add_parser("projects", aliases=["project", "list"], help="List projects")
    p_projects.add_argument("--json", action="store_true", help="Output JSON")
    p_projects.set_defaults(_mission_control_handler=_cmd_projects)

    p_launches = sub.add_parser("launches", help="List launch telemetry")
    p_launches.add_argument("project", help="Project ID")
    p_launches.add_argument("--json", action="store_true", help="Output JSON")
    p_launches.set_defaults(_mission_control_handler=_cmd_launches)

    p_snapshot = sub.add_parser("snapshot", help="Show a project snapshot")
    p_snapshot.add_argument("project", help="Project ID")
    p_snapshot.add_argument("--json", action="store_true", help="Output JSON")
    p_snapshot.set_defaults(_mission_control_handler=_cmd_snapshot)

    p_events = sub.add_parser("events", help="List telemetry events")
    p_events.add_argument("project", nargs="?", help="Project ID")
    p_events.add_argument("--limit", type=int, default=None, help="Limit returned events")
    p_events.add_argument("--json", action="store_true", help="Output JSON")
    p_events.set_defaults(_mission_control_handler=_cmd_events)

    return parser


def mission_control_command(args: argparse.Namespace) -> int:
    handler = getattr(args, "_mission_control_handler", None)
    if handler is None:
        print("mission-control: missing action", file=sys.stderr)
        return 1
    return handler(args)

"""``hermes context`` CLI — Shared Engineering Context commands.

Implements:
  hermes context project add
  hermes context project show
  hermes context record add
  hermes context record list
  hermes context record supersede
  hermes context snapshot show
  hermes context snapshot export
  hermes context audit show
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from hermes_cli.context_engine import models as m
from hermes_cli.context_engine.renderer import (
    export_json,
    render_context,
)
from hermes_cli.context_engine.service import ContextService


# ── Service factory ───────────────────────────────────────────────────────────

def _get_service() -> ContextService:
    from hermes_cli.context_engine.store import ContextStore
    return ContextService(store=ContextStore())


# ── Project commands ─────────────────────────────────────────────────────────

def _cmd_project_add(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        proj = service.register_project(
            display_name=args.display_name,
            project_id=args.project_id or None,
            repository_identity=args.repository or None,
            local_path=args.local_path or None,
            default_branch=args.default_branch or None,
            actor="cli",
        )
        print(f"project: registered {proj.project_id}")
        print(f"  name:   {proj.display_name}")
        print(f"  repo:   {proj.repository_identity or '(none)'}")
        print(f"  path:   {proj.local_path or '(none)'}")
        return 0
    except ValueError as exc:
        print(f"project add: {exc}", file=sys.stderr)
        return 1


def _cmd_project_show(args: argparse.Namespace) -> int:
    service = _get_service()
    proj = service.get_project(args.project)
    if proj is None:
        print(f"project: no such project: {args.project}", file=sys.stderr)
        return 1
    status_marker = " [archived]" if proj.status == m.ProjectStatus.ARCHIVED else ""
    print(f"{proj.display_name}  [{proj.project_id}]{status_marker}")
    print(f"  repo:     {proj.repository_identity or '(none)'}")
    print(f"  path:     {proj.local_path or '(none)'}")
    print(f"  branch:   {proj.default_branch or '(unknown)'}")
    print(f"  status:   {proj.status.value}")
    print(f"  created: {m.format_timestamp(proj.created_at)}")
    print(f"  updated: {m.format_timestamp(proj.updated_at)}")
    if proj.metadata:
        print(f"  metadata: {json.dumps(proj.metadata)}")
    return 0


def _cmd_project_list(args: argparse.Namespace) -> int:
    service = _get_service()
    projects = service.list_projects()
    if not projects:
        print("(no projects registered)")
        return 0
    for proj in projects:
        arch = " [archived]" if proj.status == m.ProjectStatus.ARCHIVED else ""
        print(f"  {proj.project_id}  {proj.display_name}{arch}")
    return 0


def _cmd_project_archive(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        proj = service.archive_project(args.project, actor="cli")
        print(f"project: archived {proj.project_id}")
    except ValueError as exc:
        print(f"project archive: {exc}", file=sys.stderr)
        return 1
    return 0


# ── Record commands ───────────────────────────────────────────────────────────

def _cmd_record_add(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        record_type = m.parse_record_type(args.type)
    except ValueError:
        print(f"record add: unknown record type: {args.type!r}", file=sys.stderr)
        return 1

    body: Optional[str] = None
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body:
        body = args.body

    source_refs: List[m.SourceReference] = []
    if args.source:
        for src in args.source:
            parts = src.split(":", 1)
            if len(parts) == 2:
                source_refs.append(m.SourceReference(
                    source_type=parts[0],
                    source_identifier=parts[1],
                ))
            else:
                source_refs.append(m.SourceReference(
                    source_type="manual",
                    source_identifier=src,
                ))

    try:
        record = service.add_record(
            project_id=args.project,
            record_type=record_type,
            title=args.title,
            body=body,
            confidence=args.confidence if hasattr(args, "confidence") and args.confidence else None,
            tags=args.tag or [],
            source_refs=source_refs or None,
            created_by="cli",
        )
        print(f"record: added {record.record_id} ({record.record_type.value})")
        print(f"  project: {record.project_id}")
        print(f"  title:   {record.title}")
        return 0
    except ValueError as exc:
        print(f"record add: {exc}", file=sys.stderr)
        return 1


def _cmd_record_list(args: argparse.Namespace) -> int:
    service = _get_service()
    record_type: Optional[m.RecordType] = None
    if args.type:
        try:
            record_type = m.parse_record_type(args.type)
        except ValueError:
            print(f"record list: unknown record type: {args.type!r}", file=sys.stderr)
            return 1

    status: Optional[m.RecordStatus] = None
    if args.status:
        try:
            status = m.parse_record_status(args.status)
        except ValueError:
            print(f"record list: unknown status: {args.status!r}", file=sys.stderr)
            return 1

    include_inactive = args.all or args.status is not None

    try:
        records = service.list_records(
            args.project,
            record_type=record_type,
            status=status,
            include_inactive=include_inactive,
        )
    except ValueError as exc:
        print(f"record list: {exc}", file=sys.stderr)
        return 1

    if not records:
        print("(no records)")
        return 0

    for rec in records:
        status_flag = ""
        if rec.status != m.RecordStatus.ACTIVE:
            status_flag = f" [{rec.status.value}]"
        conf = f" conf={rec.confidence:.0%}" if rec.confidence is not None else ""
        print(f"  {rec.record_id}  [{rec.record_type.value}]{status_flag}{conf}")
        print(f"    {rec.title}")
        if rec.tags:
            print(f"    tags: {', '.join(rec.tags)}")
        if rec.supersedes:
            print(f"    supersedes: {', '.join(rec.supersedes)}")
    return 0


def _cmd_record_show(args: argparse.Namespace) -> int:
    service = _get_service()
    record = service.get_record(args.project, args.record)
    if record is None:
        print(f"record: no such record: {args.record}", file=sys.stderr)
        return 1
    print(f"{record.record_id}  [{record.record_type.value}]")
    print(f"  title:   {record.title}")
    if record.body:
        print(f"  body:")
        for line in record.body.splitlines():
            print(f"    {line}")
    if record.status != m.RecordStatus.ACTIVE:
        print(f"  status:  {record.status.value}")
    if record.confidence is not None:
        print(f"  confidence: {record.confidence:.0%}")
    if record.tags:
        print(f"  tags:    {', '.join(record.tags)}")
    if record.source_refs:
        print("  sources:")
        for sr in record.source_refs:
            print(f"    [{sr.source_type}] {sr.source_identifier}")
    if record.supersedes:
        print(f"  supersedes: {', '.join(record.supersedes)}")
    print(f"  created: {m.format_timestamp(record.created_at)}")
    print(f"  updated: {m.format_timestamp(record.updated_at)}")
    return 0


def _cmd_record_supersede(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        new_record = service.add_record(
            project_id=args.project,
            record_type=m.parse_record_type(args.new_type),
            title=args.new_title,
            body=args.new_body,
            tags=args.tag or [],
            created_by="cli",
        )
        updated = service.supersede_record(
            project_id=args.project,
            record_id=args.record,
            new_record=new_record,
            actor="cli",
        )
        print(f"record: {args.record} superseded by {updated.record_id}")
        return 0
    except ValueError as exc:
        print(f"record supersede: {exc}", file=sys.stderr)
        return 1


# ── Snapshot commands ────────────────────────────────────────────────────────

def _cmd_snapshot_show(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        snapshot = service.build_snapshot(args.project, generated_by="cli")
    except ValueError as exc:
        print(f"snapshot show: {exc}", file=sys.stderr)
        return 1

    pkg = render_context(snapshot, role=args.role or "all")

    if args.markdown:
        print(pkg.to_markdown())
    else:
        print(f"Snapshot v{snapshot.version} for {snapshot.project_id}")
        print(f"  records:  {len(snapshot.records)}")
        print(f"  launches: {len(snapshot.launches)}")
        print(f"  hash:     {snapshot.integrity_hash()[:16]}...")
        print(f"  generated: {m.format_timestamp(snapshot.generated_at)}")
    return 0


def _cmd_snapshot_export(args: argparse.Namespace) -> int:
    service = _get_service()
    try:
        snapshot = service.build_snapshot(args.project, generated_by="cli")
    except ValueError as exc:
        print(f"snapshot export: {exc}", file=sys.stderr)
        return 1

    pkg = render_context(snapshot, role=args.role or "all")

    if args.output:
        path = Path(args.output)
        if args.json:
            path.write_text(export_json(pkg), encoding="utf-8")
        else:
            path.write_text(pkg.to_markdown(), encoding="utf-8")
        print(f"snapshot exported to {path}")
    else:
        if args.json:
            print(export_json(pkg))
        else:
            print(pkg.to_markdown())
    return 0


# ── Audit commands ────────────────────────────────────────────────────────────

def _cmd_audit_show(args: argparse.Namespace) -> int:
    service = _get_service()
    events = service.list_events(project_id=args.project, limit=args.limit)
    if not events:
        print("(no audit events)")
        return 0
    for evt in events:
        print(
            f"  {evt.event_id}  {evt.event_type}"
            f"  [{evt.project_id}]"
            f"  {m.format_timestamp(evt.timestamp)}"
        )
        if args.verbose and evt.actor:
            print(f"    actor: {evt.actor}")
        if args.verbose and evt.payload:
            print(f"    payload: {json.dumps(evt.payload)}")
    return 0


# ── Parser builder ────────────────────────────────────────────────────────────

def build_context_parser(
    parent_subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Attach the ``context`` subcommand tree."""
    parser = parent_subparsers.add_parser(
        "context",
        help="Shared Engineering Context",
        description=(
            "Project-scoped engineering context: objectives, roadmap, "
            "architecture decisions, risks, blockers, lessons, launches, "
            "and audit history."
        ),
    )
    sub = parser.add_subparsers(dest="context_action")

    # ── project subcommand ─────────────────────────────────────────────────
    proj_parser = sub.add_parser(
        "project", help="Project management", aliases=["proj"]
    )
    proj_sub = proj_parser.add_subparsers(dest="project_action")

    p_add = proj_sub.add_parser("add", help="Register a project")
    p_add.add_argument("display_name", help="Human-readable project name")
    p_add.add_argument("--project-id", dest="project_id", help="Explicit ID")
    p_add.add_argument("--repository", dest="repository", help="Git remote or identity")
    p_add.add_argument("--local-path", dest="local_path", help="Local path")
    p_add.add_argument("--default-branch", dest="default_branch", help="Default branch")

    p_show = proj_sub.add_parser("show", help="Show a project's details")
    p_show.add_argument("project", help="Project ID or slug")

    p_list = proj_sub.add_parser("list", aliases=["ls"], help="List all projects")

    p_archive = proj_sub.add_parser("archive", help="Archive a project")
    p_archive.add_argument("project", help="Project ID or slug")

    proj_parser.set_defaults(
        _project_add=_cmd_project_add,
        _project_show=_cmd_project_show,
        _project_list=_cmd_project_list,
        _project_archive=_cmd_project_archive,
    )

    # ── record subcommand ──────────────────────────────────────────────────
    rec_parser = sub.add_parser(
        "record", help="Context record management", aliases=["rec"]
    )
    rec_sub = rec_parser.add_subparsers(dest="record_action")

    p_rec_add = rec_sub.add_parser("add", help="Add a context record")
    p_rec_add.add_argument("project", help="Project ID")
    p_rec_add.add_argument("type", help="Record type (see types)")
    p_rec_add.add_argument("title", help="Record title")
    p_rec_add.add_argument("--body", dest="body", help="Record body text")
    p_rec_add.add_argument(
        "--body-file", dest="body_file",
        help="Read body from file",
    )
    p_rec_add.add_argument(
        "--tag", dest="tag", action="append",
        help="Tag (repeatable)",
    )
    p_rec_add.add_argument(
        "--source", dest="source", action="append",
        help="Source as 'type:identifier' (repeatable)",
    )
    p_rec_add.add_argument(
        "--confidence", dest="confidence", type=float,
        help="Confidence 0.0-1.0",
    )

    p_rec_list = rec_sub.add_parser("list", aliases=["ls"], help="List records")
    p_rec_list.add_argument("project", help="Project ID")
    p_rec_list.add_argument("--type", dest="type", help="Filter by type")
    p_rec_list.add_argument(
        "--status", dest="status",
        help="Filter by status (active|resolved|deprecated|invalidated)",
    )
    p_rec_list.add_argument(
        "--all", dest="all", action="store_true",
        help="Include inactive records",
    )

    p_rec_show = rec_sub.add_parser("show", help="Show a record's details")
    p_rec_show.add_argument("project", help="Project ID")
    p_rec_show.add_argument("record", help="Record ID")

    p_rec_supersede = rec_sub.add_parser(
        "supersede", help="Supersede a record with a new one"
    )
    p_rec_supersede.add_argument("project", help="Project ID")
    p_rec_supersede.add_argument("record", help="Record ID to supersede")
    p_rec_supersede.add_argument("new_type", help="New record type")
    p_rec_supersede.add_argument("new_title", help="New record title")
    p_rec_supersede.add_argument("--body", dest="new_body", help="New record body")
    p_rec_supersede.add_argument(
        "--tag", dest="tag", action="append",
        help="Tag (repeatable)",
    )

    rec_parser.set_defaults(
        _record_add=_cmd_record_add,
        _record_list=_cmd_record_list,
        _record_show=_cmd_record_show,
        _record_supersede=_cmd_record_supersede,
    )

    # ── snapshot subcommand ────────────────────────────────────────────────
    snap_parser = sub.add_parser(
        "snapshot", help="Snapshot and export"
    )
    snap_sub = snap_parser.add_subparsers(dest="snapshot_action")

    p_snap_show = snap_sub.add_parser("show", help="Show a project snapshot")
    p_snap_show.add_argument("project", help="Project ID")
    p_snap_show.add_argument(
        "--role", dest="role",
        help="Agent role (planner|builder|reviewer|security|documentation|release|all)",
    )
    p_snap_show.add_argument(
        "--markdown", action="store_true",
        help="Render as Markdown",
    )

    p_snap_export = snap_sub.add_parser("export", help="Export a snapshot")
    p_snap_export.add_argument("project", help="Project ID")
    p_snap_export.add_argument(
        "--output", dest="output",
        help="Output file (default: stdout)",
    )
    p_snap_export.add_argument(
        "--json", action="store_true",
        help="Export as JSON (default: Markdown)",
    )
    p_snap_export.add_argument(
        "--role", dest="role",
        help="Agent role (planner|builder|reviewer|security|documentation|release|all)",
    )

    snap_parser.set_defaults(
        _snapshot_show=_cmd_snapshot_show,
        _snapshot_export=_cmd_snapshot_export,
    )

    # ── audit subcommand ───────────────────────────────────────────────────
    p_audit = sub.add_parser("audit", help="Show audit log")
    p_audit.add_argument(
        "project", nargs="?", help="Project ID (omit for all projects)",
    )
    p_audit.add_argument(
        "--limit", type=int, default=100,
        help="Number of events to show (default: 100)",
    )
    p_audit.add_argument(
        "-v", "--verbose", dest="verbose",
        action="store_true",
        help="Show actor and payload",
    )
    p_audit.set_defaults(_audit_show=_cmd_audit_show)

    return parser


def context_command(args: argparse.Namespace) -> int:
    """Entry point from ``hermes context …`` argparse dispatch."""
    action = getattr(args, "context_action", None)
    if not action:
        return 0

    # Project subcommand dispatch.
    if action == "project":
        project_action = getattr(args, "project_action", None)
        dispatch = {
            "add": args._project_add,
            "show": args._project_show,
            "list": args._project_list,
            "ls": args._project_list,
            "archive": args._project_archive,
        }.get(project_action)
        if dispatch is None:
            if project_action:
                print(f"context project: unknown action: {project_action}", file=sys.stderr)
            return 1
        return dispatch(args)

    # Record subcommand dispatch.
    if action == "record" or action == "rec":
        record_action = getattr(args, "record_action", None)
        dispatch = {
            "add": args._record_add,
            "list": args._record_list,
            "ls": args._record_list,
            "show": args._record_show,
            "supersede": args._record_supersede,
        }.get(record_action)
        if dispatch is None:
            if record_action:
                print(f"context record: unknown action: {record_action}", file=sys.stderr)
            return 1
        return dispatch(args)

    # Snapshot subcommand dispatch.
    if action == "snapshot":
        snap_action = getattr(args, "snapshot_action", None)
        dispatch = {
            "show": args._snapshot_show,
            "export": args._snapshot_export,
        }.get(snap_action)
        if dispatch is None:
            if snap_action:
                print(f"context snapshot: unknown action: {snap_action}", file=sys.stderr)
            return 1
        return dispatch(args)

    # Audit.
    if action == "audit":
        return args._audit_show(args)

    print(f"context: unknown action: {action}", file=sys.stderr)
    return 1

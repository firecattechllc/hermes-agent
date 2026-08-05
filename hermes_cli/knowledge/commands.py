"""Operator CLI for the Step 33 knowledge service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .config import KnowledgeConfig
from .service import KnowledgeService


def _service(node: str | None = None) -> KnowledgeService:
    db = Path(
        os.environ.get(
            "HERMES_KNOWLEDGE_DB",
            str(Path("~/.hermes/knowledge/graph.sqlite3").expanduser()),
        )
    )
    return KnowledgeService(
        KnowledgeConfig(database_path=db, node_id=node or "mac-hermes")
    )


def _emit(value, as_json: bool) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, tuple):
        value = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in value
        ]
    print(json.dumps(value, indent=None if as_json else 2, sort_keys=True, default=str))


def knowledge_command(args: argparse.Namespace) -> int:
    try:
        service = _service(getattr(args, "node", None))
        command = args.knowledge_command
        if command == "discover":
            snapshot, changes = service.discover(args.collector)
            _emit(
                {
                    "snapshot": snapshot.model_dump(mode="json"),
                    "changes": [item.model_dump(mode="json") for item in changes],
                },
                args.json,
            )
        elif command == "status":
            _emit(service.status(), args.json)
        elif command == "entities":
            _emit(
                service.store.search_entities(
                    entity_type=args.type,
                    name=args.name,
                    label=args.label,
                    node_id=args.node,
                    status=args.status,
                ),
                args.json,
            )
        elif command == "show":
            entity = service.store.entity(args.entity_id)
            if entity is None:
                raise ValueError("entity not found")
            _emit(entity, args.json)
        elif command == "neighbors":
            entities, relationships = service.store.neighbors(
                args.entity_id, direction=args.direction, depth=args.depth
            )
            _emit(
                {
                    "entities": [item.model_dump(mode="json") for item in entities],
                    "relationships": [
                        item.model_dump(mode="json") for item in relationships
                    ],
                },
                args.json,
            )
        elif command == "changes":
            since = (
                None
                if args.since is None
                else datetime.fromisoformat(args.since.replace("Z", "+00:00"))
            )
            _emit(service.store.changes(since), args.json)
        elif command == "impact":
            _emit(service.impact(args.entity_id, args.scenario), args.json)
        elif command == "export":
            service.export(args.output, redacted=args.redacted)
            print(str(args.output))
        elif command == "collectors":
            _emit({"enabled": list(service.config.enabled_collectors)}, args.json)
        return 0
    except (OSError, ValueError) as exc:
        print(f"knowledge: {exc}", file=sys.stderr)
        return 2


def build_knowledge_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "knowledge", help="Evidence-backed whole-system knowledge graph"
    )
    commands = parser.add_subparsers(dest="knowledge_command", required=True)
    discover = commands.add_parser("discover")
    discover.add_argument("--collector", action="append")
    discover.add_argument("--node")
    discover.add_argument("--json", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    entities = commands.add_parser("entities")
    for flag in ("type", "name", "label", "node", "status"):
        entities.add_argument(f"--{flag}")
    entities.add_argument("--json", action="store_true")
    show = commands.add_parser("show")
    show.add_argument("entity_id")
    show.add_argument("--json", action="store_true")
    neighbors = commands.add_parser("neighbors")
    neighbors.add_argument("entity_id")
    neighbors.add_argument(
        "--direction", choices=("upstream", "downstream", "both"), default="both"
    )
    neighbors.add_argument("--depth", type=int, default=1)
    neighbors.add_argument("--json", action="store_true")
    changes = commands.add_parser("changes")
    changes.add_argument("--since")
    changes.add_argument("--json", action="store_true")
    impact = commands.add_parser("impact")
    impact.add_argument("entity_id")
    impact.add_argument(
        "--scenario", choices=("outage", "remove", "upgrade"), default="outage"
    )
    impact.add_argument("--json", action="store_true")
    export = commands.add_parser("export")
    export.add_argument("--redacted", action="store_true", required=True)
    export.add_argument("--output", type=Path, required=True)
    collectors = commands.add_parser("collectors")
    collectors.add_argument("--json", action="store_true")
    parser.set_defaults(func=knowledge_command)
    return parser

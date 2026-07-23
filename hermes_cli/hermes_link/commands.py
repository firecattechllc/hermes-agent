"""Small operator surface for the Mac-side link client."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .client import HermesLinkClient
from .models import HermesLinkEnvelope, MessageType, new_message_id


def _client() -> HermesLinkClient:
    base_url = os.environ.get("HERMES_LINK_TITAN_URL", "http://127.0.0.1:9320")
    token = os.environ.get("HERMES_LINK_TOKEN")
    if not token:
        raise ValueError("HERMES_LINK_TOKEN is not configured")
    return HermesLinkClient(base_url, token=token)


def _print(result, *, as_json: bool) -> int:
    if as_json:
        print(result.model_dump_json())
    elif result.ok:
        value = result.status or result.envelope or result.queue
        print(
            json.dumps(
                value.model_dump(mode="json")
                if hasattr(value, "model_dump")
                else [item.model_dump(mode="json") for item in value],
                indent=2,
            )
        )
    else:
        print(f"link: {result.error.code}: {result.error.message}", file=sys.stderr)
    return 0 if result.ok else 1


def link_command(args: argparse.Namespace) -> int:
    try:
        client = _client()
        if args.link_command == "status":
            return _print(client.fetch_status(), as_json=args.json)
        if args.link_command == "queue":
            return _print(client.list_queue(), as_json=args.json)
        if args.link_command == "chat":
            envelope = HermesLinkEnvelope(
                message_id=new_message_id(),
                correlation_id=args.correlation_id or new_message_id(),
                sender_node=args.sender,
                recipient_node=args.recipient,
                message_type=MessageType.CHAT,
                payload={"text": args.message},
            )
            return _print(client.send_chat(envelope), as_json=args.json)
        if args.link_command == "sync":
            # Milestone one exposes a bounded reachability/queue snapshot; it
            # does not invent automatic replay authority.
            status = client.fetch_status()
            if not status.ok:
                return _print(status, as_json=args.json)
            return _print(client.list_queue(), as_json=args.json)
        raise ValueError("a link command is required")
    except ValueError as exc:
        print(f"link: {exc}", file=sys.stderr)
        return 2


def build_link_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "link", help="Governed Mac / Titan Hermes communication"
    )
    commands = parser.add_subparsers(dest="link_command", required=True)
    for name in ("status", "queue", "sync"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true")
    chat = commands.add_parser("chat")
    chat.add_argument("message")
    chat.add_argument("--sender", default="mac-hermes")
    chat.add_argument("--recipient", default="titan-hermes")
    chat.add_argument("--correlation-id")
    chat.add_argument("--json", action="store_true")
    parser.set_defaults(func=link_command)
    return parser

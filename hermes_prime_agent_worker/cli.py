"""``python -m hermes_prime_agent_worker <command> [args...]``

Deliberately absent: any subcommand that clears the kill switch, edits the
systemd unit, or performs a git merge/tag/push/release. See
:class:`hermes_prime_agent_worker.sessions.PrimeAgentWorker`'s docstring.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from hermes_prime_agent_worker.config import (
    PrimeAgentWorkerConfig,
    PrimeAgentWorkerConfigError,
)
from hermes_prime_agent_worker.sessions import PrimeAgentWorker


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes_prime_agent_worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "validate-config", help="Load and validate configuration only"
    )
    subparsers.add_parser("status", help="Show background daemon status")

    doctor = subparsers.add_parser("doctor", help="Inspect background services")
    doctor.add_argument("--fix", action="store_true")
    doctor.add_argument("--approve-fix", action="store_true")

    list_cmd = subparsers.add_parser("list", help="List agents")
    list_cmd.add_argument("--all", action="store_true", dest="include_saved")

    run = subparsers.add_parser("run", help="Run a bounded governed task")
    run.add_argument("--workspace", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--approve-mutation", action="store_true")
    run.add_argument("--approve-network", action="store_true")
    run.add_argument("--tools", default="")
    run.add_argument("--gate", action="append", default=[])

    send = subparsers.add_parser("send", help="Send a message to an agent")
    send.add_argument("agent_id")
    send.add_argument("message")

    stop = subparsers.add_parser("stop", help="Stop one agent")
    stop.add_argument("agent_id")

    subparsers.add_parser(
        "shutdown", help="Stop every agent and the background service"
    )

    emergency = subparsers.add_parser("emergency-stop", help="Trip the kill switch")
    emergency.add_argument("--reason", required=True)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = PrimeAgentWorkerConfig.from_env()
    except PrimeAgentWorkerConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.command == "validate-config":
        print(json.dumps({"valid": True}))
        return 0

    worker = PrimeAgentWorker(config)

    if args.command == "status":
        snapshots = worker.status()
        print(json.dumps([dataclasses.asdict(s) for s in snapshots], default=str))
        return 0

    if args.command == "doctor":
        result = worker.doctor(fix=args.fix, fix_approved=args.approve_fix)
        print(json.dumps(result, default=str))
        return 0 if result.get("permitted", True) else 3

    if args.command == "list":
        result = worker.list_sessions(include_saved=args.include_saved)
        print(json.dumps(result, default=str))
        return 0

    if args.command == "run":
        requested_tools = tuple(t.strip() for t in args.tools.split(",") if t.strip())
        result = worker.run_task(
            workspace=Path(args.workspace),
            task_text=args.task,
            mutation_approved=args.approve_mutation,
            network_approved=args.approve_network,
            requested_tools=requested_tools,
            gate_commands=tuple(args.gate),
        )
        print(
            json.dumps(
                {
                    "permitted": result.permitted,
                    "reason_codes": result.reason_codes,
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "truncated": result.truncated,
                    "duration_seconds": result.duration_seconds,
                    "correlation_id": result.correlation_id,
                },
                default=str,
            )
        )
        return 0 if result.permitted and result.returncode == 0 else 1

    if args.command == "send":
        result = worker.send(args.agent_id, args.message)
        print(json.dumps(result, default=str))
        return 0 if result.get("returncode") == 0 else 1

    if args.command == "stop":
        result = worker.stop(args.agent_id)
        print(json.dumps(result, default=str))
        return 0 if result.get("returncode") == 0 else 1

    if args.command == "shutdown":
        result = worker.shutdown(force=True)
        print(json.dumps(result, default=str))
        return 0

    if args.command == "emergency-stop":
        result = worker.emergency_stop(reason=args.reason)
        print(json.dumps(result, default=str))
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2

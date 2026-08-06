"""Command-line entry points.

::

    python -m hermes_docs_worker collect --dry-run
    python -m hermes_docs_worker daily --dry-run
    python -m hermes_docs_worker weekly --dry-run
    python -m hermes_docs_worker status
    python -m hermes_docs_worker validate-config

Every subcommand except ``status`` and ``validate-config`` accepts
``--dry-run``; without it, a real run may commit, push an automation
branch, and open a pull request (never merge one, never touch ``main``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional, Sequence

from hermes_docs_worker.config import DocsWorkerConfig, DocsWorkerConfigError
from hermes_docs_worker.evidence import EvidenceRetentionStore
from hermes_docs_worker.locking import AlreadyRunningError
from hermes_docs_worker.orchestrator import run_worker


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m hermes_docs_worker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("collect", "daily", "weekly"):
        sub = subparsers.add_parser(name, help=f"run a {name} documentation pass")
        sub.add_argument(
            "--dry-run", action="store_true",
            help="collect and generate Markdown, but never commit, push, or open a PR",
        )

    subparsers.add_parser("status", help="report worker/lock/last-run status (read-only)")
    subparsers.add_parser(
        "validate-config", help="validate HERMES_DOCS_WORKER_* configuration and exit"
    )
    return parser


def _cmd_validate_config() -> int:
    try:
        DocsWorkerConfig.from_env()
    except DocsWorkerConfigError as error:
        print(f"configuration invalid: {error}", file=sys.stderr)
        return 2
    print("configuration OK")
    return 0


def _cmd_status() -> int:
    try:
        config = DocsWorkerConfig.from_env()
    except DocsWorkerConfigError as error:
        print(f"configuration invalid: {error}", file=sys.stderr)
        return 2

    lock_path = config.state_dir / "run.lock"
    print(f"docs repo:        {config.docs_repo_path}")
    print(f"hermes source:    {config.hermes_source_dir}")
    print(f"state dir:        {config.state_dir}")
    print(f"github repo:      {config.github_repo}")
    print(f"lock file exists: {lock_path.exists()}")

    store = EvidenceRetentionStore(config.state_dir)
    latest = store.latest()
    if latest is None:
        print("last evidence run: none recorded")
    else:
        print(
            f"last evidence run: {latest.run_id} at {latest.collected_at} "
            f"({len(latest.facts)} facts, {len(latest.collector_errors)} collector errors)"
        )
    return 0


def _cmd_run(mode: str, dry_run: bool) -> int:
    try:
        config = DocsWorkerConfig.from_env()
    except DocsWorkerConfigError as error:
        print(f"configuration invalid: {error}", file=sys.stderr)
        return 2

    try:
        result = run_worker(config, mode=mode, dry_run=dry_run)
    except AlreadyRunningError as error:
        print(str(error))
        return 0

    print(result.summary())
    if result.errors:
        print("collector errors:")
        for error in result.errors:
            print(f"  - {error}")
    if result.broken_wikilinks:
        print("broken wiki-links:")
        for path, missing in result.broken_wikilinks.items():
            print(f"  - {path}: {', '.join(missing)}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    if args.command == "validate-config":
        return _cmd_validate_config()
    if args.command == "status":
        return _cmd_status()
    if args.command in ("collect", "daily", "weekly"):
        return _cmd_run(args.command, args.dry_run)

    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - argparse.error exits before this


if __name__ == "__main__":
    sys.exit(main())

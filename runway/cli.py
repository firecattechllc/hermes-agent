"""Runway command-line entry point.

Deliberately minimal and standalone: no dependency on ``hermes_cli``, and no
console-script entry point was added to ``pyproject.toml`` in this change
(kept out of scope -- see docs/architecture/PROJECT_RUNWAY_FOUNDATION.md).
Invoke as::

    python -m runway.cli credentials edit
    python -m runway.cli credentials status --var RUNWAY_SOME_PROVIDER_API_KEY

Every command here is safe to run with no credentials configured, performs
no network I/O, and never prints a credential value -- see
:mod:`runway.providers.credentials` for the resolver these commands drive.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from runway.providers.credentials import (
    DEFAULT_PROVIDERS_ENV_PATH,
    CredentialResolutionError,
    FileCredentialResolver,
    ensure_providers_env,
)


def _resolve_editor() -> str:
    for var in ("EDITOR", "VISUAL"):
        editor = os.environ.get(var, "").strip()
        if editor:
            return editor
    for fallback in ("nano", "vi"):
        found = shutil.which(fallback)
        if found:
            return found
    raise RuntimeError("no editor found -- set $EDITOR (or $VISUAL), or install nano/vi")


def cmd_credentials_edit(args: argparse.Namespace) -> int:
    path = ensure_providers_env(args.path)
    try:
        editor = _resolve_editor()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Opening {path} in {editor!r}. Contents are never printed by this command.")
    subprocess.run([editor, str(path)], check=False)
    # Editors sometimes recreate the file (e.g. atomic-save-via-rename),
    # which can reset permissions to whatever the umask allows -- reassert.
    ensure_providers_env(path)
    return 0


def cmd_credentials_status(args: argparse.Namespace) -> int:
    path = args.path or DEFAULT_PROVIDERS_ENV_PATH
    names = _variable_names(args)
    if not names:
        print(
            "No credential names to check. Pass one or more --var NAME, or "
            "--config path/to/gateway.yaml to source names from a channel registry."
        )
        return 0

    resolver = FileCredentialResolver(path)
    try:
        results = resolver.status(names)
    except CredentialResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for name in names:
        state = "CONFIGURED" if results.get(name) else "MISSING"
        print(f"{name}: {state}")
    return 0


def _variable_names(args: argparse.Namespace) -> list[str]:
    names: list[str] = list(dict.fromkeys(args.names or []))  # de-dupe, preserve order
    if args.config:
        from runway.providers.gateway.config_loader import load_channel_registry

        registry = load_channel_registry(Path(args.config))
        for channel in registry.channels:
            if channel.credential_ref.startswith("file:"):
                var_name = channel.credential_ref[len("file:"):]
                if var_name not in names:
                    names.append(var_name)
    return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runway", description="Project Runway local utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    credentials = subparsers.add_parser("credentials", help="Manage local provider credentials")
    credentials_sub = credentials.add_subparsers(dest="subcommand", required=True)

    edit = credentials_sub.add_parser(
        "edit", help="Create (if missing) and open the local providers.env in $EDITOR"
    )
    edit.add_argument("--path", type=Path, default=None, help=f"Override the default path ({DEFAULT_PROVIDERS_ENV_PATH})")
    edit.set_defaults(func=cmd_credentials_edit)

    status = credentials_sub.add_parser(
        "status", help="Report CONFIGURED/MISSING per credential name -- never prints values"
    )
    status.add_argument("--path", type=Path, default=None, help=f"Override the default path ({DEFAULT_PROVIDERS_ENV_PATH})")
    status.add_argument("--config", type=str, default=None, help="Gateway YAML config to source credential_ref names from")
    status.add_argument("--var", dest="names", action="append", default=None, help="Variable name to check (repeatable)")
    status.set_defaults(func=cmd_credentials_status)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

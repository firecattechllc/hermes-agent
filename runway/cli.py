"""Runway command-line entry point.

Deliberately minimal and standalone: no dependency on ``hermes_cli``, and no
console-script entry point was added to ``pyproject.toml`` in this change
(kept out of scope -- see docs/architecture/PROJECT_RUNWAY_FOUNDATION.md).
Invoke as::

    python -m runway.cli credentials edit
    python -m runway.cli credentials status --var RUNWAY_SOME_PROVIDER_API_KEY
    python -m runway.cli credentials desktop-template --var RUNWAY_SOME_PROVIDER_API_KEY
    python -m runway.cli credentials import ~/Desktop/Runway-Provider-Keys.env

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
    DEFAULT_DESKTOP_TEMPLATE_PATH,
    DEFAULT_PROVIDERS_ENV_PATH,
    CredentialResolutionError,
    FileCredentialResolver,
    ensure_providers_env,
    import_desktop_template,
    write_desktop_template,
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


def cmd_credentials_desktop_template(args: argparse.Namespace) -> int:
    path = args.path or DEFAULT_DESKTOP_TEMPLATE_PATH
    names = _variable_names(args)
    result_path = write_desktop_template(path, names)
    print(f"Wrote {result_path} ({len(names)} name(s) requested; contents are never printed by this command).")
    print("This file is NOT read by Runway directly -- fill it in, then run:")
    print(f"  python -m runway.cli credentials import {result_path}")
    return 0


def cmd_credentials_import(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser()
    target = args.target or DEFAULT_PROVIDERS_ENV_PATH
    try:
        summary = import_desktop_template(source, target)
    except CredentialResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not (summary.added or summary.updated or summary.skipped_empty):
        print(f"No entries found in {source}. Nothing imported.")
        return 0
    for name in summary.added:
        print(f"{name}: added")
    for name in summary.updated:
        print(f"{name}: updated")
    for name in summary.skipped_empty:
        print(f"{name}: skipped (blank in source -- did not overwrite any existing value)")
    print(f"Imported into {target}. Consider deleting {source} now that its values are stored there.")
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

    desktop_template = credentials_sub.add_parser(
        "desktop-template",
        help="Create/refresh a manual-editing-convenience key sheet on the Desktop (never read by Runway directly)",
    )
    desktop_template.add_argument(
        "--path", type=Path, default=None, help=f"Override the default path ({DEFAULT_DESKTOP_TEMPLATE_PATH})"
    )
    desktop_template.add_argument(
        "--config", type=str, default=None, help="Gateway YAML config to source credential_ref names from"
    )
    desktop_template.add_argument(
        "--var", dest="names", action="append", default=None, help="Variable name to include (repeatable)"
    )
    desktop_template.set_defaults(func=cmd_credentials_desktop_template)

    import_cmd = credentials_sub.add_parser(
        "import", help="Import a KEY=value file (e.g. the Desktop template) into the authoritative providers.env"
    )
    import_cmd.add_argument("source", type=str, help="Path to the file to import (e.g. ~/Desktop/Runway-Provider-Keys.env)")
    import_cmd.add_argument(
        "--target", type=Path, default=None, help=f"Override the authoritative store path ({DEFAULT_PROVIDERS_ENV_PATH})"
    )
    import_cmd.set_defaults(func=cmd_credentials_import)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

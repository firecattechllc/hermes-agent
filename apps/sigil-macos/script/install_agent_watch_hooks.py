#!/usr/bin/env python3
"""Conservatively merge Sigil Agent Watch hooks into Claude Code and Codex."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import tempfile

CLAUDE_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
    "Notification", "PostToolUse", "PostToolUseFailure", "Stop",
    "TaskCompleted", "SessionEnd",
)
CODEX_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
    "PostToolUse", "Stop", "SessionEnd",
)


def load_object(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"refusing to replace non-object configuration: {path}")
    return value


def merge_hooks(config: dict, events: tuple[str, ...], command: str) -> bool:
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("refusing to replace non-object hooks configuration")
    changed = False
    for event in events:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"refusing to replace non-list hook event: {event}")
        already_present = any(
            isinstance(group, dict)
            and any(
                isinstance(hook, dict) and hook.get("command") == command
                for hook in group.get("hooks", [])
            )
            for group in groups
        )
        if not already_present:
            groups.append({"hooks": [{"type": "command", "command": command}]})
            changed = True
    return changed


def atomic_write(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true", help="write merged configuration")
    parser.add_argument("--home", type=pathlib.Path, default=pathlib.Path.home(), help=argparse.SUPPRESS)
    args = parser.parse_args()

    project = pathlib.Path(__file__).resolve().parents[1]
    support = project / "Sigil/Sigil/Support"
    targets = (
        (args.home / ".claude/settings.json", CLAUDE_EVENTS, support / "agent_watch_claude_emitter.py"),
        (args.home / ".codex/hooks.json", CODEX_EVENTS, support / "agent_watch_codex_emitter.py"),
    )
    for path, events, emitter in targets:
        if not emitter.is_file():
            raise FileNotFoundError(f"hook emitter missing: {emitter}")
        command = f"/usr/bin/python3 {shlex.quote(str(emitter))}"
        config = load_object(path)
        changed = merge_hooks(config, events, command)
        if args.install and changed:
            atomic_write(path, config)
        action = "updated" if args.install and changed else "unchanged" if not changed else "would update"
        print(f"{path}: {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

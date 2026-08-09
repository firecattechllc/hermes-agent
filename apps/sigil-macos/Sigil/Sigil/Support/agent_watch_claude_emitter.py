#!/usr/bin/env python3
"""Claude Code hook adapter that persists only Agent Watch lifecycle metadata."""

from __future__ import annotations

import ctypes
import ctypes.util
import argparse
import datetime
import json
import os
import pathlib
import sys
import tempfile

from agent_watch_paths import evidence_directory

EVENT_MAP = {
    "SessionStart": "sessionStarted",
    "UserPromptSubmit": "beganWork",
    "PreToolUse": "beganWork",
    "PostToolUse": "beganWork",
    "PostToolUseFailure": "beganWork",
    "PermissionRequest": "approvalRequired",
    "Stop": "completed",
    "StopFailure": "failed",
    "TaskCompleted": "completed",
    "SessionEnd": "ended",
}


class ProcBSDInfo(ctypes.Structure):
    _fields_ = [("_opaque", ctypes.c_byte * 136)]


def parent_pid(pid: int) -> int:
    libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    info = ProcBSDInfo()
    size = libproc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size <= 0:
        return 0
    # pbi_ppid is the seventh uint32 in proc_bsdinfo on Darwin.
    return ctypes.cast(ctypes.byref(info), ctypes.POINTER(ctypes.c_uint32))[6]


def process_path(pid: int) -> str:
    libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    buffer = ctypes.create_string_buffer(4096)
    if libproc.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        return ""
    return buffer.value.decode("utf-8", errors="ignore")


def claude_ancestor() -> int:
    pid = os.getppid()
    for _ in range(12):
        path = process_path(pid).lower()
        if pathlib.Path(path).name == "claude" or "/claude.app/" in path:
            return pid
        pid = parent_pid(pid)
        if pid <= 1:
            break
    return 0


def sanitized_record(payload: dict, pid: int, observed_at: str) -> dict | None:
    hook = payload.get("hook_event_name")
    notification = payload.get("notification_type")
    event = EVENT_MAP.get(hook)
    if hook == "Notification" and notification == "permission_prompt":
        event = "approvalRequired"
    elif hook == "Notification" and notification == "idle_prompt":
        event = "waitingForInput"
    if event is None or pid <= 1:
        return None
    return {"agent": "claudeCode", "processID": pid, "event": event, "observedAt": observed_at}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--bundle-identifier", required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    pid = claude_ancestor()
    observed_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    record = sanitized_record(payload, pid, observed_at)
    if record is None:
        return 0
    try:
        target_dir = evidence_directory(args.bundle_identifier)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = target_dir / f"claudeCode-{pid}.json"
    fd, temporary = tempfile.mkstemp(prefix=".claude-", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

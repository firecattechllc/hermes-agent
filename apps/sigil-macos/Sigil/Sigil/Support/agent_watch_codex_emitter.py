#!/usr/bin/env python3
"""Codex command-hook adapter that persists only lifecycle metadata."""

from __future__ import annotations

import ctypes
import ctypes.util
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile

EVENT_MAP = {
    "SessionStart": "sessionStarted",
    "UserPromptSubmit": "beganWork",
    "PreToolUse": "beganWork",
    "PostToolUse": "beganWork",
    "PermissionRequest": "approvalRequired",
    "Stop": "completed",
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
    return ctypes.cast(ctypes.byref(info), ctypes.POINTER(ctypes.c_uint32))[6]


def process_path(pid: int) -> str:
    libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    buffer = ctypes.create_string_buffer(4096)
    if libproc.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        return ""
    return buffer.value.decode("utf-8", errors="ignore")


def codex_ancestor() -> int:
    pid = os.getppid()
    for _ in range(12):
        path = process_path(pid).lower()
        if pathlib.Path(path).name in {"codex", "codex-cli"} or "/codex.app/" in path:
            return pid
        pid = parent_pid(pid)
        if pid <= 1:
            break
    return 0


def sanitized_record(payload: dict, pid: int, observed_at: str) -> dict | None:
    event = EVENT_MAP.get(payload.get("hook_event_name"))
    if event is None or pid <= 1:
        return None
    return {"agent": "codex", "processID": pid, "event": event, "observedAt": observed_at}


def sigil_is_running() -> bool:
    return subprocess.run(
        ["/usr/bin/pgrep", "-x", "SigilDev"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not sigil_is_running():
        return 0
    pid = codex_ancestor()
    observed_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    record = sanitized_record(payload, pid, observed_at)
    if record is None:
        return 0
    target_dir = pathlib.Path.home() / (
        "Library/Containers/com.firecattechnology.Sigil.dev/Data/Library/"
        "Application Support/SigilDev/AgentWatch/Events"
    )
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = target_dir / f"codex-{pid}.json"
    fd, temporary = tempfile.mkstemp(prefix=".codex-", dir=target_dir)
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

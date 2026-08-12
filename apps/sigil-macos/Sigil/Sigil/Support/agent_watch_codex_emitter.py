#!/usr/bin/env python3
"""Codex command-hook adapter that persists only lifecycle metadata."""

from __future__ import annotations

import ctypes
import ctypes.util
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile

from agent_watch_paths import evidence_directory

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
    """Documented proc_bsdinfo layout from macOS libproc.h."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def parent_pid(pid: int, *, library=None) -> int:
    """Return only the documented parent PID field from macOS libproc."""
    libproc = library or ctypes.CDLL(
        ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
    )
    info = ProcBSDInfo()
    size = libproc.proc_pidinfo(
        pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)
    )
    return int(info.pbi_ppid) if size == ctypes.sizeof(info) else 0


def process_path(pid: int) -> str:
    libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    buffer = ctypes.create_string_buffer(4096)
    if libproc.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        return ""
    return buffer.value.decode("utf-8", errors="ignore")


def codex_ancestor(
    *,
    start_pid: int | None = None,
    parent_resolver=parent_pid,
    path_resolver=process_path,
) -> int:
    pid = start_pid if start_pid is not None else os.getppid()
    for _ in range(12):
        path = path_resolver(pid).lower()
        if pathlib.Path(path).name in {"codex", "codex-cli"} or "/codex.app/" in path:
            return pid
        pid = parent_resolver(pid)
        if pid <= 1:
            break
    return 0


def sanitized_record(payload: dict, pid: int, observed_at: str) -> dict | None:
    event = EVENT_MAP.get(payload.get("hook_event_name"))
    if event is None or pid <= 1:
        return None
    return {"agent": "codex", "processID": pid, "event": event, "observedAt": observed_at}


def transcript_owner_pid(payload: dict, *, runner=subprocess.run) -> int:
    """Resolve the sole Codex process holding the hook's exact transcript."""
    value = payload.get("transcript_path")
    if not isinstance(value, str) or not value:
        return 0
    transcript = pathlib.Path(value)
    if not transcript.is_file():
        return 0
    try:
        result = runner(
            ["/usr/sbin/lsof", "-t", "--", str(transcript)],
            check=False, capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    owners = set()
    for line in result.stdout.splitlines():
        try:
            pid = int(line)
        except ValueError:
            continue
        path = process_path(pid).lower()
        if pathlib.Path(path).name in {"codex", "codex-cli"} or "/codex.app/" in path:
            owners.add(pid)
    return owners.pop() if len(owners) == 1 else 0


def process_id(payload: dict) -> int:
    """Resolve a supplied test PID, exact transcript owner, or Codex ancestor."""
    supplied = payload.get("process_id")
    if isinstance(supplied, int) and not isinstance(supplied, bool) and supplied > 1:
        return supplied
    return transcript_owner_pid(payload) or codex_ancestor()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--bundle-identifier", required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    pid = process_id(payload)
    observed_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    record = sanitized_record(payload, pid, observed_at)
    if record is None:
        return 0
    try:
        target_dir = evidence_directory(args.bundle_identifier)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
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

"""Parses ``prime-agent status``/``doctor`` JSON output into a governed,
closed status vocabulary. Never trusts the raw shape blindly -- unexpected
or missing fields collapse to the most conservative status rather than
raising, since a status check must never itself become a new failure mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Sequence, Tuple


class DaemonHealth(str, Enum):
    RUNNING = "running"
    NOT_RUNNING = "not_running"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DaemonSnapshot:
    health: DaemonHealth
    pid: Optional[int]
    version: Optional[str]
    build_id: Optional[str]
    uptime_seconds: Optional[int]
    session_count: Optional[int]
    socket_path: Optional[str]


def most_conservative(snapshots: Sequence[DaemonHealth]) -> DaemonHealth:
    """Collapse multiple health readings to the least-favorable one, same
    convention as ``hermes_docs_worker.status``'s status enum collapse."""
    order = (DaemonHealth.UNKNOWN, DaemonHealth.NOT_RUNNING, DaemonHealth.RUNNING)
    if not snapshots:
        return DaemonHealth.UNKNOWN
    return min(snapshots, key=order.index)


def parse_status(payload: Any) -> Tuple[DaemonSnapshot, ...]:
    """``payload`` is the parsed JSON from ``prime-agent status --json`` (or
    ``doctor --json``, same shape): a list of daemon entries, empty when no
    daemon is running."""
    if not isinstance(payload, list):
        return (
            DaemonSnapshot(
                health=DaemonHealth.UNKNOWN,
                pid=None,
                version=None,
                build_id=None,
                uptime_seconds=None,
                session_count=None,
                socket_path=None,
            ),
        )
    if not payload:
        return ()

    snapshots = []
    for entry in payload:
        if not isinstance(entry, dict):
            snapshots.append(
                DaemonSnapshot(
                    health=DaemonHealth.UNKNOWN,
                    pid=None,
                    version=None,
                    build_id=None,
                    uptime_seconds=None,
                    session_count=None,
                    socket_path=None,
                )
            )
            continue
        pid = entry.get("pid")
        health = (
            DaemonHealth.RUNNING
            if isinstance(pid, int) and pid > 0
            else DaemonHealth.UNKNOWN
        )
        snapshots.append(
            DaemonSnapshot(
                health=health,
                pid=pid if isinstance(pid, int) else None,
                version=entry.get("version")
                if isinstance(entry.get("version"), str)
                else None,
                build_id=entry.get("buildId")
                if isinstance(entry.get("buildId"), str)
                else None,
                uptime_seconds=(
                    entry.get("uptimeSeconds")
                    if isinstance(entry.get("uptimeSeconds"), int)
                    else None
                ),
                session_count=(
                    entry.get("sessionCount")
                    if isinstance(entry.get("sessionCount"), int)
                    else None
                ),
                socket_path=(
                    entry.get("socketPath")
                    if isinstance(entry.get("socketPath"), str)
                    else None
                ),
            )
        )
    return tuple(snapshots)


def daemon_is_running(payload: Any) -> bool:
    snapshots = parse_status(payload)
    return any(s.health == DaemonHealth.RUNNING for s in snapshots)

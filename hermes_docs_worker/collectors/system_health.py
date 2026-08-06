"""Titan system health: disk, memory, temperature, uptime.

Read-only ``/proc`` and ``shutil.disk_usage`` reads only -- no subprocess,
no writes. Every value is a live, currently-observed measurement, so
statuses here legitimately use ``Verified``/``Degraded``/``Blocked``
(never ``Deployed``, which is reserved for a running service, not a
resource reading).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Tuple

from hermes_docs_worker.evidence import EvidenceFact, make_fact
from hermes_docs_worker.status import StatusValue

SOURCE = "system_health"

_DISK_DEGRADED_PCT = 85.0
_DISK_BLOCKED_PCT = 95.0
_MEM_DEGRADED_PCT = 85.0
_MEM_BLOCKED_PCT = 95.0
_TEMP_DEGRADED_C = 70.0
_TEMP_BLOCKED_C = 80.0


def _fact(label: str, status: StatusValue, detail: str, now: int) -> EvidenceFact:
    return make_fact(
        category="system_health", label=label, status=status, detail=detail,
        source=SOURCE, collected_at=now,
    )


def _collect_disk(path: Path, now: int) -> EvidenceFact:
    try:
        usage = shutil.disk_usage(path)
    except OSError as error:
        return _fact("disk", StatusValue.UNKNOWN, f"disk usage unavailable: {error}", now)
    used_pct = (usage.used / usage.total) * 100 if usage.total else 0.0
    detail = f"{used_pct:.1f}% used ({usage.free // (1024 ** 2)}MiB free)"
    if used_pct >= _DISK_BLOCKED_PCT:
        status = StatusValue.BLOCKED
    elif used_pct >= _DISK_DEGRADED_PCT:
        status = StatusValue.DEGRADED
    else:
        status = StatusValue.VERIFIED
    return _fact("disk", status, detail, now)


def _collect_memory(now: int) -> EvidenceFact:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return _fact("memory", StatusValue.UNKNOWN, "/proc/meminfo not available", now)
    try:
        values: dict[str, int] = {}
        for line in meminfo_path.read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            rest = rest.strip().split()
            if rest and rest[0].isdigit():
                values[key.strip()] = int(rest[0])
    except OSError as error:
        return _fact("memory", StatusValue.UNKNOWN, f"memory read failed: {error}", now)

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total:
        return _fact("memory", StatusValue.UNKNOWN, "MemTotal not reported", now)
    used_pct = 100.0 * (1 - (available or 0) / total)
    detail = f"{used_pct:.1f}% used ({(available or 0) // 1024}MiB available)"
    if used_pct >= _MEM_BLOCKED_PCT:
        status = StatusValue.BLOCKED
    elif used_pct >= _MEM_DEGRADED_PCT:
        status = StatusValue.DEGRADED
    else:
        status = StatusValue.VERIFIED
    return _fact("memory", status, detail, now)


def _collect_temperature(now: int) -> EvidenceFact:
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if not thermal_path.exists():
        return _fact("temperature", StatusValue.UNKNOWN, "no thermal zone available", now)
    try:
        raw = thermal_path.read_text(encoding="utf-8").strip()
        celsius = int(raw) / 1000.0
    except (OSError, ValueError) as error:
        return _fact("temperature", StatusValue.UNKNOWN, f"temperature read failed: {error}", now)
    detail = f"{celsius:.1f}C"
    if celsius >= _TEMP_BLOCKED_C:
        status = StatusValue.BLOCKED
    elif celsius >= _TEMP_DEGRADED_C:
        status = StatusValue.DEGRADED
    else:
        status = StatusValue.VERIFIED
    return _fact("temperature", status, detail, now)


def _collect_uptime(now: int) -> EvidenceFact:
    uptime_path = Path("/proc/uptime")
    if not uptime_path.exists():
        return _fact("uptime", StatusValue.UNKNOWN, "/proc/uptime not available", now)
    try:
        seconds = float(uptime_path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError) as error:
        return _fact("uptime", StatusValue.UNKNOWN, f"uptime read failed: {error}", now)
    hours = seconds / 3600.0
    return _fact("uptime", StatusValue.VERIFIED, f"{hours:.1f}h", now)


def collect(*, disk_path: Path = Path("/"), now: int | None = None) -> Tuple[EvidenceFact, ...]:
    observed_at = now if now is not None else int(time.time())
    return (
        _collect_disk(disk_path, observed_at),
        _collect_memory(observed_at),
        _collect_temperature(observed_at),
        _collect_uptime(observed_at),
    )

"""systemd service state, for an explicit allowlist of units only.

This collector never queries a unit name it wasn't handed in
``config.systemd_allowlist`` -- there is no discovery mode, no
``systemctl list-units`` call, and no way for a unit name to arrive from
vault content or model output. Every subprocess call goes through
:mod:`hermes_docs_worker.proc`, which independently enforces
argument-separated, hard-coded argv.
"""

from __future__ import annotations

import time
from typing import Tuple

from hermes_docs_worker.config import DocsWorkerConfig
from hermes_docs_worker.evidence import EvidenceFact, make_fact
from hermes_docs_worker.proc import run_argv
from hermes_docs_worker.status import StatusValue

SOURCE = "systemd_state"


def _status_for(load_state: str, active_state: str, sub_state: str) -> StatusValue:
    if load_state == "not-found":
        return StatusValue.UNKNOWN
    if active_state == "failed":
        return StatusValue.BLOCKED
    if active_state == "active" and sub_state == "running":
        return StatusValue.DEPLOYED
    if active_state == "active":
        return StatusValue.DEGRADED
    if active_state == "inactive":
        return StatusValue.CONFIGURED
    return StatusValue.UNKNOWN


def collect_unit(config: DocsWorkerConfig, unit: str, *, now: int) -> EvidenceFact:
    if unit not in config.systemd_allowlist:
        raise ValueError(f"{unit!r} is not in the configured systemd allowlist")

    result = run_argv(
        (
            "systemctl", "show", unit, "--no-page",
            "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
        ),
        timeout=config.max_subprocess_seconds,
    )
    if result.returncode != 0:
        return make_fact(
            category="systemd", label=unit, status=StatusValue.UNKNOWN,
            detail="systemctl show failed", source=SOURCE, collected_at=now,
        )

    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key:
            parsed[key] = value

    load_state = parsed.get("LoadState", "unknown")
    active_state = parsed.get("ActiveState", "unknown")
    sub_state = parsed.get("SubState", "unknown")
    status = _status_for(load_state, active_state, sub_state)
    detail = f"load={load_state} active={active_state} sub={sub_state}"
    return make_fact(
        category="systemd", label=unit, status=status, detail=detail, source=SOURCE,
        collected_at=now,
    )


def collect(config: DocsWorkerConfig, *, now: int | None = None) -> Tuple[EvidenceFact, ...]:
    observed_at = now if now is not None else int(time.time())
    return tuple(collect_unit(config, unit, now=observed_at) for unit in config.systemd_allowlist)

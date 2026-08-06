"""Hermes service/runtime status on Titan.

Two independent, read-only signals, never conflated: whether the
configured Hermes source checkout exists on disk (a static fact --
``Configured`` at best, never ``Deployed``), and whether the fleet's
Hermes-related systemd units (from ``config.systemd_allowlist``) are
actually running (a live signal, delegated to
:mod:`hermes_docs_worker.collectors.systemd_state` rather than
re-implemented here).
"""

from __future__ import annotations

import time
from typing import Tuple

from hermes_docs_worker.collectors import systemd_state
from hermes_docs_worker.config import DocsWorkerConfig
from hermes_docs_worker.evidence import EvidenceFact, make_fact
from hermes_docs_worker.status import StatusValue

SOURCE = "hermes_runtime"

# Units in the allowlist that represent Hermes' own runtime, as opposed to
# a dependency like Ollama. Only units actually present in
# config.systemd_allowlist are ever queried.
_HERMES_UNIT_MARKERS = ("hermes",)


def collect(config: DocsWorkerConfig, *, now: int | None = None) -> Tuple[EvidenceFact, ...]:
    observed_at = now if now is not None else int(time.time())
    facts: list[EvidenceFact] = []

    source_dir = config.hermes_source_dir
    if source_dir.exists() and (source_dir / ".git").exists():
        facts.append(
            make_fact(
                category="hermes_runtime", label="source_checkout",
                status=StatusValue.CONFIGURED,
                detail="Hermes source checkout present at configured deployment path",
                source=SOURCE, collected_at=observed_at,
            )
        )
    else:
        facts.append(
            make_fact(
                category="hermes_runtime", label="source_checkout", status=StatusValue.BLOCKED,
                detail="configured Hermes source directory is missing or not a git checkout",
                source=SOURCE, collected_at=observed_at,
            )
        )

    hermes_units = tuple(
        unit for unit in config.systemd_allowlist
        if any(marker in unit for marker in _HERMES_UNIT_MARKERS)
    )
    for unit in hermes_units:
        fact = systemd_state.collect_unit(config, unit, now=observed_at)
        facts.append(
            make_fact(
                category="hermes_runtime", label=f"service:{unit}", status=fact.status,
                detail=fact.detail, source=SOURCE, collected_at=observed_at,
            )
        )

    return tuple(facts)

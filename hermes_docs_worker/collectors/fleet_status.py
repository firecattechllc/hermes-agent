"""Prime/Mac/Hydra Live status, read only through the existing governed
fleet registry.

This collector never makes a network call of its own and never contacts
the Mac directly. It reads
:class:`hermes_cli.prime.fleet_registry.FleetRegistryStore` -- the same
durable, on-disk registry Prime's own heartbeat/admission pipeline
maintains -- which is exactly the "existing governed fleet interface" the
governance contract requires. If the registry is empty, missing, or
raises, every configured fleet node comes back ``Unknown`` rather than the
collector inventing a status.
"""

from __future__ import annotations

import time
from typing import Tuple

from hermes_docs_worker.config import DocsWorkerConfig
from hermes_docs_worker.evidence import EvidenceFact, make_fact
from hermes_docs_worker.status import StatusValue

SOURCE = "fleet_status"

_CONNECTION_STATE_STATUS = {
    "connected": StatusValue.DEPLOYED,
    "degraded": StatusValue.DEGRADED,
    "disconnected": StatusValue.DEGRADED,
    "unknown": StatusValue.UNKNOWN,
}


def collect(config: DocsWorkerConfig, *, now: int | None = None) -> Tuple[EvidenceFact, ...]:
    observed_at = now if now is not None else int(time.time())

    try:
        from hermes_cli.prime.fleet_registry import FleetRegistryStore
    except ImportError as error:
        return tuple(
            make_fact(
                category="fleet_status", label=key, status=StatusValue.UNKNOWN,
                detail=f"fleet registry module unavailable: {error}", source=SOURCE,
                collected_at=observed_at,
            )
            for key in config.fleet_node_keys
        )

    try:
        store = FleetRegistryStore()
        records = {record.natural_key: record for record in store.all()}
    except Exception as error:  # noqa: BLE001 - registry failure must never crash a run
        return tuple(
            make_fact(
                category="fleet_status", label=key, status=StatusValue.UNKNOWN,
                detail=f"fleet registry unreadable: {error}", source=SOURCE,
                collected_at=observed_at,
            )
            for key in config.fleet_node_keys
        )

    facts = []
    for key in config.fleet_node_keys:
        record = records.get(key)
        if record is None:
            facts.append(
                make_fact(
                    category="fleet_status", label=key, status=StatusValue.UNKNOWN,
                    detail="no registry entry for this node", source=SOURCE,
                    collected_at=observed_at,
                )
            )
            continue
        if record.revoked:
            facts.append(
                make_fact(
                    category="fleet_status", label=key, status=StatusValue.BLOCKED,
                    detail="fleet registration revoked", source=SOURCE,
                    collected_at=observed_at,
                )
            )
            continue
        connection_state = str(getattr(record.connection_state, "value", record.connection_state))
        status = _CONNECTION_STATE_STATUS.get(connection_state.lower(), StatusValue.UNKNOWN)
        detail = f"connection_state={connection_state} last_seen_at={record.last_seen_at}"
        facts.append(
            make_fact(
                category="fleet_status", label=key, status=status, detail=detail,
                source=SOURCE, collected_at=observed_at,
            )
        )
    return tuple(facts)
